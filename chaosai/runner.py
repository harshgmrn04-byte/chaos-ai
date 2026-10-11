"""Measured single-experiment state machine and isolated offline fixture pipeline."""

from datetime import UTC, datetime
from pathlib import Path

from chaosai.analyzer import analyze_run
from chaosai.approval import Approver
from chaosai.baseline import measure_baseline
from chaosai.config import Settings
from chaosai.experiment import execute_experiment as execute_experiment
from chaosai.fixture_data import fixture
from chaosai.mapper import map_target, save_graph
from chaosai.models import (
    Approval,
    Baseline,
    ExperimentResult,
    ExperimentSpec,
    PolicyDecision,
    RunMeta,
    Sample,
)
from chaosai.planner import plan_experiments
from chaosai.report import render_report
from chaosai.runs import append_jsonl, create_run, read_json, write_json


def fixture_decision(spec: ExperimentSpec) -> PolicyDecision:
    return PolicyDecision(
        experiment_id=spec.id,
        requested=spec,
        final=spec,
        clamps=[],
        allowed=True,
        deny_reason=None,
    )


def execute_fixture(decision: PolicyDecision, run_dir: Path) -> ExperimentResult:
    """Persist simulated evidence without waiting, injecting, or touching the target."""
    result = next(
        r for r in fixture("results", list[ExperimentResult]) if r.spec.id == decision.experiment_id
    )
    result.spec = decision.final
    result.decision = decision
    result.samples_path = f"experiments/{result.spec.id}/samples.jsonl"
    append_jsonl(run_dir / result.samples_path, fixture(f"samples-{result.spec.id}", list[Sample]))
    write_json(run_dir / "experiments" / result.spec.id / "result.json", result)
    return result


def run_pipeline(
    settings: Settings,
    approver: Approver,
    *,
    dry_run: bool = False,
    offline: bool = False,
    max_experiments: int = 8,
    reuse_baseline: bool = False,
) -> Path:
    if not 1 <= max_experiments <= 8:
        raise ValueError("max-experiments must be between 1 and 8.")
    if not offline:
        from chaosai.orchestrator import run_automatic

        return run_automatic(
            settings,
            approver,
            dry_run=dry_run,
            max_experiments=max_experiments,
            reuse_baseline=reuse_baseline,
        )
    if not dry_run:
        approver.ensure_interactive()
    run_dir = create_run(settings.runs_dir)
    meta = RunMeta(
        run_id=run_dir.name,
        started_at=datetime.now(UTC),
        planner_source="heuristic",
        analyzer_source="heuristic",
        target_project=settings.compose_project,
        approvals=[],
        offline=offline,
    )
    write_json(run_dir / "run.json", meta)
    # Legacy empty journal is upgraded durably by the first real fault.
    write_json(run_dir / "journal.json", [])
    graph = map_target(settings, offline=True)
    save_graph(graph, settings, run_dir)
    baseline = measure_baseline(graph, settings, run_dir, offline=True)
    write_json(run_dir / "baseline.json", baseline)
    provider = "mock" if settings.llm_provider == "mock" else "none"
    plan, meta.planner_source = plan_experiments(
        graph,
        baseline,
        provider=provider,
        cache_dir=run_dir / "llm",
        settings=settings,
        simulation=True,
    )
    write_json(run_dir / "plan.json", plan)
    decisions = read_json(run_dir / "decisions.json", list[PolicyDecision])
    meta.planner_sources = list(dict.fromkeys(d.source for d in decisions))
    if not dry_run:
        for decision in decisions[:max_experiments]:
            choice = approver.decide(decision)
            meta.approvals.append(Approval(experiment_id=decision.experiment_id, decision=choice))
            write_json(run_dir / "run.json", meta)
            if choice == "quit":
                break
            if choice != "skip":
                execute_fixture(decision, run_dir)
    write_json(run_dir / "run.json", meta)
    analyze_run(run_dir, settings.model_copy(update={"llm_provider": provider}))
    render_report(run_dir)
    meta = read_json(run_dir / "run.json", RunMeta)
    meta.phase = "COMPLETE"
    meta.finished_at = datetime.now(UTC)
    write_json(run_dir / "run.json", meta)
    return run_dir


def demo_spec(
    baseline: Baseline,
    *,
    fault: str = "stop",
    target: str = "redis",
    duration: int = 30,
    abort_error_rate: float | None = None,
) -> ExperimentSpec:
    """Explicit single-experiment spec for inject and the Day-1 demo."""
    from chaosai.models import AbortRule, Hypothesis

    return ExperimentSpec(
        id=f"{target}-{fault}",
        fault=fault,
        target=target,
        duration_s=duration,
        intensity=None,
        rank=1,
        reason="Measured Day-1 resilience experiment.",
        hypothesis=Hypothesis(
            journey="all",
            max_error_rate=baseline.thresholds.hypothesis.max_error_rate,
            max_p95_multiple=baseline.thresholds.hypothesis.p95_multiple,
            statement="All journeys remain within the baseline-derived hypothesis limits.",
        ),
        abort=AbortRule(
            max_error_rate=(
                baseline.thresholds.abort.max_error_rate
                if abort_error_rate is None
                else abort_error_rate
            ),
            max_p95_multiple=baseline.thresholds.abort.p95_multiple,
        ),
    )
