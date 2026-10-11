"""CLI for fixture runs and Phase 2 target operations."""

import errno
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

import docker
import httpx
import typer
from pydantic import ValidationError

from chaosai.analyzer import analyze_run
from chaosai.approval import ApprovalRequired, Approver
from chaosai.baseline import measure_baseline, run_load
from chaosai.catalogue import definition
from chaosai.config import PROJECT_ROOT, Settings, load_config
from chaosai.doctor import doctor_checks, llm_check, status_checks, target_status
from chaosai.faults import get_fault
from chaosai.faults.base import FaultContext, hold_fault
from chaosai.mapper import MapperError, map_target, mermaid, save_graph
from chaosai.models import (
    AbortRule,
    Approval,
    Baseline,
    ExperimentResult,
    ExperimentSpec,
    Hypothesis,
    PolicyDecision,
    RunMeta,
)
from chaosai.planner import plan_experiments
from chaosai.report import render_report
from chaosai.runner import demo_spec, execute_experiment, run_pipeline
from chaosai.runs import create_run, read_json, write_json
from chaosai.safety import (
    SafetyError,
    SafetyInterrupted,
    UndoJournal,
    clear_kill_flag,
    create_kill_flag,
    file_lock,
    kill_requested,
    policy_gate,
    replay_journals,
    rollback_signals,
    scope_guard,
)
from chaosai.seed import seed_target
from chaosai.smoke import smoke_target
from chaosai.target import TargetError, compose_up, get_docker_client

app = typer.Typer(
    help="ChaosPilot: measured experiments and offline fixture simulation.", no_args_is_help=True
)


@app.command()
def run(
    offline: bool = typer.Option(False, help="Disable LLM calls; use deterministic fixtures."),
    yes: bool = typer.Option(False, "--yes", help="Approve experiments."),
    dry_run: bool = typer.Option(False, help="Print plan/decisions without applying faults."),
    max_experiments: int = typer.Option(8, min=1, max=8, help="Maximum ranked experiments."),
    reuse_baseline: bool = typer.Option(
        False, help="Reuse only a stable baseline younger than 1h."
    ),
) -> None:
    """Plan and run sequential measured experiments, or isolated offline fixtures."""
    typer.echo(
        "Offline fixture simulation."
        if offline
        else "AI/heuristic plan; sequential measured faults; evidence analysis and scoring."
    )
    approver = Approver(yes=yes, is_tty=sys.stdin.isatty(), ask=input, simulation=offline)
    try:
        run_dir = run_pipeline(
            load_config(offline=offline),
            approver,
            dry_run=dry_run,
            offline=offline,
            max_experiments=max_experiments,
            reuse_baseline=reuse_baseline,
        )
    except (
        ApprovalRequired,
        MapperError,
        ValidationError,
        TargetError,
        docker.errors.DockerException,
        OSError,
    ) as exc:
        message = (
            "Invalid configuration or graph data."
            if isinstance(exc, ValidationError)
            else str(exc)
            if isinstance(exc, (ApprovalRequired, MapperError, TargetError))
            else "Target baseline failed; check Docker readiness and artifact storage."
        )
        typer.echo(message, err=True)
        raise typer.Exit(2) from exc
    decisions_path = run_dir / "decisions.json"
    decisions = read_json(decisions_path, list[PolicyDecision]) if decisions_path.exists() else []
    for decision in sorted(decisions, key=lambda d: d.final.rank):
        spec = decision.final
        would_run = spec.rank <= max_experiments and decision.allowed
        action = (
            "would run"
            if dry_run and would_run
            else ("outside selected limit" if not would_run else "planned")
        )
        typer.echo(
            f"{spec.rank}. {spec.fault} on {spec.target} ({spec.duration_s}s, "
            f"intensity={spec.intensity}); source={decision.source}; "
            f"{action}"
        )
        typer.echo(f"Reason: {spec.reason}")
        for clamp in decision.clamps:
            typer.echo(f"Clamp {clamp.field}: {clamp.requested} -> {clamp.final} ({clamp.reason})")
        if not decision.allowed:
            typer.echo(f"Denied: {decision.deny_reason}")
    from chaosai.models import RunMeta

    meta = read_json(run_dir / "run.json", RunMeta)
    exit_code = 0
    if meta.stop_reason:
        typer.echo(f"Stopped: {meta.stop_reason}")
        exit_code = (
            130
            if meta.stop_reason.startswith("Interrupted")
            else (0 if meta.stop_reason == "Approval: quit." else 2)
        )
    if not offline and not dry_run:
        for path in (run_dir / "experiments").glob("*/result.json"):
            result = read_json(path, ExperimentResult)
            show_experiment(result)
            if not result.rollback_ok and not result.skipped:
                exit_code = 2
            if result.abort_reason and result.abort_reason.startswith("Interrupted"):
                exit_code = 130
    if (run_dir / "report.html").exists():
        typer.echo(f"Report: {run_dir / 'report.html'}")
    else:
        typer.echo(f"Run evidence: {run_dir}")
    if exit_code:
        raise typer.Exit(exit_code)


@app.command(name="map")
def map_command(
    mermaid_output: bool = typer.Option(False, "--mermaid", help="Write docs/graph.mmd."),
    offline: bool = typer.Option(
        False, help="Use bundled graph fixture without reading the target."
    ),
    run_dir: Path | None = typer.Option(None, help="Existing run directory inside chaos-ai/runs/."),
) -> None:
    """Map Compose references, apply overrides, and save run/latest graph artifacts."""
    try:
        # Mapping requires no credentials: use process configuration, never open .env.
        settings = load_config(offline=True)
        graph = map_target(settings, offline=offline)
        if run_dir is not None:
            run_dir = (settings.project_root / run_dir).resolve()
            if not run_dir.is_dir() or not run_dir.is_relative_to(settings.runs_dir.resolve()):
                raise MapperError("--run-dir must be an existing directory inside chaos-ai/runs/.")
        else:
            run_dir = create_run(settings.runs_dir)
        save_graph(graph, settings, run_dir)
        if mermaid_output:
            path = settings.project_root / "docs" / "graph.mmd"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(mermaid(graph), encoding="utf-8")
    except (MapperError, ValidationError, OSError) as exc:
        message = (
            str(exc) if isinstance(exc, MapperError) else "Cannot map: check YAML/configuration."
        )
        typer.echo(message, err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Graph: {graph.app} ({len(graph.nodes)} nodes, {len(graph.edges)} edges)")
    typer.echo("SERVICE | KIND | CONTAINER | HOST PORTS | CONTAINER PORTS | INJECTABLE | REASON")
    for node in graph.nodes:
        host = ",".join(str(port) for port in node.host_ports) or "-"
        container = ",".join(str(port) for port in node.container_ports) or "-"
        typer.echo(
            f"{node.id} | {node.kind} | {node.container or '-'} | {host} | {container} | "
            f"{'yes' if node.injectable else 'no'} | {node.injectable_reason or '-'}"
        )
    typer.echo("SOURCE -> TARGET | KIND | CRITICALITY | EVIDENCE")
    for edge in graph.edges:
        typer.echo(
            f"{edge.source} -> {edge.target} | {edge.kind} | {edge.criticality} | {edge.evidence}"
        )
    for flow in graph.flows:
        typer.echo(f"Flow {flow.name} touches: {', '.join(flow.path)}")
    for variable in graph.sources.ignored_config:
        typer.echo(f"Ignored config (CORS/allowed origins): {variable}")
    typer.echo(f"Graph JSON: {run_dir / 'graph.json'}")
    typer.echo(f"Latest graph: {settings.project_root / '.chaosai' / 'graph.json'}")
    if mermaid_output:
        typer.echo(f"Mermaid: {settings.project_root / 'docs' / 'graph.mmd'}")


@app.command()
def report(run_dir: Path = typer.Argument(..., help="Run directory path or run ID.")) -> None:
    """Regenerate the HTML report from saved artifacts."""
    if not run_dir.is_dir():
        run_dir = PROJECT_ROOT / "runs" / run_dir
    # Keep all writes inside this project, including report regeneration.
    if not run_dir.resolve().is_relative_to(PROJECT_ROOT):
        typer.echo("Run directory must be inside chaos-ai/.", err=True)
        raise typer.Exit(2)
    try:
        path = render_report(run_dir)
    except (OSError, ValueError) as exc:
        typer.echo("Cannot render run artifacts; check saved JSON and sample paths.", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Report: {path}")


@app.command()
def replay(
    run_dir: Path = typer.Argument(..., help="Saved run path or ID; cache-only analysis."),
) -> None:
    """Recompute analysis/scores from saved evidence and caches, with no network or dotenv."""
    if not run_dir.is_dir():
        run_dir = PROJECT_ROOT / "runs" / run_dir
    if not run_dir.resolve().is_relative_to(PROJECT_ROOT):
        typer.echo("Run directory must be inside chaos-ai/.", err=True)
        raise typer.Exit(2)
    try:
        analyze_run(run_dir, load_config(offline=True), cached_only=True)
        path = render_report(run_dir)
    except (OSError, ValueError) as exc:
        typer.echo("Cannot replay run artifacts; check saved JSON and sample paths.", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Replay (cache only; missing/invalid entries use fallback): {path}")


def target_config() -> Settings:
    try:
        return load_config()
    except ValidationError as exc:
        typer.echo("Invalid target configuration; check variable types in .env.example.", err=True)
        raise typer.Exit(1) from exc


def target_failure(exc: Exception) -> None:
    message = (
        str(exc)
        if isinstance(exc, (TargetError, ApprovalRequired, MapperError))
        else ("Docker target operation failed; check scoped containers and Docker Desktop.")
    )
    typer.echo(f"FAIL: {message}", err=True)
    raise typer.Exit(1) from exc


@app.command()
def doctor(
    llm: bool = typer.Option(False, help="Also probe the configured LLM with JSON."),
) -> None:
    """Check Python, Docker, target readiness and configuration presence."""
    settings = target_config()
    checks = doctor_checks(settings)
    if llm:
        checks.append(llm_check(settings))
    typer.echo("RESULT | CHECK | DETAIL / FIX HINT")
    for check in checks:
        outcome = "PASS" if check.passed else "FAIL"
        hint = f" Fix: {check.hint}" if not check.passed else ""
        typer.echo(f"{outcome} | {check.name} | {check.detail}{hint}")
    if any(not check.passed for check in checks):
        raise typer.Exit(1)


@app.command("plan")
def plan_command(
    offline: bool = typer.Option(False, help="Use graph/baseline fixtures and heuristics only."),
) -> None:
    """Save a ranked plan and policy decision trace without load or Docker access."""
    try:
        settings = load_config(offline=offline)
        graph = map_target(settings, offline=offline)
        latest = settings.safety_dir / "baseline.json"
        if offline:
            from chaosai.fixture_data import fixture

            baseline = fixture("baseline", Baseline)
        elif latest.exists():
            baseline = read_json(latest, Baseline)
        else:
            raise SafetyError("No saved baseline; run chaosai baseline or use plan --offline.")
        run_dir = create_run(settings.runs_dir)
        save_graph(graph, settings, run_dir)
        write_json(run_dir / "baseline.json", baseline)
        plan, source = plan_experiments(
            graph,
            baseline,
            provider="none" if offline else settings.llm_provider,
            cache_dir=run_dir / "llm",
            settings=settings,
        )
        decisions = read_json(run_dir / "decisions.json", list[PolicyDecision])
    except (ValidationError, MapperError, TargetError, OSError) as exc:
        target_failure(exc)
    typer.echo(f"Planner: {source}; baseline stable: {baseline.stable}")
    typer.echo("RANK | FAULT | TARGET | DURATION | SOURCE | REASON")
    for spec, decision in zip(plan.root, decisions, strict=True):
        entry = definition(spec.fault)
        pending = " [planning only]" if entry and not entry.implemented else ""
        typer.echo(
            f"{spec.rank} | {spec.fault}{pending} | {spec.target} | {spec.duration_s}s | "
            f"{decision.source} | {spec.reason}"
        )
        for clamp in decision.clamps:
            typer.echo(f"  {clamp.field}: {clamp.requested} -> {clamp.final}; {clamp.reason}")
    typer.echo(f"Plan: {run_dir / 'plan.json'}")
    typer.echo(f"Decision trace: {run_dir / 'decisions.json'}")


@app.command()
def up() -> None:
    """Build and start the target's named Compose project."""
    try:
        compose_up(target_config())
    except TargetError as exc:
        target_failure(exc)
    typer.echo("PASS: target Compose up completed; run chaosai doctor after services are ready.")


@app.command()
def status() -> None:
    """Show only COMPOSE_PROJECT containers and their published ports."""
    settings = target_config()
    try:
        states = target_status(settings)
    except (TargetError, docker.errors.DockerException) as exc:
        target_failure(exc)
    typer.echo("SERVICE | CONTAINER | STATE | HEALTH | HOST PORTS")
    for state in states:
        ports = ", ".join(f"{host}:{port}" for host, port in state.ports) or "none"
        paused = " (paused)" if state.paused else ""
        typer.echo(
            f"{state.service} | {state.name} | {state.state}{paused} | {state.health} | {ports}"
        )
    if any(not check.passed for check in status_checks(states, settings)[:4]):
        typer.echo(
            "FAIL: missing, stopped, unhealthy or unpublished target service; run chaosai doctor."
        )
        raise typer.Exit(1)


@app.command()
def seed(
    users: int | None = typer.Option(None, min=0, help="Load users (default SEED_USERS=20)."),
) -> None:
    """Register missing test accounts, promote admin, and create a high-stock catalog."""
    settings = target_config()
    docker_client = None
    try:
        docker_client = get_docker_client()
        with httpx.Client(timeout=settings.request_timeout_s, trust_env=False) as client:
            result = seed_target(settings, docker_client, client, users)
    except (TargetError, docker.errors.DockerException) as exc:
        target_failure(exc)
    finally:
        if docker_client is not None:
            docker_client.close()
    typer.echo(
        f"PASS: seed complete; accounts created={result.users_created}, "
        f"existing={result.users_existing}; categories created={result.categories_created}; "
        f"products created={result.products_created}; DB verified users={result.verified_users}, "
        f"admin={result.verified_admin}, seed products={result.verified_products}, "
        f"total products={result.product_count}."
    )

    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")


@app.command()
def smoke() -> None:
    """Browse, login, create/retrieve an order; check notification evidence best-effort."""
    settings = target_config()
    try:
        with httpx.Client(timeout=settings.request_timeout_s, trust_env=False) as client:
            result = smoke_target(settings, client)
    except (TargetError, docker.errors.DockerException) as exc:
        target_failure(exc)
    typer.echo(f"PASS: browse -> login -> checkout -> retrieve; order={result.order_id}.")
    for evidence in result.evidence:
        typer.echo(f"PASS: {evidence}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")
    typer.echo("Notification receipt evidence does not prove Resend email delivery.")


@app.command(name="load")
def load_command(
    users: int | None = typer.Option(None, min=1, max=150, help="Workers (default LOAD_USERS=10)."),
    duration: float | None = typer.Option(None, min=0.01, help="Seconds (default 30)."),
) -> None:
    """Send homepage/browse/login/checkout load; checkout creates real orders."""
    settings = target_config()
    run_dir = create_run(settings.runs_dir)
    try:
        with file_lock(settings.active_lock_path, blocking=False):
            stats = run_load(
                settings,
                run_dir,
                users if users is not None else settings.load_users,
                duration if duration is not None else settings.load_duration_s,
            )
    except (TargetError, docker.errors.DockerException, OSError) as exc:
        target_failure(exc)
    for name, value in stats.items():
        typer.echo(
            f"{name}: n={value.n}, errors={value.error_rate:.2%}, "
            f"p95={value.p95_ms:.1f}ms, rps={value.rps:.2f}"
        )
    typer.echo(f"Samples: {run_dir / 'samples.jsonl'}")


@app.command(name="baseline")
def baseline_command(
    duration: float | None = typer.Option(
        None, min=0.01, help="Measurement seconds (default 120)."
    ),
    quick: bool = typer.Option(False, help="10s warm-up plus 40s measurement."),
) -> None:
    """Measure live load and save run/latest baseline; checkout creates real orders."""
    settings = target_config()
    run_dir = create_run(settings.runs_dir)
    try:
        with file_lock(settings.active_lock_path, blocking=False):
            graph = map_target(settings)
            save_graph(graph, settings, run_dir)
            value = measure_baseline(graph, settings, run_dir, duration_s=duration, quick=quick)
    except (TargetError, MapperError, docker.errors.DockerException, OSError) as exc:
        target_failure(exc)
    for name, stats in value.journeys.items():
        typer.echo(
            f"{name}: n={stats.n}, errors={stats.error_rate:.2%}, "
            f"p95={stats.p95_ms:.1f}ms, rps={stats.rps:.2f}"
            f"{' [low_sample]' if stats.low_sample else ''}"
        )
    typer.echo(f"Stable: {value.stable}; window: {value.window_s:.1f}s")
    typer.echo(f"Baseline: {run_dir / 'baseline.json'}")
    typer.echo(f"Latest baseline: {settings.project_root / '.chaosai' / 'baseline.json'}")


fault_app = typer.Typer(help="Low-level faults with durable rollback.", no_args_is_help=True)
app.add_typer(fault_app, name="fault")


@fault_app.command("apply")
def fault_apply(
    fault: str,
    target: str,
    duration: int = typer.Option(30, help="Seconds, clamped to [5,120]."),
    yes: bool = typer.Option(False, "--yes", help="Approve this live fault."),
) -> None:
    """Apply kill/stop/pause; automatically undo at duration, signal or kill switch."""
    approver = Approver(yes=yes, is_tty=sys.stdin.isatty(), ask=input, simulation=False)
    client = None
    try:
        approver.ensure_interactive()
        settings = target_config()
        graph = map_target(settings)
        implementation = get_fault(fault)
        spec = ExperimentSpec(
            id=f"{target}-{fault}",
            fault=fault,
            target=target,
            duration_s=duration,
            intensity=None,
            rank=1,
            reason="Approved low-level safety test.",
            hypothesis=Hypothesis(
                journey="all",
                max_error_rate=0.02,
                max_p95_multiple=2,
                statement="Low-level fault/undo test.",
            ),
            abort=AbortRule(max_error_rate=0.5, max_p95_multiple=10),
        )
        decision = policy_gate(spec)
        implementation.validate(decision.final, graph)
        if not decision.allowed:
            raise SafetyError(decision.deny_reason or "Policy denied.")
        typer.echo(
            "Low-level test: duration, signals and KILL protection; no load/metric watchdog."
        )
        if approver.decide(decision) in {"skip", "quit"}:
            typer.echo("Fault skipped.")
            return
        client = get_docker_client()
        with file_lock(settings.active_lock_path, blocking=False):
            abort = threading.Event()
            with rollback_signals(lambda: replay_journals(client, settings), abort):
                replay_journals(client, settings)  # Reconcile before every low-level run.
                if kill_requested(settings):
                    raise SafetyError("KILL flag present; run chaosai rollback --clear-kill first.")
                run_dir = create_run(settings.runs_dir)
                write_json(run_dir / "decisions.json", [decision])
                save_graph(graph, settings, run_dir)
                ctx = FaultContext(
                    decision.final,
                    graph,
                    client,
                    settings,
                    UndoJournal(settings.journal_path(run_dir), client, settings),
                )
                implementation.apply(ctx)
                typer.echo(f"Applied {fault} to {target} for {decision.final.duration_s}s.")
                typer.echo(f"Journal: {ctx.journal.path}")
                reason = hold_fault(implementation, ctx, abort)
                implementation.undo(ctx)
                if reason:
                    typer.echo(reason)
                if ctx.self_healed is not None:
                    typer.echo(f"Observed self-heal: {ctx.self_healed}")
        typer.echo("Rollback complete.")
    except SafetyInterrupted:
        typer.echo("Interrupted; rollback complete.")
        raise typer.Exit(130) from None
    except (
        ApprovalRequired,
        TargetError,
        MapperError,
        docker.errors.DockerException,
        OSError,
        ValidationError,
    ) as exc:
        target_failure(exc)
    finally:
        if client is not None:
            client.close()


@fault_app.command("undo")
def fault_undo(fault: str, target: str) -> None:
    """Replay pending records for one scoped target/fault; recovery needs no approval."""
    settings = target_config()
    client = None
    try:
        get_fault(fault)
        graph = map_target(settings)
        client = get_docker_client()
        container = scope_guard(target, graph, client, settings.compose_project)
        count = replay_journals(client, settings, container=container.name, fault=fault)
        typer.echo(f"Rollback complete: {count} undo records restored.")
    except (
        TargetError,
        MapperError,
        docker.errors.DockerException,
        OSError,
        ValidationError,
    ) as exc:
        target_failure(exc)
    finally:
        if client is not None:
            client.close()


@app.command("rollback")
def rollback_command(
    clear_kill: bool = typer.Option(False, help="Clear KILL after recovery, with no active fault."),
) -> None:
    """Replay all pending undo journals, retaining failures for retry."""
    settings = target_config()
    client = None
    try:
        client = get_docker_client()
        count = replay_journals(client, settings)
        if clear_kill:
            clear_kill_flag(settings)
        typer.echo(f"Rollback complete: {count} undo records restored.")
    except (TargetError, docker.errors.DockerException, OSError, ValidationError) as exc:
        target_failure(exc)
    finally:
        if client is not None:
            client.close()


@app.command("kill")
def kill_command() -> None:
    """Persist KILL before connecting to Docker, then directly replay every journal."""
    settings = target_config()
    client = None
    try:
        create_kill_flag(settings)
        client = get_docker_client()
        count = replay_journals(client, settings)
        typer.echo(f"KILL set; rollback complete: {count} undo records restored.")
        typer.echo("After the injector exits, use chaosai rollback --clear-kill to resume.")
    except (TargetError, docker.errors.DockerException, OSError, ValidationError) as exc:
        target_failure(exc)
    finally:
        if client is not None:
            client.close()


def show_experiment(result: ExperimentResult) -> None:
    impact = ", ".join(result.impacted_journeys) or "none observed"
    typer.echo(f"Verdict: {result.verdict}; impact: {impact}")
    typer.echo(f"Container rollback OK: {result.rollback_ok}")
    typer.echo(
        f"Application recovered: {result.recovered}; recovery seconds: {result.recovery_time_s}"
    )
    typer.echo(f"Abort reason: {result.abort_reason or 'none'}")
    if result.skipped:
        typer.echo(f"Skipped: {result.skip_reason}")
    for name, stats in result.during.items():
        typer.echo(
            f"During {name}: n={stats.n}, errors={stats.error_rate:.2%}, p95={stats.p95_ms:.1f}ms"
        )
    for limit in result.evidence_limits:
        typer.echo(f"Evidence limit: {limit}")


@app.command("inject")
def inject_command(
    fault: str,
    target: str,
    duration: int = typer.Option(30, help="Seconds, policy-clamped to [5,120]."),
    abort_error_rate: float | None = typer.Option(None, help="Watchdog error limit, [0.05,0.9]."),
    yes: bool = typer.Option(False, "--yes", help="Approve this measured live experiment."),
) -> None:
    """Inject kill/stop/pause with load, watchdog and measured recovery."""
    approver = Approver(yes=yes, is_tty=sys.stdin.isatty(), ask=input, simulation=False)
    try:
        approver.ensure_interactive()
        settings = target_config()
        graph = map_target(settings)
        latest = settings.safety_dir / "baseline.json"
        if not latest.exists():
            raise SafetyError("No saved baseline; run chaosai baseline first.")
        baseline = read_json(latest, Baseline)
        decision = policy_gate(
            demo_spec(
                baseline,
                fault=fault,
                target=target,
                duration=duration,
                abort_error_rate=abort_error_rate,
            )
        )
        get_fault(fault).validate(decision.final, graph)
        from chaosai.approval import approval_prompt

        typer.echo(approval_prompt(decision, simulation=False).rsplit("[y]es", 1)[0])
        run_dir = create_run(settings.runs_dir)
        save_graph(graph, settings, run_dir)
        write_json(run_dir / "baseline.json", baseline)
        write_json(run_dir / "decisions.json", [decision])
        write_json(run_dir / "plan.json", [decision.final])
        meta = RunMeta(
            run_id=run_dir.name,
            started_at=datetime.now(UTC),
            planner_source="heuristic",
            analyzer_source="heuristic",
            target_project=settings.compose_project,
            approvals=[],
            offline=False,
            data_source="measured",
            baseline_source="reused",
            phase="EXPERIMENTS",
            target_profile="owned-auth"
            if (settings.safety_dir / "sandbox.json").exists()
            else "original",
            credential_cache_s=settings.credential_cache_s,
        )
        write_json(run_dir / "run.json", meta)
        result = execute_experiment(
            decision, run_dir, settings=settings, graph=graph, baseline=baseline, approver=approver
        )
        meta.approvals.append(
            Approval(experiment_id=result.spec.id, decision=result.approval_decision)
        )
        meta.phase = "STOPPED" if result.interrupted or kill_requested(settings) else "COMPLETE"
        meta.finished_at = datetime.now(UTC)
        write_json(run_dir / "run.json", meta)
        # Single-fault diagnostics always finish quickly with deterministic analysis.
        analyze_run(run_dir, settings.model_copy(update={"llm_provider": "none"}))
        render_report(run_dir)
    except ApprovalRequired as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    except (
        TargetError,
        MapperError,
        ValidationError,
        docker.errors.DockerException,
        OSError,
    ) as exc:
        target_failure(exc)
    show_experiment(result)
    typer.echo(f"Result: {run_dir / 'experiments' / result.spec.id / 'result.json'}")
    if result.approval_decision == "quit":
        raise typer.Exit(130)
    if not result.rollback_ok and not result.skipped:
        raise typer.Exit(2)
    if result.abort_reason and result.abort_reason.startswith("Interrupted"):
        raise typer.Exit(130)


@app.command()
def sandbox(
    restore: bool = typer.Option(False, help="Restore the original scoped auth image."),
) -> None:
    """Prepare an owned local auth image for load tests; never edit target source."""
    from chaosai.sandbox import setup_sandbox

    try:
        setup_sandbox(load_config(), restore=restore)
    except (TargetError, OSError, ValueError, docker.errors.DockerException) as exc:
        typer.echo(str(exc) if isinstance(exc, TargetError) else "Sandbox setup failed.")
        raise typer.Exit(1) from exc
    typer.echo(
        "Original auth image restored."
        if restore
        else "Sandbox auth ready: 6000 requests/15m; limiter retained; target source unchanged."
    )


@app.command()
def dashboard(
    host: str | None = typer.Option(None, help="Loopback address (default DASHBOARD_HOST)."),
    port: int | None = typer.Option(None, min=1024, max=65535, help="DASHBOARD_PORT."),
) -> None:
    """Serve live experiments, metrics, findings and approved local controls."""
    from chaosai.dashboard import make_server

    try:
        settings = load_config()
    except (OSError, ValueError) as exc:
        typer.echo(
            "Dashboard could not load configuration. Check configuration file access, "
            "DASHBOARD_HOST (127.0.0.1 or localhost) and DASHBOARD_PORT (1024–65535).",
            err=True,
        )
        raise typer.Exit(1) from exc
    try:
        host = host or settings.dashboard_host
        server = make_server(settings, host, port or settings.dashboard_port)
    except OSError as exc:
        address = f"http://{host}:{port or settings.dashboard_port}"
        if exc.errno == errno.EADDRINUSE:
            hint = (
                f"Port {port or settings.dashboard_port} is already in use. "
                f"If ChaosPilot is already running, open {address}. "
                "Otherwise choose a free port: chaosai dashboard --port 8766."
            )
        elif exc.errno in {errno.EACCES, errno.EPERM}:
            hint = (
                "Permission denied when opening the local dashboard socket. "
                "Run chaosai dashboard in your regular terminal, or allow this "
                "command to run outside the agent sandbox."
            )
        else:
            hint = f"Could not bind {address} (OS error {exc.errno}). Check the host and port."
        typer.echo(f"Dashboard could not start. {hint}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(
            "Dashboard could not start. Check DASHBOARD_HOST (127.0.0.1 or localhost) "
            "and DASHBOARD_PORT (1024–65535).",
            err=True,
        )
        raise typer.Exit(1) from exc
    typer.echo(f"ChaosPilot dashboard: http://{host}:{server.server_port}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.jobs.shutdown()
        server.server_close()
