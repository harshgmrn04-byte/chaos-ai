"""Deterministic evidence evaluation; empty and sparse windows never prove health."""

import math
from collections.abc import Iterable

from chaosai.metrics import MIN_BASELINE_SAMPLES, latency_limit_ms, stats_by_journey, window_stats
from chaosai.models import Baseline, Hypothesis, JourneyStats, Sample, Thresholds, Verdict


def compute_during_after(
    samples: Iterable[Sample], t_inject: float, t_undo: float, *, t_end: float | None = None
) -> tuple[dict[str, JourneyStats], dict[str, JourneyStats]]:
    """Requests belong by start time: injection inclusive, undo exclusive during."""
    values = list(samples)
    names = {s.journey for s in values}
    end = (
        t_end
        if t_end is not None
        else max((s.t + max(s.latency_ms / 1000, 1e-6) for s in values), default=t_undo)
    )

    def stats(start: float, stop: float) -> dict[str, JourneyStats]:
        if stop <= start:
            return {}
        return {
            **stats_by_journey(values, start, stop, names),
            "all": window_stats(values, start, stop),
        }

    return stats(t_inject, t_undo), stats(t_undo, end)


def near_baseline(
    samples: list[Sample], baseline: Baseline, thresholds: Thresholds, t0: float, t1: float
) -> tuple[bool, str]:
    """S9 uses 5–20s adaptive windows, requiring evidence even for sparse journeys."""
    if not baseline.stable or not baseline.journeys or "all" not in baseline.journeys:
        return False, "Baseline is unstable or lacks aggregate evidence."
    for name, original in baseline.journeys.items():
        length = (
            5
            if name == "all"
            else max(5, min(20, thresholds.abort.min_samples / original.rps))
            if original.rps > 0
            else 20
        )
        if original.n == 0:
            return False, f"{name}: no baseline evidence."
        if t1 - t0 < length:
            return False, f"{name}: waiting for precheck window."
        selected = samples if name == "all" else [s for s in samples if s.journey == name]
        stats = window_stats(selected, t1 - length, t1)
        minimum = (
            thresholds.abort.min_samples
            if name == "all" or original.rps * 20 >= (thresholds.abort.min_samples)
            else 1
        )
        if stats.n < minimum and length < 20 and t1 - t0 >= 20:
            # Baseline throughput is an estimate, not a promise of exact sample cadence.
            stats = window_stats(selected, t1 - 20, t1)
        if stats.n < minimum:
            return False, f"{name}: insufficient precheck samples ({stats.n}/{minimum})."
        if stats.error_rate > original.error_rate + thresholds.hypothesis.max_error_rate:
            return False, f"{name}: precheck error rate above baseline allowance."
        if stats.p95_ms > latency_limit_ms(
            original.p95_ms, 1.5, thresholds.hypothesis.p95_floor_ms
        ):
            return False, f"{name}: precheck p95 above 1.5x baseline."
    return True, "All journeys near baseline; sparse windows provide limited evidence."


def recovery_window_s(baseline: Baseline) -> int:
    """Keep dense 1s windows; give sparse journeys time for actual requests."""
    rates = [s.rps for name, s in baseline.journeys.items() if name != "all"]
    if not rates or min(rates) <= 0:
        return 20
    return max(1, min(20, math.ceil(1 / min(rates))))


def find_recovery(
    samples: Iterable[Sample],
    baseline: Baseline,
    thresholds: Thresholds,
    t_undo: float,
    *,
    t_end: float | None = None,
) -> tuple[bool, float | None]:
    """Three complete healthy windows for EVERY journey, ending within 120s.

    Windows are 1–20s, sized from measured baseline traffic. Missing journeys reset
    the streak. Callers seal windows behind request timeout to exclude in-flight work.
    """
    values = list(samples)
    names = [name for name in baseline.journeys if name != "all"]
    if not names or any(baseline.journeys[name].n == 0 for name in names):
        return False, None
    end = (
        t_end
        if t_end is not None
        else max((s.t + s.latency_ms / 1000 for s in values), default=t_undo)
    )
    streak = 0
    width = recovery_window_s(baseline)
    for index in range(min(120 // width, max(0, math.floor((end - t_undo) / width)))):
        start = t_undo + index * width
        healthy = True
        for name in names:
            stats = window_stats((s for s in values if s.journey == name), start, start + width)
            if (
                stats.n == 0
                or stats.error_rate > thresholds.hypothesis.max_error_rate
                or stats.p95_ms
                > latency_limit_ms(
                    baseline.journeys[name].p95_ms, 1.5, thresholds.hypothesis.p95_floor_ms
                )
            ):
                healthy = False
                break
        streak = streak + 1 if healthy else 0
        if streak == 3:
            return True, float((index + 1) * width)
    return False, None


def impacted_journeys(
    during: dict[str, JourneyStats],
    baseline: Baseline,
    thresholds: Thresholds,
) -> list[str]:
    """Observed hypothesis breaches, including sparse descriptive evidence."""
    return sorted(
        name
        for name, stats in during.items()
        if name != "all"
        and name in baseline.journeys
        and stats.n > 0
        and (
            stats.error_rate > thresholds.hypothesis.max_error_rate
            or stats.p95_ms
            > latency_limit_ms(
                baseline.journeys[name].p95_ms,
                thresholds.hypothesis.p95_multiple,
                thresholds.hypothesis.p95_floor_ms,
            )
        )
    )


def verdict(
    during: dict[str, JourneyStats],
    baseline: Baseline,
    thresholds: Thresholds,
    hypothesis: Hypothesis,
    *,
    aborted: bool = False,
) -> Verdict:
    """Aborts win; >=20 baseline AND during samples required per hypothesis journey."""
    if aborted:
        return "aborted"
    names = (
        [name for name in baseline.journeys if name != "all"]
        if hypothesis.journey == "all"
        else [hypothesis.journey]
    )
    if not names:
        return "inconclusive"
    enough = [
        name
        for name in names
        if name in during
        and name in baseline.journeys
        and min(during[name].n, baseline.journeys[name].n) >= MIN_BASELINE_SAMPLES
    ]
    breached = set(impacted_journeys(during, baseline, thresholds))
    if breached.intersection(enough):
        return "refuted"
    return "confirmed" if len(enough) == len(names) else "inconclusive"
