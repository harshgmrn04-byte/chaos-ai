"""Bundled, synthetic Phase 1 data; never target measurements."""

from importlib.resources import files
from typing import TypeVar

from pydantic import TypeAdapter

T = TypeVar("T")


def fixture(name: str, model_type: type[T]) -> T:
    contents = files("chaosai").joinpath("fixtures", f"{name}.json").read_text(encoding="utf-8")
    return TypeAdapter(model_type).validate_json(contents)
