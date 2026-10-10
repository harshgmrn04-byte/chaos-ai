"""CLI for fixture runs and Phase 2 target operations."""

import sys
from pathlib import Path

import docker
import httpx
import typer
from pydantic import ValidationError

from chaosai.approval import ApprovalRequired, Approver
from chaosai.config import PROJECT_ROOT, Settings, load_config
from chaosai.doctor import doctor_checks, status_checks, target_status
from chaosai.report import render_report
from chaosai.runner import run_pipeline
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
    typer.echo("Phase 1 simulation: fixture data only; no target or LLM calls.")
    approver = Approver(yes=yes, is_tty=sys.stdin.isatty(), ask=input)
    try:
        run_dir = run_pipeline(load_config(offline=offline), approver, dry_run=dry_run)
    except (ApprovalRequired, ValidationError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Report: {run_dir / 'report.html'}")


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
        f"products created={result.products_created}."
    )


@app.command()
def smoke() -> None:
    """Browse, login, create/retrieve an order; check notification evidence best-effort."""
    settings = target_config()
    try:
        with httpx.Client(timeout=settings.request_timeout_s, trust_env=False) as client:
            result = smoke_target(settings, client)
    except TargetError as exc:
        target_failure(exc)
    typer.echo(f"PASS: browse -> login -> checkout -> retrieve; order={result.order_id}.")
    for evidence in result.evidence:
        typer.echo(f"PASS: {evidence}")
    for warning in result.warnings:
        typer.echo(f"WARNING: {warning}")
    typer.echo("Notification receipt evidence does not prove Resend email delivery.")


def not_implemented() -> None:
    typer.echo("not implemented yet")
    raise typer.Exit(2)


for command_name in (
    "map",
    "load",
    "baseline",
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
