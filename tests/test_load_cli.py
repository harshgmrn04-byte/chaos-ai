from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from chaosai import cli
from chaosai.config import Settings, load_config
from chaosai.fixture_data import fixture
from chaosai.models import Baseline
from chaosai.safety import file_lock
from chaosai.target import TargetError


def test_cli_load_options(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock(return_value=fixture("baseline", Baseline).journeys)
    monkeypatch.setattr(cli, "run_load", run)
    result = CliRunner().invoke(cli.app, ["load", "--users", "7", "--duration", "20"])
    assert result.exit_code == 0, result.output
    assert run.call_args.args[2:] == (7, 20)
    assert "Samples:" in result.output


@pytest.mark.parametrize(
    "options,duration,quick",
    [([], None, False), (["--duration", "55"], 55, False), (["--quick"], None, True)],
)
def test_cli_baseline_options(
    cli_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    options: list[str],
    duration: float | None,
    quick: bool,
) -> None:
    measure = Mock(return_value=fixture("baseline", Baseline))
    monkeypatch.setattr(cli, "measure_baseline", measure)
    result = CliRunner().invoke(cli.app, ["baseline", *options])
    assert result.exit_code == 0, result.output
    assert measure.call_args.kwargs == {"duration_s": duration, "quick": quick}
    assert "Stable: True" in result.output and "Latest baseline:" in result.output


def test_cli_target_failure_no_traceback(
    cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "run_load", Mock(side_effect=TargetError("Docker unreachable")))
    result = CliRunner().invoke(cli.app, ["load", "--users", "1", "--duration", "2"])
    assert result.exit_code == 1 and "FAIL: Docker unreachable" in result.output


@pytest.mark.parametrize("command", ["load", "baseline"])
def test_cli_load_rejects_concurrent_live_work(cli_workspace, monkeypatch, command):
    operation = Mock()
    monkeypatch.setattr(cli, "run_load", operation)
    monkeypatch.setattr(cli, "measure_baseline", operation)
    with file_lock(cli_workspace / ".chaosai/active.lock", blocking=False):
        result = CliRunner().invoke(cli.app, [command])
    assert result.exit_code == 1
    operation.assert_not_called()


@pytest.mark.parametrize(
    "option,value", [("--users", "0"), ("--users", "151"), ("--duration", "0")]
)
def test_cli_load_invalid_options(option: str, value: str) -> None:
    assert CliRunner().invoke(cli.app, ["load", option, value]).exit_code == 2


def test_load_env_configuration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in {
        "FRONTEND_URL": "http://localhost:9000",
        "CHECKOUT_RPS": "0.5",
        "LOGIN_RATE_CAP": "0.75",
        "LOAD_USERS": "8",
        "BASELINE_DURATION_S": "60",
        "SEED_PASSWORD": "",
    }.items():
        monkeypatch.setenv(key, value)
    settings = load_config(offline=True, project_root=tmp_path)
    assert settings.frontend_url == "http://localhost:9000" and settings.checkout_rps == 0.5
    assert settings.load_users == 8 and settings.baseline_duration_s == 60
    assert settings.seed_password.get_secret_value() == ""
    assert settings.login_rate_cap == 0.75
    assert Settings().login_rate_cap == 1
    with pytest.raises(ValidationError):
        Settings(think_min_s=1, think_max_s=0.2)
    with pytest.raises(ValidationError):
        Settings(login_rate_cap=0)
