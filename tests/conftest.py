"""Tests cannot use network sockets or read project dotenv files."""

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest

from chaosai import cli, config
from chaosai.config import Settings


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    def denied(*args: object, **kwargs: object) -> None:
        raise AssertionError("Network and dotenv reads are forbidden in unit tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(config, "load_dotenv", denied)
    for key in (
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_MODEL",
        "LLM_PROVIDER",
        "TARGET_DIR",
        "COMPOSE_PROJECT",
    ):
        monkeypatch.setenv(key, "")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(project_root=tmp_path, llm_provider="mock")


@pytest.fixture
def cli_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    def test_config(*, offline: bool = False) -> Settings:
        return Settings(project_root=tmp_path, llm_provider="none" if offline else "mock")

    monkeypatch.setattr(cli, "load_config", test_config)
    monkeypatch.setattr(cli, "PROJECT_ROOT", tmp_path)
    yield tmp_path
