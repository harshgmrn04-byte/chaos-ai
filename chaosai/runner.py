"""Walking skeleton orchestration; execution returns fixtures only."""

from datetime import UTC, datetime
from pathlib import Path

from chaosai.analyzer import analyze_results
from chaosai.approval import Approver
from chaosai.baseline import measure_baseline
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.mapper import map_target, save_graph
from chaosai.models import (
    Approval,
    ExperimentResult,
    ExperimentSpec,
    PolicyDecision,
    RunMeta,
    Sample,
)
from chaosai.planner import plan_experiments
from chaosai.report import render_report
from chaosai.runs import append_jsonl, create_run, write_json
from chaosai.scoring import score_results


def fixture_decision(spec: ExperimentSpec) -> PolicyDecision:
    return PolicyDecision(
        experiment_id=spec.id,
        requested=spec,
        final=spec,
        clamps=[],
        allowed=True,
        deny_reason=None,
    )


def execute_experiment(decision: PolicyDecision, run_dir: Path) -> ExperimentResult:
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
    )
    write_json(run_dir / "run.json", meta)
    # Empty by design: no real faults or undo actions exist in Phase 1.
    write_json(run_dir / "journal.json", [])
    graph = map_target(settings, offline=offline)
    save_graph(graph, settings, run_dir)
    baseline = measure_baseline(graph)
    write_json(run_dir / "baseline.json", baseline)
    plan, meta.planner_source = plan_experiments(
        graph, baseline, provider=settings.llm_provider, cache_dir=run_dir / "llm"
    )
    write_json(run_dir / "plan.json", plan)
    decisions = [fixture_decision(spec) for spec in plan.root]
    write_json(run_dir / "decisions.json", decisions)
    results: list[ExperimentResult] = []
    if not dry_run:
        for decision in decisions:
            choice = approver.decide(decision)
            meta.approvals.append(Approval(experiment_id=decision.experiment_id, decision=choice))
            write_json(run_dir / "run.json", meta)
            if choice == "quit":
                break
            if choice != "skip":
                results.append(execute_experiment(decision, run_dir))
    findings, meta.analyzer_source = analyze_results(
        results, provider=settings.llm_provider, cache_dir=run_dir / "llm"
    )
    write_json(run_dir / "findings.json", findings)
    scores, summary = score_results(results)
    write_json(run_dir / "scores.json", scores)
    write_json(run_dir / "summary.json", summary)
    write_json(run_dir / "run.json", meta)
    render_report(run_dir)
    return run_dir
