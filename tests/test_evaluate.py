"""Synthetic evaluation boundaries and deliberately missing journey evidence."""

from datetime import UTC, datetime

import pytest

from chaosai.baseline import derive_thresholds
from chaosai.evaluate import (
    compute_during_after,
    find_recovery,
    impacted_journeys,
    near_baseline,
    verdict,
)
from chaosai.models import Baseline, Hypothesis, JourneyStats, Sample


def sample(t: float, journey: str = "browse", *, latency: float = 10, ok: bool = True) -> Sample:
    return Sample(
        t=t,
        journey=journey,
        ok=ok,
        status=200 if ok else 500,
        latency_ms=latency,
        error=None if ok else "HTTP error",
    )


def baseline_for(names: tuple[str, ...] = ("browse", "login")) -> Baseline:
    stats = JourneyStats(n=100, error_rate=0, p50_ms=10, p95_ms=10, p99_ms=10, rps=10)
    return Baseline(
        created_at=datetime.now(UTC),
        window_s=10,
        stable=True,
        journeys={name: stats.model_copy() for name in (*names, "all")},
        containers={},
        queues={},
        thresholds=derive_thresholds(stats),
    )


def test_half_open_during_after() -> None:
    during, after = compute_during_after(
        [sample(0), sample(1), sample(2), sample(3)], 1, 2, t_end=3
    )
    assert during["browse"].n == after["browse"].n == 1
    assert during["all"].rps == 1
    assert compute_during_after([], 0, 0) == ({}, {})
    during, after = compute_during_after([sample(1)], 1, 1)
    assert during == {} and after["browse"].n == 1


@pytest.mark.parametrize("latency,impacted", [(110, False), (110.001, True)])
def test_hypothesis_absolute_floor_and_equality(latency: float, impacted: bool) -> None:
    baseline = baseline_for(("browse",))
    during, _ = compute_during_after([sample(i / 10, latency=latency) for i in range(20)], 0, 2)
    thresholds = baseline.thresholds
    assert bool(impacted_journeys(during, baseline, thresholds)) is impacted
    hypothesis = Hypothesis(
        journey="all", max_error_rate=0.02, max_p95_multiple=2, statement="holds"
    )
    assert verdict(during, baseline, thresholds, hypothesis) == (
        "refuted" if impacted else "confirmed"
    )
    thresholds.hypothesis.p95_multiple = 20
    assert impacted_journeys(during, baseline, thresholds) == []


@pytest.mark.parametrize(
    "n,expected", [(0, "inconclusive"), (19, "inconclusive"), (20, "confirmed")]
)
def test_verdict_sample_boundary(n: int, expected: str) -> None:
    baseline = baseline_for(("browse",))
    during, _ = compute_during_after([sample(i / 20) for i in range(n)], 0, 1)
    hypothesis = Hypothesis(
        journey="all", max_error_rate=0.02, max_p95_multiple=2, statement="holds"
    )
    assert verdict(during, baseline, baseline.thresholds, hypothesis) == expected
    assert verdict(during, baseline, baseline.thresholds, hypothesis, aborted=True) == "aborted"
    baseline.journeys["browse"].n = 19
    assert verdict(during, baseline, baseline.thresholds, hypothesis) == "inconclusive"


def test_sparse_impacts_are_descriptive_and_no_false_confirmation() -> None:
    baseline = baseline_for()
    during, _ = compute_during_after([sample(0.1, ok=False)], 0, 1)
    assert impacted_journeys(during, baseline, baseline.thresholds) == ["browse"]
    h = Hypothesis(journey="all", max_error_rate=0.02, max_p95_multiple=2, statement="holds")
    assert verdict(during, baseline, baseline.thresholds, h) == "inconclusive"
    during["browse"].n = 20
    assert verdict(during, baseline, baseline.thresholds, h) == "refuted"
    during["browse"].error_rate = 0
    assert verdict(during, baseline, baseline.thresholds, h) == "inconclusive"
    h.journey = "browse"
    assert verdict(during, baseline, baseline.thresholds, h) == "confirmed"


def recovery_samples(start: int, stop: int) -> list[Sample]:
    return [
        sample(i + 0.1, name, latency=15)
        for i in range(start, stop)
        for name in ("browse", "login")
    ]


def test_recovery_complete_windows_three_streak_and_equality() -> None:
    baseline = baseline_for()
    samples = recovery_samples(10, 13)
    assert find_recovery(samples, baseline, baseline.thresholds, 10, t_end=12.999) == (False, None)
    assert find_recovery(samples, baseline, baseline.thresholds, 10, t_end=13) == (True, 3)
    samples[2].ok = False
    samples += recovery_samples(13, 15)
    assert find_recovery(samples, baseline, baseline.thresholds, 10, t_end=15) == (True, 5)
    samples[2].ok = True
    samples[2].latency_ms = 110.001
    assert find_recovery(samples, baseline, baseline.thresholds, 10, t_end=15) == (True, 5)


def test_recovery_empty_sparse_and_missing_baseline_never_healthy() -> None:
    baseline = baseline_for()
    assert find_recovery([], baseline, baseline.thresholds, 10, t_end=130) == (False, None)
    samples = [s for s in recovery_samples(10, 130) if s.journey == "browse"]
    samples += [sample(10.1, "login"), sample(12.1, "login")]
    assert find_recovery(samples, baseline, baseline.thresholds, 10, t_end=130) == (False, None)
    baseline.journeys["login"].n = 0
    assert find_recovery(recovery_samples(10, 13), baseline, baseline.thresholds, 10, t_end=13) == (
        False,
        None,
    )


@pytest.mark.parametrize("start,expected", [(127, (True, 120)), (128, (False, None))])
def test_recovery_120_second_boundary(start: int, expected: tuple[bool, float | None]) -> None:
    baseline = baseline_for()
    assert (
        find_recovery(
            recovery_samples(start, start + 3), baseline, baseline.thresholds, 10, t_end=200
        )
        == expected
    )


def test_precheck_sparse_requires_a_sample_and_strict_limits() -> None:
    baseline = baseline_for()
    baseline.journeys["login"].rps = 0.1
    samples = [sample(i / 10) for i in range(200)]
    healthy, reason = near_baseline(samples, baseline, baseline.thresholds, 0, 20)
    assert not healthy and "login" in reason
    samples.append(sample(10, "login", latency=15))
    assert near_baseline(samples, baseline, baseline.thresholds, 0, 20)[0]
    samples[-1].latency_ms = 110.001
    assert not near_baseline(samples, baseline, baseline.thresholds, 0, 20)[0]
    samples[-1].latency_ms = 15
    samples[-1].ok = False
    assert not near_baseline(samples, baseline, baseline.thresholds, 0, 20)[0]
    baseline.stable = False
    assert not near_baseline(samples, baseline, baseline.thresholds, 0, 20)[0]


def test_recovery_hypothesis_error_limit_equality() -> None:
    baseline = baseline_for()
    values = [
        sample(i + j / 100, name, ok=j != 0)
        for i in range(3)
        for name in ("browse", "login")
        for j in range(50)
    ]
    assert find_recovery(values, baseline, baseline.thresholds, 0, t_end=3) == (True, 3)
    values[1].ok = False
    assert find_recovery(values, baseline, baseline.thresholds, 0, t_end=3) == (False, None)
