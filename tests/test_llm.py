"""All Gemini paths use a fake synchronous SDK; no network or real keys."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from google.genai import types

from chaosai import llm
from chaosai.config import Settings
from chaosai.llm import LLM, LLMUnavailable, complete_json, rank_models
from chaosai.models import LLMProbe, PlannerOutput, ResolvedModel
from chaosai.runs import read_json, write_json


class FakeError(RuntimeError):
    def __init__(self, code: int, message: str = "secret-bearing SDK payload") -> None:
        self.code, self.message = code, message
        super().__init__(message)


def model(name: str, *, supported: bool = True) -> types.Model:
    return types.Model(name=name, supported_actions=["generateContent"] if supported else [])


def transport(settings: Settings, responses: list[object], names: list[str] | None = None) -> LLM:
    client = SimpleNamespace(
        models=SimpleNamespace(
            list=Mock(return_value=[model(n) for n in names or ["models/gemini-9-flash"]]),
            generate_content=Mock(
                side_effect=[
                    SimpleNamespace(text=r) if isinstance(r, str) else r for r in responses
                ]
            ),
        )
    )
    return LLM(settings.model_copy(update={"llm_provider": "gemini"}), client=client, sleep=Mock())


def test_ranking_filters_and_orders_families_versions_preview() -> None:
    names = [
        "models/gemini-2.9-flash",
        "models/gemini-2.10-flash",
        "models/gemini-7-flash-preview",
        "models/gemini-9-flash-lite",
        "models/gemini-10-pro",
        "models/gemini-12-pro-preview",
        *[
            f"models/gemini-20-flash-{word}"
            for word in (
                "embedding",
                "imagen",
                "veo",
                "tts",
                "live",
                "image",
                "audio",
                "robotics",
                "gemma",
            )
        ],
    ]
    assert rank_models(
        [model(n) for n in reversed(names)] + [model("gemini-99-flash", supported=False)]
    ) == [names[i] for i in (1, 0, 3, 4, 2, 5)]


@pytest.mark.parametrize("code", [403, 404])
def test_resolution_falls_through_and_caches_winner(settings: Settings, code: int) -> None:
    session = transport(
        settings,
        [FakeError(code), '{"ok":true}', '{"ok":true}'],
        ["models/gemini-9-flash", "models/gemini-8-flash"],
    )
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    assert session.source == "gemini:models/gemini-8-flash"
    assert read_json(settings.llm_model_path, ResolvedModel).model == session.model
    assert session.client.models.list.call_count == 1
    assert [c.kwargs["model"] for c in session.client.models.generate_content.call_args_list] == [
        "models/gemini-9-flash",
        "models/gemini-8-flash",
        "models/gemini-8-flash",
    ]


def test_resolution_probes_at_most_four_candidates(settings: Settings) -> None:
    session = transport(
        settings, [FakeError(404)] * 4, [f"models/gemini-{n}-flash" for n in range(9, 3, -1)]
    )
    with pytest.raises(LLMUnavailable, match="No accessible"):
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert session.client.models.generate_content.call_count == 4


def test_cached_model_skips_listing_and_probe(settings: Settings) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="models/gemini-8-flash"))
    session = transport(settings, ['{"ok":true}'])
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    session.client.models.list.assert_not_called()
    assert session.client.models.generate_content.call_count == 1


def test_override_is_probed_without_listing(settings: Settings) -> None:
    settings.gemini_model = "chosen-by-human"
    session = transport(settings, ['{"ok":true}', '{"ok":true}'])
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    session.client.models.list.assert_not_called()
    assert session.source == "gemini:chosen-by-human"


def test_later_not_found_reresolves_once(settings: Settings) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="models/gemini-9-flash"))
    session = transport(
        settings,
        [FakeError(404), '{"ok":true}', FakeError(404)],
        ["models/gemini-9-flash", "models/gemini-8-flash"],
    )
    with pytest.raises(LLMUnavailable, match="re-resolution"):
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert session.client.models.list.call_count == 1
    assert session.client.models.generate_content.call_count == 3
    assert session.model == "models/gemini-8-flash"


@pytest.mark.parametrize("code", [429, 503])
def test_backoff_exact_delays_and_safe_failure(settings: Settings, code: int) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="cached"))
    session = transport(settings, [FakeError(code)] * 4)
    with pytest.raises(LLMUnavailable) as captured:
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert "secret-bearing" not in str(captured.value)
    assert [call.args[0] for call in session.sleep.call_args_list] == [2, 4, 8]
    assert session.client.models.generate_content.call_count == 4


def test_listing_backoff_then_success(settings: Settings) -> None:
    session = transport(settings, ['{"ok":true}', '{"ok":true}'])
    session.client.models.list.side_effect = [FakeError(503), [model("models/gemini-8-flash")]]
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    session.sleep.assert_called_once_with(2)


@pytest.mark.parametrize(
    "error", [ValueError("unsupported schema"), FakeError(400, "response_schema is unsupported")]
)
def test_schema_fallback_embeds_schema_and_validates(settings: Settings, error: Exception) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="cached"))
    session = transport(settings, [error, '{"ok":true}'])
    assert session.complete_json("probe", "sys", "quoted data", LLMProbe).ok
    calls = session.client.models.generate_content.call_args_list
    assert calls[0].kwargs["config"]["response_schema"] is LLMProbe
    assert "response_schema" not in calls[1].kwargs["config"]
    assert "Required JSON schema" in calls[1].kwargs["contents"]
    assert calls[1].kwargs["config"]["response_mime_type"] == "application/json"


def test_non_schema_400_does_not_retry_or_expose_payload(settings: Settings) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="cached"))
    session = transport(settings, [FakeError(400)])
    with pytest.raises(LLMUnavailable) as error:
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert "secret-bearing" not in str(error.value)
    assert session.client.models.generate_content.call_count == 1


@pytest.mark.parametrize("bad", ["not JSON", '{"ok":"nope"}', '{"ok":true,"extra":1}'])
def test_malformed_output_retries_once(settings: Settings, bad: str) -> None:
    write_json(settings.llm_model_path, ResolvedModel(model="cached"))
    session = transport(settings, [bad, bad])
    with pytest.raises(LLMUnavailable, match="after retry"):
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert session.client.models.generate_content.call_count == 2


def test_mock_retry_recovery_cache_and_hash_inputs(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Mock(side_effect=["bad", '{"ok":true}', '{"ok":true}', '{"ok":true}'])
    monkeypatch.setattr(llm, "mock_response", response)
    session = LLM(settings, cache_dir=settings.project_root / "llm")
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    assert response.call_count == 2
    session.complete_json("probe", "sys", "changed", LLMProbe)
    session.complete_json("probe", "changed", "data", LLMProbe)
    assert response.call_count == 4
    assert len(list(session.cache_dir.glob("*-invalid-1.json"))) == 1
    assert len(list(session.cache_dir.glob("*.json"))) == 4


def test_none_never_reads_cache_or_opens_transport(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm.genai, "Client", Mock(side_effect=AssertionError("transport")))
    settings.llm_provider = "none"
    session = LLM(settings, cache_dir=settings.project_root)
    with pytest.raises(LLMUnavailable, match="disabled"):
        session.complete_json("probe", "sys", "data", LLMProbe)
    assert not settings.llm_model_path.exists()


def test_default_provider_uses_environment_without_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    assert complete_json("probe", "sys", "data", LLMProbe).ok


def test_gemini_client_configuration_and_cleanup(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "fake-unit-key")
    session = transport(settings, ['{"ok":true}', '{"ok":true}'])
    fake = session.client
    factory = Mock(return_value=fake)
    fake.close = Mock()
    monkeypatch.setattr(llm.genai, "Client", factory)
    session.client = None
    assert session.complete_json("probe", "sys", "data", LLMProbe).ok
    options = factory.call_args.kwargs["http_options"]
    assert options.timeout == settings.llm_timeout_s * 1000
    assert options.retry_options.attempts == 1
    session.close()
    fake.close.assert_called_once()


def test_mock_flat_planner_contract() -> None:
    result = complete_json("planner", "sys", "data", PlannerOutput, provider="mock")
    assert len(result.experiments) == 3
    schema = json.dumps(PlannerOutput.model_json_schema())
    assert "additionalProperties" in schema
    assert "hypothesis" not in schema  # LLM-facing fields are flat.


def test_cached_response_resolution_failure_remains_unavailable(settings: Settings) -> None:
    session = transport(settings, [])
    session.cache_dir = settings.project_root / "llm"
    path = session.cache_dir / f"probe-{llm.response_key('probe', 'sys', 'data')}.json"
    write_json(path, LLMProbe(ok=True))
    session.client.models.list.side_effect = RuntimeError("secret transport payload")
    with pytest.raises(LLMUnavailable, match="Cached response model unavailable"):
        session.complete_json("probe", "sys", "data", LLMProbe)
