"""S5 windows, thresholds and independent consecutive-breach counters."""

import threading
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from chaosai.config import Settings
from chaosai.fixture_data import fixture
from chaosai.models import (
    Baseline,
    ContainerObservation,
    ContainerStats,
    JourneyStats,
    Sample,
    WatchdogState,
    WatchdogWindow,
)
from chaosai.watchdog import Watchdog, evaluate, journey_window_s, live_window


def stats(n: int = 10, error: float = 0, p95: float = 8, rps: float = 2) -> JourneyStats:
    return JourneyStats(n=n, error_rate=error, p50_ms=p95, p95_ms=p95, p99_ms=p95, rps=rps)


@pytest.fixture
def baseline() -> Baseline:
    value = fixture("baseline", Baseline)
    value.journeys = {"all": stats(rps=10), "browse": stats(rps=2), "login": stats(rps=0.1)}
    value.containers = {
        f"c{i}": ContainerStats(cpu_pct=0, mem_mb=1, restarts=0, status="running") for i in range(6)
    }
    return value


def window(elapsed: float = 10, **journeys: JourneyStats) -> WatchdogWindow:
    return WatchdogWindow(elapsed_s=elapsed, journeys=journeys)


def step(
    baseline: Baseline, current: WatchdogWindow, state: WatchdogState | None = None
) -> tuple[WatchdogState, str | None]:
    return evaluate(current, baseline.thresholds, baseline, state or WatchdogState())


def test_grace_and_input_immutability(baseline: Baseline) -> None:
    state = WatchdogState(consecutive={"all": 1})
    new, reason = step(baseline, window(4.999, all=stats(error=1)), state)
    assert not new.consecutive and reason is None
    assert state.consecutive == {"all": 1}
    new, reason = step(baseline, window(5, all=stats(error=1)))
    assert reason is None and new.consecutive["all"] == 1


def test_min_samples_resets_counter(baseline: Baseline) -> None:
    state, _ = step(baseline, window(all=stats(error=1)))
    state, reason = step(baseline, window(all=stats(n=9, error=1)), state)
    assert reason is None and state.consecutive["all"] == 0
    state, reason = step(baseline, window(all=stats(error=1)), state)
    assert reason is None
    _, reason = step(baseline, window(all=stats(error=1)), state)
    assert reason and "error rate" in reason


def test_missing_window_resets_counter(baseline: Baseline) -> None:
    state, _ = step(baseline, window(all=stats(error=1)))
    state, reason = step(baseline, window(), state)
    assert not reason and state.consecutive["all"] == 0


def test_single_evaluation_spike_and_sustained_breach(baseline: Baseline) -> None:
    state, reason = step(baseline, window(all=stats(error=1)))
    assert reason is None
    state, reason = step(baseline, window(all=stats()), state)
    assert reason is None and state.consecutive["all"] == 0
    state, reason = step(baseline, window(all=stats(error=1)), state)
    assert reason is None
    new, reason = step(baseline, window(all=stats(error=1)), state)
    assert reason and new.abort_reason == reason
    again, reason2 = step(baseline, window(all=stats()), new)
    assert again == new and reason2 == reason


@pytest.mark.parametrize("p95,breaches", [(80, False), (200, False), (758, False), (759, True)])
def test_absolute_floor_and_strict_boundary(baseline: Baseline, p95: float, breaches: bool) -> None:
    state, reason = step(baseline, window(all=stats(p95=p95)))
    assert reason is None
    _, reason = step(baseline, window(all=stats(p95=p95)), state)
    assert bool(reason) == breaches


def test_both_latency_guards_required(baseline: Baseline) -> None:
    baseline.journeys["all"].p95_ms = 500
    state, _ = step(baseline, window(all=stats(p95=2000)))  # above +750, below 10x.
    _, reason = step(baseline, window(all=stats(p95=2000)), state)
    assert reason is None


def test_error_equality_does_not_breach(baseline: Baseline) -> None:
    state, _ = step(baseline, window(all=stats(error=0.5)))
    _, reason = step(baseline, window(all=stats(error=0.5)), state)
    assert reason is None


def test_low_rate_journey_only_aggregate(baseline: Baseline) -> None:
    current = window(all=stats(), login=stats(error=1, p95=9000))
    state, _ = step(baseline, current)
    state, reason = step(baseline, current, state)
    assert reason is None and "login" not in state.consecutive
    current = window(all=stats(error=1), login=stats(error=1))
    state, _ = step(baseline, current)
    _, reason = step(baseline, current, state)
    assert reason and reason.startswith("all:")


def test_per_journey_breach_and_separate_counters(baseline: Baseline) -> None:
    state, _ = step(baseline, window(all=stats(error=1), browse=stats()))
    state, reason = step(baseline, window(all=stats(), browse=stats(error=1)), state)
    assert reason is None
    _, reason = step(baseline, window(all=stats(), browse=stats(error=1)), state)
    assert reason and reason.startswith("browse:")


@pytest.mark.parametrize(
    "rps,length", [(0, None), (0.49, None), (0.5, 20), (1, 10), (2, 5), (20, 5)]
)
def test_adaptive_window_bounds(rps: float, length: float | None) -> None:
    assert journey_window_s(rps, 10) == length


def test_blast_radius_immediate_excludes_target_and_preexisting(baseline: Baseline) -> None:
    baseline.containers["old"] = ContainerStats(cpu_pct=0, mem_mb=0, restarts=0, status="exited")
    state = WatchdogState(target_containers={"c0"})
    current = window(0)
    current.container_states = {
        "c0": "exited",
        "c1": "paused",
        "c2": "exited",
        "c3": "dead",
        "old": "exited",
        "foreign": "exited",
    }
    state, reason = step(baseline, current, state)
    assert reason is None  # Exactly three non-targets.
    current.container_states["c4"] = "exited"
    _, reason = step(baseline, current, state)
    assert reason and reason.startswith("Blast radius: 4")


def sample(t: float, journey: str = "browse", ok: bool = True) -> Sample:
    return Sample(t=t, journey=journey, ok=ok, status=200 if ok else 500, latency_ms=8, error=None)


def test_windows_use_rate_and_exclude_pre_fault_samples(baseline: Baseline) -> None:
    baseline.journeys["browse"].rps = 1  # Ten-second window.
    samples = [
        sample(0),
        sample(10),
        sample(14),
        sample(15),
        sample(19),
        sample(20),
        sample(19, "login"),
    ]
    observations = [
        ContainerObservation(t=19, name="c1", stats=baseline.containers["c1"]),
        ContainerObservation(
            t=18, name="c1", stats=baseline.containers["c1"].model_copy(update={"status": "exited"})
        ),
        ContainerObservation(
            t=21, name="c1", stats=baseline.containers["c1"].model_copy(update={"status": "dead"})
        ),
    ]
    current = live_window(samples, observations, baseline, baseline.thresholds, 12, 20)
    assert current.journeys["all"].n == 4  # Expands when short; excludes pre-fault requests.
    assert current.journeys["browse"].n == 3  # [12,20), bounded by injection time.
    assert "login" not in current.journeys
    assert current.container_states["c1"] == "running"


def test_completed_timeouts_remain_visible_when_recent_window_is_starved(
    baseline: Baseline,
) -> None:
    baseline.journeys["browse"].rps = 30
    samples = [sample(5 + i / 10, ok=False) for i in range(20)]
    for item in samples:
        item.latency_ms = 5000
    state = WatchdogState()
    for now in (12, 13):
        current = live_window(samples, [], baseline, baseline.thresholds, 5, now)
        assert current.journeys["browse"].n == 20
        state, reason = step(baseline, current, state)
    assert reason and "error rate" in reason


def test_one_second_spike_in_live_buffer_does_not_trip(baseline: Baseline) -> None:
    samples = [sample(second + i / 10, ok=second != 6) for second in range(15) for i in range(10)]
    state = WatchdogState()
    for now in range(5, 15):
        current = live_window(samples, [], baseline, baseline.thresholds, 0, now)
        state, reason = step(baseline, current, state)
        assert reason is None


def test_thread_consumes_live_buffers_and_aborts(baseline: Baseline) -> None:
    samples = Mock(snapshot=lambda: [sample(5 + i / 10, ok=False) for i in range(30)])
    containers = Mock(snapshot=lambda: [])
    abort = threading.Event()
    watchdog = Watchdog(
        samples,
        containers,
        baseline,
        baseline.thresholds,
        abort,
        target_containers={"c0"},
        started_at=0,
    )
    assert watchdog.evaluate_once(now=8) is None
    assert watchdog.evaluate_once(now=9)
    assert abort.is_set() and watchdog.breach_reason
    watchdog.stop()


def test_watchdog_thread_waits_one_second_and_fails_closed(baseline: Baseline) -> None:
    abort = threading.Event()
    watchdog = Watchdog(
        Mock(snapshot=Mock(side_effect=ValueError("SECRET"))),
        Mock(snapshot=lambda: []),
        baseline,
        baseline.thresholds,
        abort,
        target_containers=set(),
    )
    watchdog.stop_event = Mock(wait=Mock(return_value=False))
    watchdog.run()
    watchdog.stop_event.wait.assert_called_once_with(1)
    assert abort.is_set() and watchdog.breach_reason == "Watchdog evaluation unavailable."


def test_unhealthy_but_running_is_not_blast_radius(baseline: Baseline) -> None:
    current = window()
    current.container_states = {name: "unhealthy" for name in baseline.containers}
    _, reason = step(baseline, current)
    assert reason is None


def test_disappeared_containers_feed_blast_radius(
    baseline: Baseline,
    settings: Settings,
    tmp_path: Path,
) -> None:
    from chaosai.metrics import ContainerStatsPoller
    from chaosai.target import PROJECT_LABEL

    containers = []
    for name in baseline.containers:
        container = Mock(
            id=f"id-{name}",
            labels={PROJECT_LABEL: settings.compose_project},
            status="running",
            attrs={"State": {}},
            stats=Mock(return_value={}),
        )
        container.name = name
        containers.append(container)
    client = Mock()
    client.containers.list.return_value = containers
    poller = ContainerStatsPoller(client, settings, tmp_path / "observations.jsonl")
    poller.poll_once()
    client.containers.list.return_value = containers[:2]
    poller.poll_once()
    current = live_window([], poller.snapshot(), baseline, baseline.thresholds, 0, time.time() + 1)
    _, reason = step(baseline, current)
    assert reason and "Blast radius: 4" in reason


def test_watchdog_refuses_missing_aggregate_baseline(baseline: Baseline) -> None:
    del baseline.journeys["all"]
    with pytest.raises(ValueError, match="aggregate"):
        step(baseline, window())


def test_initially_unhealthy_running_containers_can_leave_running(baseline: Baseline) -> None:
    for container in baseline.containers.values():
        container.status = "unhealthy"
    current = window()
    current.container_states = {name: "exited" for name in list(baseline.containers)[:4]}
    _, reason = step(baseline, current)
    assert reason and "Blast radius: 4" in reason


def test_stopped_and_paused_status_survives_old_healthcheck(
    settings: Settings, tmp_path: Path
) -> None:
    from chaosai.metrics import ContainerStatsPoller
    from chaosai.target import PROJECT_LABEL

    container = Mock(
        id="one",
        labels={PROJECT_LABEL: settings.compose_project},
        status="exited",
        attrs={"State": {"Health": {"Status": "unhealthy"}}},
    )
    container.name = "one"
    client = Mock()
    client.containers.list.return_value = [container]
    poller = ContainerStatsPoller(client, settings, tmp_path / "observations.jsonl")
    poller.poll_once()
    assert poller.snapshot()[-1].stats.status == "exited"
    container.status = "running"
    container.attrs["State"]["Paused"] = True
    poller.poll_once()
    assert poller.snapshot()[-1].stats.status == "paused"
    container.stats.assert_not_called()
