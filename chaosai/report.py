"""Self-contained, escaped HTML/Markdown from saved evidence; no target access."""

import json
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from chaosai.models import (
    Baseline,
    Event,
    ExperimentResult,
    Finding,
    Graph,
    Plan,
    PolicyDecision,
    RunMeta,
    Sample,
    Score,
    Summary,
)
from chaosai.runs import read_json, read_jsonl
from chaosai.scoring import held_up, ranked_summary, severity


def timeline(samples: list[Sample]) -> list[tuple[float, float]]:
    """At most 100 time buckets, plotting mean latency including timed-out requests."""
    if not samples:
        return []
    start, end = min(s.t for s in samples), max(s.t for s in samples)
    groups: dict[int, list[float]] = {}
    for sample in samples:
        bucket = min(99, int(99 * (sample.t - start) / (end - start))) if end > start else 0
        groups.setdefault(bucket, []).append(sample.latency_ms)
    values = {b: sum(v) / len(v) for b, v in groups.items()}
    maximum = max(max(values.values()), 1)
    return [
        (round(10 + b * 480 / 99, 2), round(90 - 80 * v / maximum, 2))
        for b, v in sorted(values.items())
    ]


def readable_time(value: datetime | float) -> str:
    """Use explicit UTC and milliseconds consistently for metadata and epoch events."""
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromtimestamp(value, UTC)
    except (ValueError, OverflowError, OSError):
        return "Timestamp outside supported date range"
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).isoformat(timespec="milliseconds").replace("T", " ")


def chart_context(samples: list[Sample], events: list[Event]) -> dict[str, object]:
    """Labels share the sparkline's bucket scale; markers use recorded events only."""
    if not samples:
        return {}
    start, end = min(s.t for s in samples), max(s.t for s in samples)
    groups: dict[int, list[float]] = {}
    for sample in samples:
        bucket = min(99, int(99 * (sample.t - start) / (end - start))) if end > start else 0
        groups.setdefault(bucket, []).append(sample.latency_ms)
    maximum = max(max(sum(v) / len(v) for v in groups.values()), 1)
    markers = [
        {
            "x": round(60 + 480 * (e.t - start) / (end - start), 2),
            "elapsed": round(e.t - start, 3),
            "event": e,
        }
        for e in events
        if end > start
        and start <= e.t <= end
        and e.phase in {"PRECHECK", "INJECT", "OBSERVE", "UNDO", "RECOVER", "RESULT"}
    ]
    return {"start": start, "span": end - start, "maximum": maximum, "markers": markers}


def report_context(run_dir: Path) -> dict[str, object]:
    meta = read_json(run_dir / "run.json", RunMeta)
    raw = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if (
        "data_source" not in raw
        and not meta.offline
        and meta.baseline_source in {"measured", "reused"}
    ):
        meta.data_source = "measured"
    baseline = read_json(run_dir / "baseline.json", Baseline)
    plan = read_json(run_dir / "plan.json", Plan)
    results = sorted(
        [read_json(p, ExperimentResult) for p in (run_dir / "experiments").glob("*/result.json")],
        key=lambda r: r.spec.rank,
    )
    scores = read_json(run_dir / "scores.json", list[Score])
    findings = read_json(run_dir / "findings.json", list[Finding])
    by_score = {s.experiment_id: s for s in scores}
    by_result = {r.spec.id: r for r in results}
    if set(by_score) - set(by_result):
        raise ValueError("Stored score has no corresponding experiment result.")
    for finding in findings:
        if finding.experiment_id in by_score and finding.experiment_id in by_result:
            finding.severity = severity(
                by_score[finding.experiment_id], by_result[finding.experiment_id]
            )
    summary = ranked_summary(
        results, scores, findings, read_json(run_dir / "summary.json", Summary)
    )
    by_finding = {f.experiment_id: f for f in findings}
    specs = {s.id: s for s in plan.root}
    for result in results:
        specs.setdefault(result.spec.id, result.spec)
    cards = []
    for spec in sorted(specs.values(), key=lambda s: s.rank):
        result = by_result.get(spec.id)
        points: list[tuple[float, float]] = []
        chart: dict[str, object] = {}
        if result:
            path = (run_dir / result.samples_path).resolve()
            if not path.is_relative_to(run_dir.resolve()):
                raise ValueError("Sample artifact must remain inside its run directory.")
            if path.exists():
                samples = read_jsonl(path, Sample)
                points = timeline(samples)
                chart = chart_context(samples, result.events)
        cards.append(
            {
                "spec": spec,
                "result": result,
                "score": by_score.get(spec.id),
                "finding": by_finding.get(spec.id),
                "points": points,
                "chart": chart,
            }
        )
    decision_path = run_dir / "decisions.json"
    return {
        "meta": meta,
        "graph": read_json(run_dir / "graph.json", Graph),
        "baseline": baseline,
        "summary": summary,
        "scores": scores,
        "findings": findings,
        "cards": cards,
        "held": [
            by_finding[s.experiment_id]
            for s in scores
            if s.experiment_id in by_finding and held_up(s, by_result[s.experiment_id])
        ],
        "decisions": read_json(decision_path, list[PolicyDecision])
        if decision_path.exists()
        else [],
        "approvals": {a.experiment_id: a.decision for a in meta.approvals},
    }


def render_report(run_dir: Path) -> Path:
    context = report_context(run_dir)
    environment = Environment(
        loader=FileSystemLoader(Path(__file__).parent / "templates"),
        autoescape=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["readable_time"] = readable_time
    for name in ("report.html", "report.md"):
        if not (run_dir / name).resolve().is_relative_to(run_dir.resolve()):
            raise ValueError("Report output must remain inside its run directory.")
        (run_dir / name).write_text(
            environment.get_template(name + ".j2").render(**context), encoding="utf-8"
        )
    return run_dir / "report.html"
