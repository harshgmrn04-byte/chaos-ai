"""Phase 1 CLI; all target actions remain explicit placeholders."""

import sys
from pathlib import Path

import typer
from pydantic import ValidationError

from chaosai.approval import ApprovalRequired, Approver
from chaosai.config import PROJECT_ROOT, load_config
from chaosai.report import render_report
from chaosai.runner import run_pipeline

app = typer.Typer(help="ChaosPilot (Phase 1 fixture simulation).", no_args_is_help=True)


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


def not_implemented() -> None:
    typer.echo("not implemented yet")
    raise typer.Exit(2)


for command_name in (
    "doctor",
    "up",
    "status",
    "seed",
    "smoke",
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
