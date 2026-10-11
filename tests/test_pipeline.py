from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from chaosai import cli, runner
from chaosai.approval import ApprovalRequired, Approver
from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.models import (
    Baseline,
    ExperimentResult,
    Finding,
    Graph,
    Plan,
    RunMeta,
    Sample,
    Score,
    Summary,
)
from chaosai.report import render_report
from chaosai.runs import read_json, read_jsonl, write_json

cli_runner = CliRunner()


@pytest.fixture(autouse=True)
def compose_fixture(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    settings.target_dir = Path(__file__).parent / "fixtures"
    monkeypatch.setattr(runner, "measure_baseline", lambda *a, **kw: fixture("baseline", Baseline))


def test_full_offline_cli(cli_workspace: Path) -> None:
    result = cli_runner.invoke(cli.app, ["run", "--offline", "--yes"])
    assert result.exit_code == 0, result.output
    run_dir = next((cli_workspace / "runs").iterdir())
    graph = read_json(run_dir / "graph.json", Graph)
    assert not graph.sources.parsed
    assert read_json(cli_workspace / ".chaosai" / "graph.json", Graph) == graph
    meta = read_json(run_dir / "run.json", RunMeta)
    assert meta.planner_source == meta.analyzer_source == "heuristic"
    assert [a.decision for a in meta.approvals] == ["yes"] * 3
    plan = read_json(run_dir / "plan.json", Plan)
    assert len({s.fault for s in plan.root}) == 3
    assert read_json(run_dir / "journal.json", list) == []
    assert not list((run_dir / "llm").iterdir())
    for spec in plan.root:
        exp = read_json(run_dir / "experiments" / spec.id / "result.json", ExperimentResult)
        assert exp.spec == spec
        assert len(read_jsonl(run_dir / exp.samples_path, Sample)) == 300
    assert len(read_json(run_dir / "findings.json", list[Finding])) == 3
    assert len(read_json(run_dir / "scores.json", list[Score])) == 3
    html = (run_dir / "report.html").read_text()
    for text in (
        "fixture data only",
        "Application map",
        "Baseline",
        "Experiment plan",
        "Results",
        "Scores",
        "Findings",
        "Ranked weaknesses",
        "Top fixes",
    ):
        assert text in html
    assert "<style>" in html
    assert 'src="http' not in html and 'href="http' not in html
    assert (run_dir / "report.md").exists()
    rebuilt = cli_runner.invoke(cli.app, ["report", str(run_dir)])
    assert rebuilt.exit_code == 0, rebuilt.output
    assert (run_dir / "report.html").read_text() == html


def test_ordering_and_mock_cache(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    for stage in (
        "map_target",
        "measure_baseline",
        "plan_experiments",
        "execute_fixture",
        "analyze_results",
        "score_results",
        "render_report",
    ):
        original = getattr(runner, stage)

        def wrapped(
            *args: object,
            _name: str = stage,
            _func: Callable[..., object] = original,
            **kwargs: object,
        ) -> object:
            order.append(_name)
            return _func(*args, **kwargs)

        monkeypatch.setattr(runner, stage, wrapped)

    def ask(prompt: str) -> str:
        order.append("approval")
        return "y"

    run_dir = runner.run_pipeline(settings, Approver(yes=False, is_tty=True, ask=ask), offline=True)
    graph = read_json(run_dir / "graph.json", Graph)
    assert not graph.sources.parsed
    assert read_json(settings.project_root / ".chaosai" / "graph.json", Graph) == graph
    assert order == [
        "map_target",
        "measure_baseline",
        "plan_experiments",
        "approval",
        "execute_fixture",
        "approval",
        "execute_fixture",
        "approval",
        "execute_fixture",
        "analyze_results",
        "score_results",
        "render_report",
    ]
    meta = read_json(run_dir / "run.json", RunMeta)
    assert meta.planner_source == meta.analyzer_source == "mock"
    assert len(list((run_dir / "llm").glob("*.json"))) == 2


@pytest.mark.parametrize(
    "answers,expected,choices",
    [
        (["n", "y", "q"], ["postgres-pause"], ["skip", "yes", "quit"]),
        (["a"], ["redis-stop", "postgres-pause", "product-cpu"], ["all", "yes", "yes"]),
        (["q"], [], ["quit"]),
        (["n", "n", "n"], [], ["skip", "skip", "skip"]),
    ],
)
def test_skip_all_quit_and_matching_analysis(
    settings: Settings, answers: list[str], expected: list[str], choices: list[str]
) -> None:
    replies = iter(answers)
    run_dir = runner.run_pipeline(
        settings, Approver(yes=False, is_tty=True, ask=lambda _: next(replies)), offline=True
    )
    meta = read_json(run_dir / "run.json", RunMeta)
    assert [a.decision for a in meta.approvals] == choices
    assert sorted(p.name for p in (run_dir / "experiments").iterdir()) == sorted(expected)
    findings = read_json(run_dir / "findings.json", list[Finding])
    scores = read_json(run_dir / "scores.json", list[Score])
    assert {f.experiment_id for f in findings} == set(expected)
    assert {s.experiment_id for s in scores} == set(expected)
    summary = read_json(run_dir / "summary.json", Summary)
    if not expected:
        assert summary.band == "not assessed"
        assert summary.ranked_weaknesses == []


def test_noninteractive_refusal_creates_no_run(settings: Settings) -> None:
    with pytest.raises(ApprovalRequired):
        runner.run_pipeline(settings, Approver(yes=False, is_tty=False, ask=lambda _: "y"))
    assert not settings.runs_dir.exists()


def test_cli_refuses_without_yes(cli_workspace: Path) -> None:
    result = cli_runner.invoke(cli.app, ["run", "--offline"])
    assert result.exit_code == 2 and "No TTY" in result.output
    assert not (cli_workspace / "runs").exists()


def test_dry_run_never_prompts_or_executes(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> str:
        raise AssertionError("Dry run must not execute or prompt")

    monkeypatch.setattr(runner, "execute_fixture", fail)
    monkeypatch.setattr(runner, "execute_experiment", fail)
    run_dir = runner.run_pipeline(
        settings, Approver(yes=False, is_tty=False, ask=fail), dry_run=True
    )
    assert not list((run_dir / "experiments").iterdir())
    assert read_json(run_dir / "run.json", RunMeta).approvals == []
    assert read_json(run_dir / "scores.json", list[Score]) == []
    assert "No resilience score assessed" in (run_dir / "report.html").read_text()


def test_dry_run_cli(cli_workspace: Path) -> None:
    result = cli_runner.invoke(cli.app, ["run", "--offline", "--dry-run"])
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize(
    "command",
    [
        "replay",
    ],
)
def test_placeholder_commands(command: str) -> None:
    result = cli_runner.invoke(cli.app, [command, "ignored", "--offline"])
    assert result.exit_code == 2
    assert result.output.strip() == "not implemented yet"


def test_report_escapes_untrusted_text(settings: Settings) -> None:
    run_dir = runner.run_pipeline(
        settings, Approver(yes=True, is_tty=False, ask=lambda _: "y"), offline=True
    )
    findings = read_json(run_dir / "findings.json", list[Finding])
    findings[0].evidence.append('<script>alert("injected")</script>')
    write_json(run_dir / "findings.json", findings)
    html = render_report(run_dir).read_text()
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_direct_offline_run_cannot_call_gemini(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaosai import llm

    settings.llm_provider = "gemini"
    monkeypatch.setenv("GEMINI_API_KEY", "fake-unit-key")
    monkeypatch.setattr(llm.genai, "Client", lambda **kw: pytest.fail("Offline Gemini call"))
    directory = runner.run_pipeline(
        settings,
        Approver(yes=True, is_tty=False, ask=input),
        offline=True,
    )
    meta = read_json(directory / "run.json", RunMeta)
    assert meta.planner_source == meta.analyzer_source == "heuristic"
    assert not list((directory / "llm").iterdir())
