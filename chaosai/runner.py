"""Measured single-experiment state machine and isolated offline fixture pipeline."""

from datetime import UTC, datetime
from pathlib import Path

from chaosai.analyzer import analyze_results
from chaosai.approval import Approver
from chaosai.baseline import measure_baseline
from chaosai.catalogue import definition
from chaosai.config import Settings
from chaosai.experiment import execute_experiment
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
    Summary,
)
from chaosai.planner import plan_experiments
from chaosai.report import render_report
from chaosai.runs import append_jsonl, create_run, read_json, write_json
from chaosai.safety import file_lock, replay_journals
from chaosai.scoring import score_results
from chaosai.target import get_docker_client


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
    settings: Settings, approver: Approver, *, dry_run: bool = False, offline: bool = False
) -> Path:
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
    if not offline and not dry_run:
        client = get_docker_client()
        try:
            with file_lock(settings.active_lock_path, blocking=False):
                replay_journals(client, settings)
        finally:
            client.close()
    graph = map_target(settings, offline=offline)
    save_graph(graph, settings, run_dir)
    latest = settings.safety_dir / "baseline.json"
    baseline = (
        read_json(latest, Baseline)
        if not offline and latest.exists()
        else measure_baseline(graph, settings, run_dir, offline=offline)
    )
    write_json(run_dir / "baseline.json", baseline)
    provider = (
        ("mock" if settings.llm_provider == "mock" else "none")
        if offline
        else settings.llm_provider
    )
    plan, meta.planner_source = plan_experiments(
        graph,
        baseline,
        provider=provider,
        cache_dir=run_dir / "llm",
        settings=settings,
        simulation=offline,
    )
    write_json(run_dir / "plan.json", plan)
    decisions = read_json(run_dir / "decisions.json", list[PolicyDecision])
    results: list[ExperimentResult] = []
    if not dry_run:
        for decision in decisions:
            if not offline:
                entry = definition(decision.final.fault)
                if entry is None or not entry.implemented or not decision.allowed:
                    continue
                result = execute_experiment(
                    decision,
                    run_dir,
                    settings=settings,
                    graph=graph,
                    baseline=baseline,
                    approver=approver,
                )
                results.append(result)
                meta.approvals.append(
                    Approval(
                        experiment_id=decision.experiment_id,
                        decision=result.approval_decision,
                    )
                )
                write_json(run_dir / "run.json", meta)
                break
            choice = approver.decide(decision)
            meta.approvals.append(Approval(experiment_id=decision.experiment_id, decision=choice))
            write_json(run_dir / "run.json", meta)
            if choice == "quit":
                break
            if choice != "skip":
                results.append(execute_fixture(decision, run_dir))
    if offline:
        findings, meta.analyzer_source = analyze_results(
            results, provider=provider, cache_dir=run_dir / "llm"
        )
    else:
        findings = []
    write_json(run_dir / "findings.json", findings)
    scores, summary = (
        score_results(results)
        if offline
        else (
            [],
            Summary(
                overall_score=0,
                band="not assessed",
                ranked_weaknesses=[],
                top_fixes=[],
                narrative=(
                    "Measured experiment evidence is available; analysis/scoring arrive in Phase 9."
                    if results
                    else "Plan only; no experiment executed. Analysis/scoring pending."
                ),
            ),
        )
    )
    write_json(run_dir / "scores.json", scores)
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "run.json", meta)
    render_report(run_dir)
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
