"""Warm-up, measured baselines and deterministic threshold derivation."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.load import JOURNEYS, LoadGenerator, SampleBuffer
from chaosai.metrics import (
    ContainerStatsPoller,
    QueueDepthPoller,
    container_baseline,
    probes,
    queue_baseline,
    stats_by_journey,
    window_stats,
)
from chaosai.models import (
    AbortThresholds,
    Baseline,
    Graph,
    HypothesisThresholds,
    JourneyStats,
    Sample,
    Thresholds,
)
from chaosai.runs import write_json
from chaosai.target import TargetError, get_docker_client

logger = logging.getLogger(__name__)


def derive_thresholds(baseline: Baseline | JourneyStats) -> Thresholds:
    """Use aggregate request errors; explicit 'all' prevents double-counting."""
    if isinstance(baseline, JourneyStats):
        error_rate = baseline.error_rate
    elif "all" in baseline.journeys:
        error_rate = baseline.journeys["all"].error_rate
    else:
        n = sum(s.n for s in baseline.journeys.values())
        error_rate = sum(s.n * s.error_rate for s in baseline.journeys.values()) / n if n else 0
    return Thresholds(
        hypothesis=HypothesisThresholds(max_error_rate=error_rate + 0.02, p95_multiple=2.0),
        abort=AbortThresholds(
            max_error_rate=0.5,
            p95_multiple=10,
            max_unhealthy_containers=3,
            min_samples=10,
            consecutive_breaches=2,
        ),
    )


def stability_check(
    samples: list[Sample],
    t0: float,
    t1: float,
    journeys: tuple[str, ...] = JOURNEYS,
) -> bool:
    """Every journey needs both halves; |second-first|/first must be <=30%."""
    mid = (t0 + t1) / 2
    first = stats_by_journey(samples, t0, mid, journeys)
    second = stats_by_journey(samples, mid, t1, journeys)
    for name in first.keys() | second.keys():
        a, b = first.get(name), second.get(name)
        if a is None or b is None or a.n == 0 or b.n == 0:
            return False
        if a.p95_ms == 0:
            if b.p95_ms != 0:
                return False
        elif abs(b.p95_ms - a.p95_ms) / a.p95_ms > 0.3 + 1e-12:
            return False
    return True


def build_baseline(samples: list[Sample], t0: float, t1: float) -> Baseline:
    aggregate = window_stats(samples, t0, t1)
    return Baseline(
        created_at=datetime.fromtimestamp(t1, UTC),
        window_s=t1 - t0,
        stable=stability_check(samples, t0, t1),
        journeys={**stats_by_journey(samples, t0, t1, JOURNEYS), "all": aggregate},
        containers={},
        queues={},
        thresholds=derive_thresholds(aggregate),
    )


def collect_window(
    generator: LoadGenerator,
    workers: int,
    warmup_s: float,
    duration_s: float,
    *,
    wait: Callable[[float], None] = time.sleep,
) -> tuple[float, float]:
    """Keep load running until caller stops it; timestamp windows in epoch seconds."""
    if warmup_s < 0 or duration_s <= 0:
        raise ValueError("Warm-up must be nonnegative and duration positive")
    generator.samples.begin_window(float("inf"))
    generator.set_workers(workers)
    logger.info("Warming load for %.1fs; samples discarded.", warmup_s)
    wait(warmup_s)
    t0 = time.time()
    generator.samples.begin_window(t0, t0 + duration_s)
    logger.info("Measuring load for %.1fs.", duration_s)
    wait(duration_s)
    return t0, t0 + duration_s


def measure_baseline(
    graph: Graph,
    settings: Settings,
    run_dir: Path,
    *,
    offline: bool = False,
    duration_s: float | None = None,
    quick: bool = False,
) -> Baseline:
    if offline:
        baseline = fixture("baseline", Baseline)
        write_json(run_dir / "baseline.json", baseline)
        return baseline
    duration = (
        settings.quick_duration_s
        if quick
        else duration_s
        if duration_s is not None
        else settings.baseline_duration_s
    )
    warmup = settings.quick_warmup_s if quick else settings.warmup_s
    if duration_s is not None and duration_s <= 0:
        raise TargetError("Duration must be positive.")
    docker_client = get_docker_client()
    generator = LoadGenerator(settings, docker_client, SampleBuffer(run_dir / "samples.jsonl"))
    containers = ContainerStatsPoller(docker_client, settings, run_dir / "containers.jsonl")
    queues = QueueDepthPoller(settings, run_dir / "queues.jsonl")
    try:
        generator.prepare(settings.load_users)
        readiness = probes(graph, settings, docker_client)
        write_json(run_dir / "probes.json", readiness)
        if not all(readiness.values()):
            logger.warning("Some readiness probes failed; baseline may be unhealthy.")
        containers.start()
        queues.start()
        t0, t1 = collect_window(generator, settings.load_users, warmup, duration)
    finally:
        try:
            generator.stop()
            containers.stop()
            queues.stop()
        finally:
            docker_client.close()
    if generator.failed.is_set():
        raise TargetError("A load worker failed; baseline incomplete. Inspect disk/configuration.")
    baseline = build_baseline(generator.samples.snapshot(), t0, t1)
    baseline.containers = container_baseline(containers.snapshot(), t0, t1)
    baseline.queues = queue_baseline(queues.snapshot(), t0, t1)
    if any(stats.n == 0 for stats in baseline.journeys.values()):
        logger.warning("A journey has no samples; stability cannot be established.")
    write_json(run_dir / "baseline.json", baseline)
    write_json(settings.project_root / ".chaosai" / "baseline.json", baseline)
    return baseline


def run_load(
    settings: Settings, run_dir: Path, users: int, duration_s: float
) -> dict[str, JourneyStats]:
    if duration_s is not None and duration_s <= 0:
        raise TargetError("Duration must be positive.")
    docker_client = get_docker_client()
    generator = LoadGenerator(settings, docker_client, SampleBuffer(run_dir / "samples.jsonl"))
    try:
        generator.prepare(users)
        t0 = time.time()
        generator.samples.begin_window(t0, t0 + duration_s)
        generator.set_workers(users)
        threading.Event().wait(duration_s)
        t1 = t0 + duration_s
    finally:
        try:
            generator.stop()
        finally:
            docker_client.close()
    if generator.failed.is_set():
        raise TargetError("A load worker failed; load evidence incomplete.")
    stats = stats_by_journey(generator.samples.snapshot(), t0, t1, JOURNEYS)
    write_json(run_dir / "load.json", stats)
    return stats
