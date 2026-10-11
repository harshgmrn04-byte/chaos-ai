"""Evidence-limited findings, deterministic fallback and transport-free cached replay."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from chaosai.config import Settings
from chaosai.llm import LLM, LLMUnavailable, response_key
from chaosai.models import (
    AnalysisTrace,
    Baseline,
    ExperimentResult,
    Finding,
    Graph,
    RunMeta,
    Score,
    SourceFact,
    Summary,
)
from chaosai.planner_facts import source_facts
from chaosai.runs import read_json, write_json
from chaosai.scoring import ranked_summary, score_results, severity

PROMPT_PATH = Path(__file__).parent / "prompts" / "analyzer.md"
M = TypeVar("M", bound=BaseModel)


def evidence_choices(
    result: ExperimentResult, baseline: Baseline, score: Score | None
) -> list[str]:
    evidence = [f"Verdict: {result.verdict}; skipped={result.skipped}."]
    for name, stats in sorted(result.during.items()):
        original = baseline.journeys.get(name)
        if original:
            evidence.append(
                f"During {name}: n={stats.n}, errors={stats.error_rate:.6f}, "
                f"p95={stats.p95_ms:.3f}ms; baseline n={original.n}, "
                f"errors={original.error_rate:.6f}, p95={original.p95_ms:.3f}ms."
            )
    evidence.append(
        f"Recovered={result.recovered}; recovery_time_s={result.recovery_time_s}; "
        f"self_healed={result.self_healed}; rollback_ok={result.rollback_ok}; "
        f"cleanup_ok={result.cleanup_ok}."
    )
    if score:
        evidence.append(f"Deterministic score: {score.score}/100.")
    if result.skip_reason:
        evidence.append(f"Skip reason: {result.skip_reason}")
    if result.abort_reason:
        evidence.append(f"Abort reason: {result.abort_reason}")
    return evidence


def evidence_packet(
    result: ExperimentResult,
    baseline: Baseline,
    score: Score | None,
    graph: Graph | None,
    *,
    data_source: str,
    facts: list[SourceFact] | None = None,
) -> dict[str, object]:
    # Total <=3 excerpts across all containers; never raw samples/HTTP bodies/credentials.
    logs = [
        {"container": name, "excerpt": excerpt[:1000]}
        for name, excerpts in sorted(result.log_excerpts.items())
        for excerpt in excerpts[:3]
    ][:3]
    grounded = facts if facts is not None else source_facts(graph) if graph else []
    choices = evidence_choices(result, baseline, score) + [
        f"Source observation: {f.statement} Evidence: {', '.join(f.evidence)}" for f in grounded
    ]
    return {
        "data_source": data_source,
        "experiment_id": result.spec.id,
        "spec": result.spec.model_dump(mode="json"),
        "verdict": result.verdict,
        "during": {n: s.model_dump() for n, s in result.during.items()},
        "after": {n: s.model_dump() for n, s in result.after.items()},
        "baseline": {n: s.model_dump() for n, s in baseline.journeys.items()},
        "recovered": result.recovered,
        "recovery_time_s": result.recovery_time_s,
        "self_healed": result.self_healed,
        "container_events": result.container_events,
        "skipped": result.skipped,
        "skip_reason": result.skip_reason,
        "evidence_limits": result.evidence_limits,
        "logs_UNTRUSTED_DATA": logs,
        "score": score.model_dump() if score else None,
        "severity": severity(score, result) if score else "low",
        "evidence_choices": choices,
        "source_facts": [f.model_dump() for f in grounded],
    }


def fallback_finding(result: ExperimentResult, baseline: Baseline, score: Score | None) -> Finding:
    fault = result.spec.fault
    unmeasured = result.skipped or score is None
    title = (
        "No injected-fault assessment available"
        if unmeasured
        else "Hypothesis held within the observed window"
        if result.verdict == "confirmed"
        else "Fault observation remains inconclusive"
        if result.verdict == "inconclusive"
        else "Watchdog or operator ended the experiment"
        if result.aborted
        else "Observed fault violated the journey hypothesis"
    )
    cause = "Root cause is unproven; the recorded result describes an observation."
    fixes = {
        "kill": "Review Compose restart policy and healthcheck; test bounded restart readiness.",
        "stop": "Add bounded dependency deadlines and retry with backoff; test reconnect recovery.",
        "pause": "Add request deadlines and a circuit breaker for stalled dependencies.",
        "cpu_limit": "Measure CPU saturation; tune worker and database connection pool limits.",
        "traffic_spike": "Measure capacity; bound admission, queues and database pool concurrency.",
        "netem_delay": "Bound dependency timeout/retry budgets and test delayed responses.",
    }
    fix = fixes[fault]
    if unmeasured:
        fix = "Resolve the recorded readiness/measurement issue, then repeat with a clean baseline."
    elif fault == "kill" and result.self_healed is False:
        cause += (
            " Automatic self-heal was not observed; a missing restart policy is only a hypothesis."
        )
    elif result.spec.target == "redis" and result.impacted_journeys:
        fix = "Add bounded cache deadlines and a safe database fallback for unavailable Redis."
    elif result.spec.target == "rabbitmq" and result.impacted_journeys:
        fix = "Use a transactional outbox and bounded publisher retries; verify eventual delivery."
    if not result.recovered and not unmeasured:
        cause += " HTTP recovery was not established, regardless of container rollback."
    return Finding(
        experiment_id=result.spec.id,
        summary=title,
        root_cause=cause,
        severity=severity(score, result) if score else "low",
        evidence=evidence_choices(result, baseline, score),
        fixes=[fix],
    )


class AnalysisClient:
    """Replay reads exact validated cache keys; it never instantiates a transport."""

    def __init__(
        self,
        settings: Settings,
        cache_dir: Path,
        *,
        cached_only: bool = False,
        recorded_source: str = "heuristic",
        recorded_sources: dict[str, str] | None = None,
    ) -> None:
        self.cache_dir, self.cached_only = cache_dir, cached_only
        self.recorded_source = recorded_source
        self.recorded_sources = recorded_sources or {}
        self.session = None if cached_only else LLM(settings, cache_dir=cache_dir)
        self.sources: list[str] = []
        self.trace: list[AnalysisTrace] = []

    def complete(self, task: str, user: str, model: type[M]) -> tuple[M, str]:
        try:
            system = PROMPT_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise LLMUnavailable(
                "Analysis prompt unavailable; deterministic fallback selected."
            ) from exc
        if self.cached_only:
            try:
                result = read_json(
                    self.cache_dir / f"{task}-{response_key(task, system, user)}.json", model
                )
                identity = (
                    "summary" if task == "analyzer-summary" else json.loads(user)["experiment_id"]
                )
                source = self.recorded_sources.get(identity, self.recorded_source)
            except (OSError, ValueError) as exc:
                raise LLMUnavailable("Matching validated analysis cache unavailable.") from exc
        else:
            result = self.session.complete_json(task, system, user, model)
            source = self.session.source
        return result, source

    def record(self, experiment_id: str, source: str, reason: str | None = None) -> None:
        self.sources.append(source)
        self.trace.append(
            AnalysisTrace(
                experiment_id=experiment_id,
                source=source,
                fallback_reason=reason,
            )
        )

    def close(self) -> None:
        if self.session:
            self.session.close()


def analyze_results(
    results: list[ExperimentResult],
    *,
    baseline: Baseline,
    scores: list[Score],
    client: AnalysisClient,
    graph: Graph | None = None,
    data_source: str = "fixture",
    facts: list[SourceFact] | None = None,
) -> list[Finding]:
    by_score = {s.experiment_id: s for s in scores}
    findings = []
    for result in sorted(results, key=lambda r: r.spec.rank):
        score = by_score.get(result.spec.id)
        finding = fallback_finding(result, baseline, score)
        packet = evidence_packet(
            result, baseline, score, graph, data_source=data_source, facts=facts
        )
        source, reason = "heuristic", None
        # Skips/absent measurements cannot become invented model findings.
        if score is not None:
            try:
                proposal, source = client.complete(
                    "analyzer", json.dumps(packet, sort_keys=True), Finding
                )
                if (
                    proposal.experiment_id != result.spec.id
                    or not proposal.evidence
                    or any(e not in packet["evidence_choices"] for e in proposal.evidence)
                    or not proposal.fixes
                    or any(not fix.strip() for fix in proposal.fixes)
                    or not proposal.summary.strip()
                    or not proposal.root_cause.strip()
                ):
                    raise LLMUnavailable(
                        "Analysis has invalid identity, evidence or empty explanation."
                    )
                finding = proposal.model_copy(update={"severity": severity(score, result)})
            except LLMUnavailable as exc:
                source, reason = "heuristic", str(exc)
        client.record(result.spec.id, source, reason)
        findings.append(finding)
    return findings


def summarize_results(
    results: list[ExperimentResult],
    scores: list[Score],
    findings: list[Finding],
    summary: Summary,
    client: AnalysisClient,
    *,
    data_source: str,
) -> Summary:
    canonical = ranked_summary(results, scores, findings, summary)
    if not scores:
        return canonical
    packet = {
        "data_source": data_source,
        "findings": [f.model_dump() for f in findings],
        "scores": [s.model_dump() for s in scores],
        "summary": canonical.model_dump(),
    }
    source, reason = "heuristic", None
    try:
        proposed, source = client.complete(
            "analyzer-summary", json.dumps(packet, sort_keys=True), Summary
        )
        if not proposed.narrative.strip():
            raise LLMUnavailable("Empty analysis summary.")
        # Only narrative is explanatory. All numbers, rankings and fixes remain canonical.
        canonical.narrative = proposed.narrative
    except LLMUnavailable as exc:
        source, reason = "heuristic", str(exc)
    client.record("summary", source, reason)
    return canonical


def analyze_run(
    run_dir: Path,
    settings: Settings,
    *,
    cached_only: bool = False,
) -> None:
    meta = read_json(run_dir / "run.json", RunMeta)
    # Legacy measured runs lack the newly introduced field; offline alone is not provenance.
    raw_meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if (
        "data_source" not in raw_meta
        and not meta.offline
        and meta.baseline_source in {"measured", "reused"}
    ):
        meta.data_source = "measured"
    baseline = read_json(run_dir / "baseline.json", Baseline)
    graph = read_json(run_dir / "graph.json", Graph)
    facts_path = run_dir / "source-facts.json"
    facts = (
        [SourceFact.model_validate(f) for f in read_json(facts_path, dict)["facts"]]
        if facts_path.exists()
        else None
    )
    results = [
        read_json(p, ExperimentResult) for p in (run_dir / "experiments").glob("*/result.json")
    ]
    scores, summary = score_results(results, baseline)
    traces = (
        read_json(run_dir / "analysis.json", list[AnalysisTrace])
        if cached_only and (run_dir / "analysis.json").exists()
        else []
    )
    client = AnalysisClient(
        settings,
        run_dir / "llm",
        cached_only=cached_only,
        recorded_source=meta.analyzer_source,
        recorded_sources={t.experiment_id: t.source for t in traces},
    )
    try:
        findings = analyze_results(
            results,
            baseline=baseline,
            scores=scores,
            client=client,
            graph=graph,
            data_source=meta.data_source,
            facts=facts,
        )
        summary = summarize_results(
            results,
            scores,
            findings,
            summary,
            client,
            data_source=meta.data_source,
        )
    finally:
        client.close()
    meta.analyzer_sources = list(dict.fromkeys(client.sources)) or ["heuristic"]
    meta.analyzer_source = next((s for s in meta.analyzer_sources if s != "heuristic"), "heuristic")
    write_json(run_dir / "findings.json", findings)
    write_json(run_dir / "scores.json", scores)
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "analysis.json", client.trace)
    write_json(run_dir / "run.json", meta)
