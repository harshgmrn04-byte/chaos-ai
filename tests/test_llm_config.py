from pathlib import Path

import pytest

from chaosai import config, llm
from chaosai.config import load_config
from chaosai.llm import LLMUnavailable, complete_json
from chaosai.models import Plan
from chaosai.runs import read_json


def test_mock_validates_and_caches(tmp_path: Path) -> None:
    plan = complete_json("planner", "system", "data", Plan, provider="mock", cache_dir=tmp_path)
    assert len(plan.root) == 3
    paths = list(tmp_path.glob("planner-*.json"))
    assert len(paths) == 1
    assert read_json(paths[0], Plan) == plan


@pytest.mark.parametrize("provider", ["none", "gemini"])
def test_unavailable_provider_does_not_call_transport(provider: str) -> None:
    with pytest.raises(LLMUnavailable):
        complete_json("planner", "system", "data", Plan, provider=provider)


def test_invalid_mock_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def invalid(name: str, cls: type[Plan]) -> Plan:
        calls.append(name)
        return cls.model_validate_json("not json")

    monkeypatch.setattr(llm, "fixture", invalid)
    with pytest.raises(LLMUnavailable, match="after retry"):
        complete_json("planner", "system", "data", Plan, provider="mock")
    assert len(calls) == 2


def test_offline_ignores_keys_and_provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-test-key")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    settings = load_config(offline=True, project_root=tmp_path)
    assert settings.llm_provider == "none"
    assert settings.target_dir == Path("../ecommerce-microservices")
    assert settings.compose_project == "ecommerce-microservices"
    assert "fake-test-key" not in repr(settings)


def test_provider_defaults_and_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    assert load_config(project_root=tmp_path).llm_provider == "none"
    monkeypatch.setenv("GOOGLE_API_KEY", "fake-test-key")
    assert load_config(project_root=tmp_path).llm_provider == "gemini"
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("COMPOSE_PROJECT", "isolated-project")
    assert load_config(project_root=tmp_path).llm_provider == "mock"
    assert load_config(project_root=tmp_path).compose_project == "isolated-project"
