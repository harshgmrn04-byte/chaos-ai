"""Graph-grounded AI planning with deterministic completion and policy trace."""

import json
import logging
from pathlib import Path

from chaosai.catalogue import CATALOGUE, applicable, definition
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.llm import LLM, LLMUnavailable, complete_json
from chaosai.models import (
    AbortRule,
    Baseline,
    Clamp,
    ExperimentProposal,
    ExperimentSpec,
    Graph,
    Hypothesis,
    Plan,
    PlannerOutput,
    PolicyDecision,
)
from chaosai.planner_facts import SOURCE_REVISION, grounded_reason, source_facts
from chaosai.runs import write_json
from chaosai.safety import policy_gate

logger = logging.getLogger(__name__)
PROMPT_PATH = Path(__file__).parent / "prompts" / "planner.md"


def node_priority(target: str, graph: Graph) -> tuple[int, int, int, int]:
    """Selection score (higher is more useful), including unknown dependency edges."""
    incoming = [edge for edge in graph.edges if edge.target == target]
    unknown = sum(edge.criticality == "unknown" for edge in incoming)
    impact = sum(target in flow.path for flow in graph.flows)
    kind = next((node.kind for node in graph.nodes if node.id == target), "tool")
    weight = {"database": 4, "broker": 3, "cache": 3, "app": 2, "tool": 1}.get(kind, 0)
    return len(incoming) * 3 + unknown * 4 + weight + impact * 2, impact, len(incoming), unknown


def blast_radius(spec: ExperimentSpec, graph: Graph) -> tuple[int, int]:
    if spec.fault == "traffic_spike":
        flows = [f for f in graph.flows if spec.target == "all" or f.name == spec.target]
        return len(flows), sum(len(f.path) for f in flows)
    _, impact, fan_in, _ = node_priority(spec.target, graph)
    return impact, fan_in


def heuristic_candidates(graph: Graph, baseline: Baseline) -> list[ExperimentSpec]:
    """Prefer informative dependencies; round-robin faults ensure diversity."""
    choices: dict[str, list[ExperimentSpec]] = {}
    journeys = set(baseline.journeys) - {"all"}
    for entry in CATALOGUE:
        if not entry.plannable:
            continue
        targets = (
            sorted(journeys) + ["all"]
            if entry.target_kind == "journey"
            else [
                n.id
                for n in sorted(graph.nodes, key=lambda n: (-node_priority(n.id, graph)[0], n.id))
            ]
        )
        for target in targets:
            if not applicable(entry.fault, target, graph, journeys):
                continue
            _, impact, fan_in, unknown = node_priority(target, graph)
            reason = (
                f"Test overload in mapped journey {target} under bounded extra load."
                if entry.target_kind == "journey"
                else f"Test {entry.fault} on {target}: {fan_in} dependents, "
                f"{unknown} unknown edges, "
                f"{impact} mapped journeys; verify failure handling for this dependency."
            )
            choices.setdefault(entry.fault, []).append(
                ExperimentSpec(
                    id="pending",
                    fault=entry.fault,
                    target=target,
                    duration_s=entry.default_duration_s,
                    intensity=entry.default_intensity,
                    hypothesis=Hypothesis(
                        journey="all",
                        max_error_rate=baseline.thresholds.hypothesis.max_error_rate,
                        max_p95_multiple=baseline.thresholds.hypothesis.p95_multiple,
                        statement="Journeys remain within baseline-derived hypothesis limits.",
                    ),
                    abort=AbortRule(
                        max_error_rate=baseline.thresholds.abort.max_error_rate,
                        max_p95_multiple=baseline.thresholds.abort.p95_multiple,
                    ),
                    reason=reason,
                    rank=1,
                )
            )
    result: list[ExperimentSpec] = []
    # Three implemented types come first; extended catalogue entries are planning only.
    while any(choices.values()):
        for entries in choices.values():
            if entries:
                result.append(entries.pop(0))
    return result


def validate_proposal(
    proposal: ExperimentProposal,
    graph: Graph,
    baseline: Baseline,
) -> ExperimentSpec | None:
    if not applicable(proposal.fault, proposal.target, graph, set(baseline.journeys) - {"all"}):
        return None
    if proposal.journey not in set(baseline.journeys) | {"all"} or proposal.rank < 1:
        return None
    if not proposal.reason.strip() or not proposal.statement.strip():
        return None
    return ExperimentSpec(
        id="pending",
        fault=proposal.fault,
        target=proposal.target,
        duration_s=proposal.duration_s,
        intensity=proposal.intensity,
        hypothesis=Hypothesis(
            journey=proposal.journey,
            max_error_rate=proposal.max_error_rate,
            max_p95_multiple=proposal.max_p95_multiple,
            statement=proposal.statement,
        ),
        abort=AbortRule(
            max_error_rate=proposal.abort_max_error_rate,
            max_p95_multiple=proposal.abort_max_p95_multiple,
        ),
        reason=proposal.reason,
        rank=proposal.rank,
    )


def planner_decision(
    spec: ExperimentSpec,
    source: str,
    base_workers: int = 10,
    *,
    graph: Graph | None = None,
) -> PolicyDecision:
    """S2 clamps plus bounded hypothesis values, preserving every requested value."""
    decision = policy_gate(spec, base_workers=base_workers)
    decision.source = source
    for field, lo, hi in (("max_error_rate", 0, 1), ("max_p95_multiple", 1, 20)):
        requested = getattr(spec.hypothesis, field)
        final = max(lo, min(hi, requested))
        if final != requested:
            setattr(decision.final.hypothesis, field, final)
            decision.clamps.append(
                Clamp(
                    field=f"hypothesis.{field}",
                    requested=requested,
                    final=final,
                    reason=f"Planner hypothesis range [{lo}, {hi}].",
                )
            )
    entry = definition(spec.fault)
    if entry and entry.min_intensity is None and spec.intensity is not None:
        decision.final.intensity = None
        decision.clamps.append(
            Clamp(
                field="intensity",
                requested=spec.intensity,
                final=None,
                reason="This fault has no intensity parameter.",
            )
        )
    correction = grounded_reason(spec, graph) if graph is not None else None
    if correction is not None and spec.reason != correction[0]:
        decision.final.reason = correction[0]
        decision.clamps.append(
            Clamp(
                field="reason",
                requested=spec.reason,
                final=correction[0],
                reason=correction[1],
            )
        )
    return decision


def finalize_plan(
    proposals: list[ExperimentProposal],
    graph: Graph,
    baseline: Baseline,
    *,
    source: str,
    base_workers: int = 10,
    max_experiments: int = 8,
) -> tuple[Plan, list[PolicyDecision]]:
    maximum = max(0, min(8, max_experiments))
    chosen: list[tuple[ExperimentSpec, str]] = []
    seen: set[tuple[str, str]] = set()

    def accept(spec: ExperimentSpec, origin: str) -> bool:
        key = spec.fault, spec.target
        if key in seen or len(chosen) >= maximum:
            return False
        if not planner_decision(spec, origin, base_workers).allowed:
            return False
        seen.add(key)
        chosen.append((spec, origin))
        return True

    valid = [s for p in proposals if (s := validate_proposal(p, graph, baseline)) is not None]
    # Preserve the highest ranked AI proposals while reserving room for missing types.
    distinct_available = {
        s.fault
        for s in heuristic_candidates(graph, baseline)
        if planner_decision(s, "heuristic", base_workers).allowed
    }
    required = min(3, maximum, len(distinct_available))
    for spec in sorted(valid, key=lambda s: s.rank):
        types = {s.fault for s, _ in chosen}
        reserved = max(0, required - len(types | {spec.fault}))
        if len(chosen) < maximum - reserved:
            accept(spec, source)
    candidates = heuristic_candidates(graph, baseline)
    for spec in candidates:
        if spec.fault not in {s.fault for s, _ in chosen} and len({s.fault for s, _ in chosen}) < 3:
            accept(spec, "heuristic")
    for spec in candidates:
        accept(spec, "heuristic")
    chosen.sort(
        key=lambda pair: (
            *blast_radius(pair[0], graph),
            pair[0].rank,
            pair[0].target,
            pair[0].fault,
        )
    )
    decisions: list[PolicyDecision] = []
    for rank, (spec, origin) in enumerate(chosen, 1):
        # IDs and ranks are deterministic code, never LLM-provided artifact paths.
        spec.id = f"experiment-{rank:02d}"
        spec.rank = rank
        decisions.append(planner_decision(spec, origin, base_workers, graph=graph))
    if len({d.final.fault for d in decisions}) < 3:
        logger.warning("Graph/budget permits fewer than three distinct fault types.")
    return Plan(root=[d.final for d in decisions]), decisions


def plan_experiments(
    graph: Graph,
    baseline: Baseline,
    *,
    provider: str,
    cache_dir: Path,
    settings: Settings | None = None,
    simulation: bool = False,
) -> tuple[Plan, str]:
    if simulation:
        # Preserve the Phase 1 synthetic evidence IDs/order; no real provider can enter here.
        source = "mock"
        try:
            plan = complete_json(
                "planner",
                "Return fixture experiment specifications.",
                graph.model_dump_json() + "\n" + baseline.model_dump_json(),
                Plan,
                provider="mock" if provider == "mock" else "none",
                cache_dir=cache_dir,
            )
        except LLMUnavailable:
            plan, source = fixture("plan", Plan), "heuristic"
        decisions = [planner_decision(s, source) for s in plan.root]
    else:
        settings = settings or Settings(llm_provider=provider)
        session = LLM(settings.model_copy(update={"llm_provider": provider}), cache_dir=cache_dir)
        source = "heuristic"
        proposals: list[ExperimentProposal] = []
        try:
            payload = {
                "graph": graph.model_dump(mode="json"),
                "source_revision": SOURCE_REVISION if source_facts(graph) else None,
                "source_facts": [fact.model_dump() for fact in source_facts(graph)],
                "baseline": dict(
                    stable=baseline.stable,
                    journeys={k: v.model_dump() for k, v in baseline.journeys.items()},
                    thresholds=baseline.thresholds.model_dump(),
                ),
                "catalogue": [entry.model_dump() for entry in CATALOGUE],
                "constraints": dict(
                    max_experiments=8,
                    min_fault_types=3,
                    max_workers=150,
                    base_workers=settings.load_users,
                    one_fault_at_a_time=True,
                    human_approval=True,
                ),
            }
            output = session.complete_json(
                "planner",
                PROMPT_PATH.read_text(encoding="utf-8"),
                "UNTRUSTED DATA (JSON quoted as a string):\n" + json.dumps(json.dumps(payload)),
                PlannerOutput,
            )
            proposals, source = output.experiments, session.source
        except LLMUnavailable:
            logger.info("LLM planner unavailable; selecting deterministic hypotheses.")
        finally:
            session.close()
        plan, decisions = finalize_plan(
            proposals, graph, baseline, source=source, base_workers=settings.load_users
        )
        if not any(d.source != "heuristic" for d in decisions):
            source = "heuristic"
    plan = Plan(root=[d.final for d in decisions])
    write_json(cache_dir.parent / "plan.json", plan)
    write_json(cache_dir.parent / "decisions.json", decisions)
    return plan, source
