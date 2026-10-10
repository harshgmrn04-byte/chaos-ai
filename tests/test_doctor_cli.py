from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from chaosai import cli, config, doctor
from chaosai.config import Settings
from chaosai.models import CheckResult, ContainerStatus, SeedResult
from chaosai.target import TargetError


def healthy_states(settings: Settings) -> list[ContainerStatus]:
    return [
        ContainerStatus(
            service=service,
            name="test-" + service,
            state="running",
            health="healthy",
            paused=False,
            ports=[("127.0.0.1", 3004)] if service in settings.published_services else [],
        )
        for service in settings.expected_services
    ]


def test_doctor_all_passes_without_printing_key(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "never-print-this-key")
    monkeypatch.setattr(Path, "is_file", lambda _: True)
    monkeypatch.setattr(doctor, "target_status", lambda _: healthy_states(settings))
    monkeypatch.setattr(doctor, "tcp_ready", lambda *_: True)
    monkeypatch.setattr(doctor, "http_ready", lambda *_: True)
    checks = doctor.doctor_checks(settings)
    assert all(check.passed for check in checks)
    assert "never-print-this-key" not in str(checks) + repr(settings)
    assert len([check for check in checks if check.name.startswith("TCP")]) == 9


def test_doctor_failure_has_fix_hints(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        doctor, "target_status", MagicMock(side_effect=TargetError("Docker offline"))
    )
    checks = doctor.doctor_checks(settings)
    failures = [check for check in checks if not check.passed]
    assert {check.name for check in failures} >= {
        "Docker reachable",
        "Target containers found",
        "Target containers running",
        "Published host ports",
        "LLM key present",
        ".env present",
    }
    assert all(check.hint for check in failures)


def test_status_detects_paused_unhealthy_and_unreachable_ports(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    states = healthy_states(settings)
    states[0].paused = True
    states[1].health = "unhealthy"
    monkeypatch.setattr(doctor, "tcp_ready", lambda *_: False)
    checks = doctor.status_checks(states, settings)
    assert not checks[1].passed and not checks[2].passed
    assert any(not check.passed for check in checks if check.name.startswith("TCP"))


def test_doctor_cli_pass_fail_table(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cli,
        "doctor_checks",
        lambda _: [
            CheckResult(
                name="Docker", passed=False, detail="unreachable", hint="Start Docker Desktop"
            )
        ],
    )
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL | Docker" in result.output and "Start Docker Desktop" in result.output
    monkeypatch.setattr(
        cli,
        "doctor_checks",
        lambda _: [CheckResult(name="Docker", passed=True, detail="reachable")],
    )
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 0 and "PASS | Docker" in result.output


def test_status_cli_refuses_missing_services(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "target_status", lambda _: [])
    result = CliRunner().invoke(cli.app, ["status"])
    assert result.exit_code == 1 and "missing" in result.output


def test_up_cli_success_and_safe_error(
    cli_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = MagicMock()
    monkeypatch.setattr(cli, "compose_up", run)
    result = CliRunner().invoke(cli.app, ["up"])
    assert result.exit_code == 0 and "PASS" in result.output
    run.assert_called_once()
    run.side_effect = TargetError("Compose failed")
    result = CliRunner().invoke(cli.app, ["up"])
    assert result.exit_code == 1 and "Compose failed" in result.output


def test_seed_cli_option_and_close(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(cli, "get_docker_client", lambda: client)
    execute = MagicMock(return_value=SeedResult(users_created=4))
    monkeypatch.setattr(cli, "seed_target", execute)
    result = CliRunner().invoke(cli.app, ["seed", "--users", "3"])
    assert result.exit_code == 0 and "created=4" in result.output
    assert execute.call_args.args[3] == 3
    client.close.assert_called_once()
    result = CliRunner().invoke(cli.app, ["seed", "--users", "-1"])
    assert result.exit_code == 2


def test_config_defaults_and_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kw: None)
    monkeypatch.setenv("SEED_USERS", "7")
    monkeypatch.setenv("AUTH_URL", "http://localhost:9004")
    monkeypatch.setenv("SEED_PASSWORD", "fake-secret-password")
    monkeypatch.setenv("RABBITMQ_PASSWORD", "fake-secret-rabbit")
    settings = config.load_config(project_root=tmp_path)
    assert settings.auth_url == "http://localhost:9004" and settings.seed_users == 7
    assert settings.resolved_target_dir == (tmp_path / "../ecommerce-microservices").resolve()
    assert "fake-secret" not in repr(settings)


def test_invalid_config_cli_hides_values(cli_workspace: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "load_config", lambda: Settings(seed_users="fake-secret-invalid"))
    result = CliRunner().invoke(cli.app, ["doctor"])
    assert result.exit_code == 1 and "Invalid target configuration" in result.output
    assert "fake-secret-invalid" not in result.output


def test_admin_password_environment_is_separate_and_masked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda *_args, **_kw: None)
    monkeypatch.setenv("ADMIN_PASSWORD", "fake-admin-secret")
    monkeypatch.setenv("SEED_PASSWORD", "fake-load-secret")
    monkeypatch.setenv("AUTH_TIMEOUT_S", "3")
    settings = config.load_config(project_root=tmp_path)
    assert settings.admin_login_password.get_secret_value() == "fake-admin-secret"
    assert settings.seed_password.get_secret_value() == "fake-load-secret"
    assert settings.auth_timeout_s == 3
    assert "fake-admin-secret" not in repr(settings)


@pytest.mark.parametrize("timeout", [2, 6])
def test_auth_timeout_is_bounded(timeout: int) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(auth_timeout_s=timeout)
