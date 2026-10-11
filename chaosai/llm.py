"""Synchronous, validated LLM data transport; failures never control execution."""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, TypeVar

from google import genai
from google.genai import types
from pydantic import BaseModel, ValidationError

from chaosai.config import Settings, load_config
from chaosai.fixture_data import fixture
from chaosai.models import LLMProbe, Plan, PlannerOutput, ResolvedModel
from chaosai.runs import read_json, write_json

M = TypeVar("M", bound=BaseModel)
TASK_FIXTURES = {"planner": "plan", "analyzer": "findings"}
logger = logging.getLogger(__name__)


class LLMUnavailable(RuntimeError):
    """A safe diagnostic; the caller must use its deterministic fallback."""


def rank_models(models: Iterable[Any]) -> list[str]:
    """Rank supported text models without hardcoded model IDs or network access."""
    ranked: dict[str, tuple[Any, ...]] = {}
    for model in models:
        name = getattr(model, "name", None)
        actions = getattr(model, "supported_actions", None) or []
        if not name or "generateContent" not in actions:
            continue
        short = name.removeprefix("models/").lower()
        if any(
            word in short
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
        ):
            continue
        family = (
            0
            if "flash" in short and "lite" not in short
            else (1 if "flash" in short and "lite" in short else 2 if "pro" in short else 3)
        )
        if family == 3 or not re.fullmatch(r"[\w./-]+", name):
            continue
        match = re.search(r"gemini-(\d+(?:\.\d+)*)", short)
        version = tuple(int(v) for v in match[1].split(".")) if match else (0,)
        version = (version + (0, 0, 0))[:3]
        ranked[name] = (
            "preview" in short or "experimental" in short or "-exp" in short,
            family,
            tuple(-part for part in version),
            name,
        )
    return sorted(ranked, key=ranked.__getitem__)


def response_key(task: str, system: str, user: str) -> str:
    return hashlib.sha256(json.dumps([task, system, user]).encode()).hexdigest()


def mock_response(task: str, model_cls: type[M]) -> str:
    """Serve the original fixtures and the flat planner contract without transport."""
    if task == "probe":
        return '{"ok":true}'
    name = TASK_FIXTURES.get(task)
    if name is None:
        raise LLMUnavailable("No mock fixture for this task.")
    if task == "planner" and model_cls is PlannerOutput:
        specs = fixture("plan", Plan).root
        return json.dumps(
            {
                "experiments": [
                    dict(
                        fault=s.fault,
                        target=s.target,
                        duration_s=s.duration_s,
                        intensity=s.intensity,
                        journey=s.hypothesis.journey,
                        max_error_rate=s.hypothesis.max_error_rate,
                        max_p95_multiple=s.hypothesis.max_p95_multiple,
                        statement=s.hypothesis.statement,
                        abort_max_error_rate=s.abort.max_error_rate,
                        abort_max_p95_multiple=s.abort.max_p95_multiple,
                        reason=s.reason,
                        rank=s.rank,
                    )
                    for s in specs
                ]
            }
        )
    return fixture(name, model_cls).model_dump_json()


def error_code(exc: Exception) -> int | None:
    code = getattr(exc, "code", None)
    return code if isinstance(code, int) else None


def schema_rejected(exc: Exception) -> bool:
    if isinstance(exc, (ValueError, TypeError)):
        return True
    # Inspect only to classify; never expose SDK payloads, prompts or credentials.
    return error_code(exc) == 400 and any(
        word in str(getattr(exc, "message", "")).lower()
        for word in ("schema", "response_schema", "responseschema")
    )


class LLM:
    """One planner/doctor session with bounded retries and owned-client cleanup."""

    def __init__(
        self,
        settings: Settings,
        *,
        cache_dir: Path | None = None,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self.provider = settings.llm_provider
        self.cache_dir = cache_dir
        self.client = client
        self.sleep = sleep
        self.model: str | None = None
        self._owns_client = False
        self._json_only = False
        self._excluded: set[str] = set()

    @property
    def source(self) -> str:
        return (
            f"gemini:{self.model}"
            if self.provider == "gemini" and self.model
            else ("mock" if self.provider == "mock" else "heuristic")
        )

    def close(self) -> None:
        if self._owns_client and self.client is not None:
            try:
                self.client.close()
            except Exception:
                logger.warning("LLM client cleanup failed.")
            finally:
                self.client = None
                self._owns_client = False

    def _connect(self) -> None:
        if self.client is None:
            key = self.settings.llm_key
            if key is None:
                raise LLMUnavailable("Gemini key missing; set GEMINI_API_KEY or GOOGLE_API_KEY.")
            self.client = genai.Client(
                api_key=key.get_secret_value(),
                http_options=types.HttpOptions(
                    timeout=int(self.settings.llm_timeout_s * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
            self._owns_client = True

    def _backoff(self, call: Callable[[], Any]) -> Any:
        for attempt in range(4):
            try:
                return call()
            except Exception as exc:
                if error_code(exc) not in {429, 503} or attempt == 3:
                    raise
                self.sleep(2 ** (attempt + 1))
        raise AssertionError("unreachable")

    def _generate(self, model: str, system: str, user: str, model_cls: type[M]) -> str:
        def send(with_schema: bool) -> str:
            content = user
            config: dict[str, Any] = {
                "system_instruction": system,
                "response_mime_type": "application/json",
                "temperature": 0,
            }
            if with_schema:
                config["response_schema"] = model_cls
            else:
                content += "\nRequired JSON schema:\n" + json.dumps(model_cls.model_json_schema())
            response = self._backoff(
                lambda: self.client.models.generate_content(
                    model=model,
                    contents=content,
                    config=config,
                )
            )
            return response.text or ""

        if self._json_only:
            return send(False)
        try:
            return send(True)
        except Exception as exc:
            if not schema_rejected(exc):
                raise
            self._json_only = True
            return send(False)

    def _cached_model(self) -> str | None:
        try:
            return read_json(self.settings.llm_model_path, ResolvedModel).model
        except (OSError, ValueError):
            return None

    def resolve_model(self, *, refresh: bool = False) -> str:
        self._connect()
        override = self.settings.gemini_model
        if override:
            if not re.fullmatch(r"[\w./-]+", override):
                raise LLMUnavailable("Invalid GEMINI_MODEL; use a model ID from models.list().")
            candidates = [override]
        elif not refresh and (cached := self._cached_model()) and cached not in self._excluded:
            self.model = cached
            return cached
        else:
            models = self._backoff(lambda: list(self.client.models.list()))
            candidates = [name for name in rank_models(models) if name not in self._excluded][:4]
        for name in candidates:
            try:
                raw = self._generate(name, "Return only JSON.", 'Return {"ok":true}.', LLMProbe)
                if self.cache_dir is not None:
                    key = response_key("model-probe", name, '{"ok":true}')
                    write_json(self.cache_dir / f"model-probe-{key}.json", {"response": raw})
                probe = LLMProbe.model_validate_json(raw)
                if not probe.ok:
                    continue
            except Exception as exc:
                if error_code(exc) in {403, 404}:
                    self._excluded.add(name)
                    continue
                if isinstance(exc, ValidationError):
                    continue
                raise
            self.model = name
            write_json(self.settings.llm_model_path, ResolvedModel(model=name))
            return name
        raise LLMUnavailable("No accessible text model; check GEMINI_MODEL and key permissions.")

    def complete_json(self, task: str, system: str, user: str, model_cls: type[M]) -> M:
        if self.provider == "none":
            raise LLMUnavailable("LLM disabled; deterministic fallback selected.")
        if not re.fullmatch(r"[\w-]+", task):
            raise LLMUnavailable("Invalid LLM task name.")
        path = (
            self.cache_dir / f"{task}-{response_key(task, system, user)}.json"
            if self.cache_dir is not None
            else None
        )
        # Run-local validated caches avoid repeated paid requests. None never reads caches.
        if path is not None and path.exists():
            try:
                cached = read_json(path, model_cls)
                if self.provider == "gemini":
                    self.model = self.settings.gemini_model or self._cached_model()
                    if self.model is None:
                        self.resolve_model()
                return cached
            except (OSError, ValueError):
                pass
            except LLMUnavailable:
                raise
            except Exception as exc:
                raise LLMUnavailable("Cached response model unavailable; use fallback.") from exc
        refreshed = False
        try:
            if self.provider == "gemini" and self.model is None:
                self.resolve_model()
            for attempt in range(2):
                try:
                    if self.provider == "mock":
                        raw = mock_response(task, model_cls)
                    else:
                        while True:
                            try:
                                raw = self._generate(self.model, system, user, model_cls)
                                break
                            except Exception as exc:
                                if error_code(exc) != 404 or refreshed:
                                    raise
                                refreshed = True
                                self._excluded.add(self.model)
                                self.model = None
                                self.resolve_model(refresh=True)
                    result = model_cls.model_validate_json(raw)
                    if path is not None:
                        write_json(path, result)
                    return result
                except (ValidationError, ValueError) as exc:
                    if path is not None and "raw" in locals():
                        write_json(
                            path.with_stem(path.stem + f"-invalid-{attempt + 1}"), {"response": raw}
                        )
                    if attempt == 1:
                        raise LLMUnavailable("Invalid LLM JSON after retry; use fallback.") from exc
        except LLMUnavailable:
            raise
        except Exception as exc:
            code = error_code(exc)
            hint = {
                400: "Check the key, GEMINI_MODEL and JSON schema support.",
                401: "Check Gemini key authentication.",
                403: "Check model/key permissions.",
                404: "Model unavailable after re-resolution; check GEMINI_MODEL.",
                429: "Gemini quota exhausted after retries; retry later.",
                503: "Gemini unavailable after retries; retry later.",
            }.get(code, "LLM transport or cache unavailable; check configuration/connectivity.")
            raise LLMUnavailable(hint) from exc
        raise LLMUnavailable("LLM output unavailable; use fallback.")


def complete_json(
    task: str,
    system: str,
    user: str,
    model_cls: type[M],
    *,
    provider: str | None = None,
    cache_dir: Path | None = None,
    settings: Settings | None = None,
) -> M:
    """Shared entry point; explicit offline/mock callers never load dotenv."""
    settings = settings or load_config(offline=provider is not None, dotenv=False)
    if provider is not None:
        settings = settings.model_copy(update={"llm_provider": provider})
    session = LLM(settings, cache_dir=cache_dir)
    try:
        return session.complete_json(task, system, user, model_cls)
    finally:
        session.close()
