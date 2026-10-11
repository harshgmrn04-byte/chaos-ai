"""Self-contained reports, plan ordering, provenance and untrusted HTML escaping."""

import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from chaosai.approval import Approver
from chaosai.config import Settings
from chaosai.models import (
    Baseline,
    Clamp,
    ExperimentResult,
    Finding,
    Plan,
    PolicyDecision,
    RunMeta,
    Sample,
)
from chaosai.report import render_report, timeline
from chaosai.runner import run_pipeline
from chaosai.runs import read_json, write_json


@pytest.fixture
def directory(settings: Settings) -> Path:
    return run_pipeline(settings, Approver(yes=True, is_tty=False, ask=input), offline=True)


class AssetParser(HTMLParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        assert tag not in {"script", "iframe", "link", "img"}
        for name, value in attrs:
            assert name not in {"src", "href"}
            assert not name.startswith("on")


def test_complete_sections_self_contained_and_rank_order(directory: Path) -> None:
    html = render_report(directory).read_text()
    AssetParser().feed(html)
    for label in [
        "Summary",
        "Ranked weaknesses",
        "What held up",
        "Top fixes",
        "Application map",
        "Baseline thresholds",
        "Decision trace",
        "Scores and breakdown",
        "Timeline sparkline",
        "Findings and fixes",
        "Methodology",
    ]:
        assert label in html
    assert "<style>" in html and "<svg" in html
    assert (
        html.index("1. redis-stop") < html.index("2. postgres-pause") < html.index("3. product-cpu")
    )
    weakness_section = html.split("<h2>Ranked weaknesses</h2>")[1].split("<h2>What held up</h2>")[0]
    assert "product-cpu" not in weakness_section
    assert "product-cpu" in html.split("<h2>What held up</h2>")[1]
    markdown = (directory / "report.md").read_text()
    assert "Methodology" in markdown and "Decision trace" in markdown and "E=" in markdown


def test_all_untrusted_fields_escaped(directory: Path) -> None:
    payload = '<script>alert("injected")</script><img src="https://evil.invalid" onerror="x">'
    findings = read_json(directory / "findings.json", list[Finding])
    findings[0].summary = findings[0].root_cause = payload
    findings[0].fixes.append(payload)
    findings[0].evidence.append(payload)
    write_json(directory / "findings.json", findings)
    path = directory / "experiments/redis-stop/result.json"
    result = read_json(path, ExperimentResult)
    result.log_excerpts = {payload: [payload]}
    result.events[0].note = payload
    write_json(path, result)
    decisions = read_json(directory / "decisions.json", list[PolicyDecision])
    decisions[0].requested.reason = payload
    decisions[0].clamps.append(
        Clamp(field="reason", requested=payload, final=payload, reason=payload)
    )
    write_json(directory / "decisions.json", decisions)
    plan = read_json(directory / "plan.json", Plan)
    plan.root[0].hypothesis.statement = payload
    # Result's final spec is authoritative for its card.
    result.spec.hypothesis.statement = payload
    write_json(path, result)
    write_json(directory / "plan.json", plan)
    baseline = read_json(directory / "baseline.json", Baseline)
    baseline.journeys[payload] = next(iter(baseline.journeys.values()))
    write_json(directory / "baseline.json", baseline)
    html = render_report(directory).read_text()
    AssetParser().feed(html)
    assert payload not in html and "&lt;script&gt;" in html and "&lt;img" in html
    assert payload not in (directory / "report.md").read_text()


@pytest.mark.parametrize(
    "source,offline,banner",
    [
        ("fixture", False, True),
        ("fixture", True, True),
        ("measured", False, False),
        ("measured", True, False),
    ],
)
def test_banner_follows_data_source_not_offline(
    directory: Path, source: str, offline: bool, banner: bool
) -> None:
    meta = read_json(directory / "run.json", RunMeta)
    meta.data_source, meta.offline = source, offline
    write_json(directory / "run.json", meta)
    html = render_report(directory).read_text()
    assert ("Simulation — fixture data only." in html) == banner
    assert ("Measured evidence." in html) == (not banner)


def test_legacy_measured_run_has_no_fixture_banner(directory: Path) -> None:
    meta = json.loads((directory / "run.json").read_text())
    meta.pop("data_source")
    meta["offline"] = False
    meta["baseline_source"] = "measured"
    write_json(directory / "run.json", meta)
    assert "Simulation — fixture data only." not in render_report(directory).read_text()
    assert "data_source" not in json.loads((directory / "run.json").read_text())


def test_missing_sample_artifact_not_fabricated(directory: Path) -> None:
    for path in (directory / "experiments").glob("*/samples.jsonl"):
        path.unlink()
    assert "No sample timeline available." in render_report(directory).read_text()


def test_sample_path_cannot_escape_run(directory: Path) -> None:
    path = directory / "experiments/redis-stop/result.json"
    result = read_json(path, ExperimentResult)
    result.samples_path = "../elsewhere.jsonl"
    write_json(path, result)
    with pytest.raises(ValueError, match="inside its run"):
        render_report(directory)


def test_report_symlink_cannot_write_outside_run(directory: Path, settings: Settings) -> None:
    external = settings.project_root / "outside.html"
    external.write_text("untouched")
    (directory / "report.html").unlink()
    (directory / "report.html").symlink_to(external)
    with pytest.raises(ValueError, match="inside its run"):
        render_report(directory)
    assert external.read_text() == "untouched"


def test_sparse_and_partial_run_limits_visible(directory: Path) -> None:
    html = render_report(directory).read_text()
    assert "sparse/inconclusive evidence are descriptive" in html
    assert "partial run is not a full application assessment" in html
    assert "Container rollback and application" not in html  # fixture provenance


def test_timeline_bounded_handles_empty_and_equal_timestamps() -> None:
    assert timeline([]) == []
    samples = [
        Sample(t=i, journey="browse", ok=True, status=200, latency_ms=i, error=None)
        for i in range(1000)
    ]
    points = timeline(samples)
    assert len(points) <= 100 and all(10 <= x <= 490 and 10 <= y <= 90 for x, y in points)
    samples = [s.model_copy(update={"t": 1, "latency_ms": 0}) for s in samples]
    assert timeline(samples) == [(10, 90)]


def test_plan_rank_is_authoritative_for_cards(directory: Path) -> None:
    path = directory / "experiments/redis-stop/result.json"
    result = read_json(path, ExperimentResult)
    result.spec.rank = 20
    write_json(path, result)
    html = render_report(directory).read_text()
    assert html.index("1. redis-stop") < html.index("2. postgres-pause")


def test_ambiguous_legacy_defaults_to_fixture(directory: Path) -> None:
    meta = json.loads((directory / "run.json").read_text())
    meta.pop("data_source")
    meta["offline"] = False
    meta["baseline_source"] = None
    write_json(directory / "run.json", meta)
    assert "Simulation — fixture data only." in render_report(directory).read_text()


def test_chart_labels_recorded_event_bounds_and_readable_times(directory: Path) -> None:
    from chaosai.models import Event
    from chaosai.report import chart_context, readable_time

    samples = [
        Sample(t=t, journey="browse", ok=True, status=200, latency_ms=20, error=None)
        for t in (10, 20)
    ]
    events = [
        Event(t=t, phase=phase, note="recorded")
        for t, phase in [(5, "PRECHECK"), (15, "OBSERVE"), (21, "UNDO")]
    ]
    chart = chart_context(samples, events)
    assert chart["span"] == 10 and chart["maximum"] == 20
    assert [(m["x"], m["elapsed"], m["event"].phase) for m in chart["markers"]] == [
        (300, 5, "OBSERVE")
    ]
    assert chart_context([], events) == {}
    assert readable_time(0) == "1970-01-01 00:00:00.000+00:00"
    assert readable_time(1e100) == "Timestamp outside supported date range"
    html = render_report(directory).read_text()
    for label in (
        "Mean latency (ms)",
        "Elapsed time (s)",
        "p95 absolute increment",
        "Minimum samples / consecutive breaches",
        "touches:",
        "+00:00",
    ):
        assert label in html
    assert "INJECT is an attempt" in html
    assert "Baseline thresholds</h3><pre>" not in html
    assert timeline(samples) == [(10, 10), (490, 10)]
