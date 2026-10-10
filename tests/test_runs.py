from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from chaosai.fixture_data import fixture
from chaosai.models import Graph, Sample
from chaosai.runs import append_jsonl, create_run, read_json, read_jsonl, write_json


def test_run_creation_collision_preserves_existing_artifacts(tmp_path: Path) -> None:
    now = datetime(2026, 10, 11, 8, tzinfo=UTC)
    first = create_run(tmp_path / "runs", now=now)
    write_json(first / "run.json", {"keep": True})
    second = create_run(tmp_path / "runs", now=now)
    assert first.name == "20261011-080000"
    assert second.name == "20261011-080001"
    assert (second / "experiments").is_dir() and (second / "llm").is_dir()
    assert read_json(first / "run.json", dict[str, bool]) == {"keep": True}


def test_json_models_and_lists(tmp_path: Path) -> None:
    graph = fixture("graph", Graph)
    path = tmp_path / "nested" / "graph.json"
    write_json(path, graph)
    assert read_json(path, Graph) == graph
    write_json(path, [graph, graph])
    assert read_json(path, list[Graph]) == [graph, graph]
    assert list(path.parent.iterdir()) == [path]


def test_failed_json_replace_preserves_old_file(tmp_path: Path) -> None:
    path = tmp_path / "data.json"
    write_json(path, {"keep": 1})
    with pytest.raises(ValueError):
        write_json(path, {"invalid": float("nan")})
    assert read_json(path, dict[str, int]) == {"keep": 1}
    assert list(tmp_path.iterdir()) == [path]


def test_jsonl_append_and_validate(tmp_path: Path) -> None:
    samples = fixture("samples-redis-stop", list[Sample])[:3]
    path = tmp_path / "samples.jsonl"
    append_jsonl(path, samples[:1])
    append_jsonl(path, samples[1:])
    assert read_jsonl(path, Sample) == samples
    path.write_text('{"t": "bad"}\n', encoding="utf-8")
    with pytest.raises(ValidationError):
        read_jsonl(path, Sample)
