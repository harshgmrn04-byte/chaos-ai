"""Timestamped run directories and validated artifact IO."""

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, TypeVar

from pydantic import ConfigDict, TypeAdapter

T = TypeVar("T")
JSON_DATA = TypeAdapter(Any, config=ConfigDict(ser_json_inf_nan="constants"))


def create_run(runs_dir: Path, *, now: datetime | None = None) -> Path:
    timestamp = now or datetime.now(UTC)
    runs_dir.mkdir(parents=True, exist_ok=True)
    while True:
        run_dir = runs_dir / timestamp.strftime("%Y%m%d-%H%M%S")
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            timestamp += timedelta(seconds=1)
    (run_dir / "experiments").mkdir()
    (run_dir / "llm").mkdir()
    return run_dir


def write_json(path: Path, value: Any) -> None:
    """Replace JSON atomically after flushing to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = JSON_DATA.dump_python(value, mode="json")
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            json.dump(payload, f, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def read_json(path: Path, model_type: type[T]) -> T:
    return TypeAdapter(model_type).validate_json(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, values: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for value in values:
            payload = JSON_DATA.dump_python(value, mode="json")
            f.write(json.dumps(payload, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_jsonl(path: Path, model_type: type[T]) -> list[T]:
    adapter = TypeAdapter(model_type)
    with path.open(encoding="utf-8") as f:
        return [adapter.validate_json(line) for line in f if line.strip()]
