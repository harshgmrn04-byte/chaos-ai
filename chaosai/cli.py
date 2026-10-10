"""CLI for fixture runs and Phase 2 target operations."""

import sys
from pathlib import Path

import docker
import httpx
import typer
from pydantic import ValidationError

from chaosai.approval import ApprovalRequired, Approver
from chaosai.baseline import measure_baseline, run_load
from chaosai.config import PROJECT_ROOT, Settings, load_config
from chaosai.doctor import doctor_checks, status_checks, target_status
from chaosai.mapper import MapperError, map_target, mermaid, save_graph
from chaosai.report import render_report
from chaosai.runner import run_pipeline
from chaosai.runs import create_run
from chaosai.seed import seed_target
from chaosai.smoke import smoke_target
from chaosai.target import TargetError, compose_up, get_docker_client

app = typer.Typer(
    help="ChaosPilot: target tools and Phase 1 fixture simulation.", no_args_is_help=True
)


@app.command()
def run(
    offline: bool = typer.Option(False, help="Disable LLM calls; use deterministic fixtures."),
    yes: bool = typer.Option(False, "--yes", help="Approve all simulated experiments."),
    dry_run: bool = typer.Option(False, help="Write a plan/report without executing experiments."),
) -> None:
    """Run map → baseline → plan → approvals → fixture results → analysis → score → report."""
    typer.echo(
        "Experiments and findings remain fixtures. Offline baseline uses fixtures; "
        "live baseline sends load to the target."
    )
    approver = Approver(yes=yes, is_tty=sys.stdin.isatty(), ask=input)
    try:
        run_dir = run_pipeline(
            load_config(offline=offline), approver, dry_run=dry_run, offline=offline
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
    typer.echo(f"Report: {run_dir / 'report.html'}")


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
    except (OSError, ValidationError) as exc:
        typer.echo(f"Cannot render run artifacts: {exc}", err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Report: {path}")


def target_config() -> Settings:
    try:
        return load_config()
    except ValidationError as exc:
        typer.echo("Invalid target configuration; check variable types in .env.example.", err=True)
        raise typer.Exit(1) from exc


def target_failure(exc: Exception) -> None:
    message = (
        str(exc)
        if isinstance(exc, TargetError)
        else ("Docker target operation failed; check scoped containers and Docker Desktop.")
    )
    typer.echo(f"FAIL: {message}", err=True)
    raise typer.Exit(1) from exc


@app.command()
def doctor() -> None:
    """Check Python, Docker, target readiness and configuration presence."""
    checks = doctor_checks(target_config())
    typer.echo("RESULT | CHECK | DETAIL / FIX HINT")
    for check in checks:
        outcome = "PASS" if check.passed else "FAIL"
        hint = f" Fix: {check.hint}" if not check.passed else ""
        typer.echo(f"{outcome} | {check.name} | {check.detail}{hint}")
    if any(not check.passed for check in checks):
        raise typer.Exit(1)


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
        graph = map_target(settings)
        save_graph(graph, settings, run_dir)
        value = measure_baseline(graph, settings, run_dir, duration_s=duration, quick=quick)
    except (TargetError, MapperError, docker.errors.DockerException, OSError) as exc:
        target_failure(exc)
    for name, stats in value.journeys.items():
        typer.echo(
            f"{name}: n={stats.n}, errors={stats.error_rate:.2%}, "
            f"p95={stats.p95_ms:.1f}ms, rps={stats.rps:.2f}"
        )
    typer.echo(f"Stable: {value.stable}; window: {value.window_s:.1f}s")
    typer.echo(f"Baseline: {run_dir / 'baseline.json'}")
    typer.echo(f"Latest baseline: {settings.project_root / '.chaosai' / 'baseline.json'}")


def not_implemented() -> None:
    typer.echo("not implemented yet")
    raise typer.Exit(2)


for command_name in (
    "fault",
    "inject",
    "rollback",
    "kill",
    "plan",
    "replay",
):
    app.command(
        name=command_name,
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    )(not_implemented)
