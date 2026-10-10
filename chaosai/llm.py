"""Validated mock/none interface. Gemini transport arrives in Phase 7."""

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from chaosai.fixture_data import fixture
from chaosai.runs import write_json

M = TypeVar("M", bound=BaseModel)
TASK_FIXTURES = {"planner": "plan", "analyzer": "findings"}


class LLMUnavailable(RuntimeError):
    """The caller must use its deterministic fallback."""


def complete_json(
    task: str,
    system: str,
    user: str,
    model_cls: type[M],
    *,
    provider: str = "none",
    cache_dir: Path | None = None,
) -> M:
    if provider != "mock":
        raise LLMUnavailable("Provider unavailable in Phase 1; using fixture fallback.")
    fixture_name = TASK_FIXTURES.get(task)
    if fixture_name is None:
        raise LLMUnavailable(f"No mock fixture for task {task!r}.")
    for attempt in range(2):
        try:
            response = fixture(fixture_name, model_cls)
            # Validate again at the interface boundary, as future transports must do.
            response = model_cls.model_validate_json(response.model_dump_json())
            break
        except (ValidationError, ValueError, OSError) as exc:
            if attempt == 1:
                raise LLMUnavailable("Invalid mock JSON after retry.") from exc
    if cache_dir is not None:
        key = hashlib.sha256(json.dumps([task, system, user]).encode()).hexdigest()
        write_json(cache_dir / f"{task}-{key}.json", response)
    return response
