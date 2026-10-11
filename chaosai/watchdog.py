"""Pure S5 evaluation and a one-second live-buffer observer."""

import logging
import threading
import time
from typing import Protocol

from chaosai.metrics import latency_limit_ms, window_stats
from chaosai.models import (
    Baseline,
    ContainerObservation,
    JourneyStats,
    Sample,
    Thresholds,
    WatchdogState,
    WatchdogWindow,
)

logger = logging.getLogger(__name__)


def journey_window_s(baseline_rps: float, min_samples: int) -> float | None:
    """Sparse journeys only contribute to aggregate decisions."""
    if baseline_rps <= 0 or baseline_rps * 20 < min_samples:
        return None
    return max(5, min(20, min_samples / baseline_rps))


def evaluate(
    window_stats: WatchdogWindow,
    thresholds: Thresholds,
    baseline: Baseline,
    state: WatchdogState,
) -> tuple[WatchdogState, str | None]:
    """Pure evaluation; callers supply windows computed for each baseline rate."""
    if "all" not in baseline.journeys:
        raise ValueError("Watchdog requires an aggregate 'all' baseline.")
    new = state.model_copy(deep=True)
    if new.abort_reason is not None:
        return new, new.abort_reason
    rule = thresholds.abort
    # Blast-radius protection is immediate, including during metrics grace.
    unhealthy = [
        name
        for name, original in baseline.containers.items()
        if original.status in {"running", "unhealthy"}
        and name not in state.target_containers
        and window_stats.container_states.get(name, "running") not in {"running", "unhealthy"}
    ]
    if len(unhealthy) > rule.max_unhealthy_containers:
        new.abort_reason = f"Blast radius: {len(unhealthy)} non-target containers left running."
        return new, new.abort_reason
    if window_stats.elapsed_s < 5:
        new.consecutive = {}
        return new, None
    counters: dict[str, int] = {}
    for name, original in baseline.journeys.items():
        if name != "all" and journey_window_s(original.rps, rule.min_samples) is None:
            continue
        stats = window_stats.journeys.get(name)
        if stats is None or stats.n < rule.min_samples:
            counters[name] = 0
            continue
        breach = None
        if stats.error_rate > rule.max_error_rate:
            breach = f"{name}: error rate exceeds {rule.max_error_rate:g}."
        elif stats.p95_ms > latency_limit_ms(original.p95_ms, rule.p95_multiple, rule.p95_floor_ms):
            breach = f"{name}: p95 exceeds multiple and absolute floor."
        counters[name] = state.consecutive.get(name, 0) + 1 if breach else 0
        if breach and counters[name] >= rule.consecutive_breaches:
            new.abort_reason = breach
    new.consecutive = counters
    return new, new.abort_reason


class SampleSource(Protocol):
    def snapshot(self) -> list[Sample]: ...


class ContainerSource(Protocol):
    def snapshot(self) -> list[ContainerObservation]: ...


def live_window(
    samples: list[Sample],
    observations: list[ContainerObservation],
    baseline: Baseline,
    thresholds: Thresholds,
    started_at: float,
    now: float,
) -> WatchdogWindow:
    journeys: dict[str, JourneyStats] = {}
    for name, original in baseline.journeys.items():
        length = (
            5.0 if name == "all" else journey_window_s(original.rps, thresholds.abort.min_samples)
        )
        if length is None:
            continue
        start = max(started_at, now - length)
        if now > start:
            selected = samples if name == "all" else [s for s in samples if s.journey == name]
            stats = window_stats(selected, start, now)
            # Completed timeout requests can arrive after the nominal five-second
            # window. Keep actual samples visible without lowering the minimum.
            if stats.n < thresholds.abort.min_samples and length < 20:
                start = max(started_at, now - 20)
                stats = window_stats(selected, start, now)
            journeys[name] = stats
    latest: dict[str, ContainerObservation] = {}
    for observation in observations:
        if observation.t <= now and (
            observation.name not in latest or observation.t > latest[observation.name].t
        ):
            latest[observation.name] = observation
    return WatchdogWindow(
        elapsed_s=max(0, now - started_at),
        journeys=journeys,
        container_states={name: item.stats.status for name, item in latest.items()},
    )


class Watchdog(threading.Thread):
    def __init__(
        self,
        samples: SampleSource,
        containers: ContainerSource,
        baseline: Baseline,
        thresholds: Thresholds,
        abort_event: threading.Event,
        *,
        target_containers: set[str],
        started_at: float | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.samples, self.containers = samples, containers
        self.baseline, self.thresholds, self.abort_event = baseline, thresholds, abort_event
        self.started_at = time.time() if started_at is None else started_at
        self.state = WatchdogState(target_containers=target_containers)
        self.stop_event = threading.Event()

    @property
    def breach_reason(self) -> str | None:
        return self.state.abort_reason

    def evaluate_once(self, *, now: float | None = None) -> str | None:
        window = live_window(
            self.samples.snapshot(),
            self.containers.snapshot(),
            self.baseline,
            self.thresholds,
            self.started_at,
            time.time() if now is None else now,
        )
        self.state, reason = evaluate(window, self.thresholds, self.baseline, self.state)
        if reason:
            self.abort_event.set()
        return reason

    def run(self) -> None:
        while not self.stop_event.wait(1):
            if self.abort_event.is_set():
                return
            try:
                if self.evaluate_once():
                    return
            except Exception:
                # Losing the safety observer must fail closed, without leaking payloads.
                logger.error("Watchdog evaluation failed; requesting abort.")
                self.state.abort_reason = "Watchdog evaluation unavailable."
                self.abort_event.set()
                return

    def stop(self) -> None:
        self.stop_event.set()
        if self.ident is not None:
            self.join()
