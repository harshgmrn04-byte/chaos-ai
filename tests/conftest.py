"""Tests cannot use network sockets or read project dotenv files."""

import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import docker
import pytest

from chaosai import cli, config
from chaosai.config import Settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("integration"):
        return

    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network and dotenv reads are forbidden in unit tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(config, "load_dotenv", denied)
    monkeypatch.setattr(docker, "from_env", denied)
    monkeypatch.setattr(docker, "DockerClient", denied)
    monkeypatch.setattr(subprocess, "run", denied)
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_MODEL",
        "LLM_PROVIDER",
        "TARGET_DIR",
        "COMPOSE_PROJECT",
        "DOCKER_HOST",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "FRONTEND_URL",
        "LOAD_USERS",
        "LOAD_DURATION_S",
        "THINK_MIN_S",
        "THINK_MAX_S",
        "METRICS_INTERVAL_S",
        "LOAD_TIMEOUT_S",
        "CHECKOUT_RPS",
        "AUTH_RPS",
        "TOKEN_REFRESH_S",
        "WARMUP_S",
        "BASELINE_DURATION_S",
        "AUTH_URL",
        "PRODUCT_URL",
        "ORDER_URL",
        "RABBITMQ_MANAGEMENT_URL",
        "RABBITMQ_USER",
        "RABBITMQ_PASSWORD",
        "RABBITMQ_VHOST",
        "SEED_USERS",
        "SEED_PASSWORD",
        "ADMIN_PASSWORD",
        "AUTH_TIMEOUT_S",
        "REQUEST_TIMEOUT_S",
        "NOTIFICATION_INTERVAL_S",
    ):
        monkeypatch.setenv(key, "")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path, llm_provider="mock")


@pytest.fixture
def cli_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    def test_config(*, offline: bool = False) -> Settings:
        return Settings(
            project_root=tmp_path,
            llm_provider="none" if offline else "mock",
            target_dir=Path(__file__).parent / "fixtures",
        )

    monkeypatch.setattr(cli, "load_config", test_config)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    yield tmp_path
