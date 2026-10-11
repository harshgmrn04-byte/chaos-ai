"""Measured runner with synthetic load, virtual time and real scoped undo journals."""

import signal
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

from chaosai import cli, experiment, runner, safety
from chaosai.approval import ApprovalRequired, Approver
from chaosai.config import Settings
from chaosai.faults import get_fault
from chaosai.fixture_data import fixture
from chaosai.models import (
    ContainerObservation,
    ContainerStats,
    ExperimentResult,
    Graph,
    JournalData,
    Plan,
    RunMeta,
)
from chaosai.runs import create_run, read_json, read_jsonl, write_json
from chaosai.safety import UndoJournal, create_kill_flag, file_lock, policy_gate
from chaosai.watchdog import Watchdog
from tests.test_evaluate import baseline_for, sample
from tests.test_safety import FakeContainer


class Harness:
    def __init__(self, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
        self.settings = settings
        self.now = 1000.0
        self.baseline = baseline_for()
        self.graph = fixture("graph", Graph)
        self.container = FakeContainer("redis", settings.compose_project)
        self.container.logs = Mock(return_value=b"normal line\npassword=secret\n")
        self.client = SimpleNamespace(
            containers=SimpleNamespace(get=Mock(return_value=self.container)), close=Mock()
        )
        self.baseline.containers = {
            "redis": ContainerStats(cpu_pct=0, mem_mb=1, restarts=0, status="running")
        }
        self.timer = self.watchdog = self.load = self.poller = None
        self.mode = "normal"
        self.waits: list[float] = []
        self.rollback_calls = 0
        self.signal_handlers = {
            sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)
        }
        harness = self

        class Timer:
            def __init__(self, duration: float, callback: object) -> None:
                self.duration, self.callback = duration, callback
                self.deadline = None
                self.cancelled = self.joined = False
                harness.timer = self

            def start(self) -> None:
                self.deadline = harness.now + self.duration

            def cancel(self) -> None:
                self.cancelled = True

            def join(self) -> None:
                self.joined = True

        class Observer:
            def __init__(self, *, target: object, daemon: bool) -> None:
                self.target = target

            def start(self) -> None:
                if self.target.__name__ == "prepare_load" and harness.mode != "prepare_timeout":
                    self.target()

            def join(self) -> None:
                pass

        class Load:
            def __init__(self, settings: Settings, client: object, samples: object) -> None:
                self.samples = samples
                self.failed = threading.Event()
                self.stopped = False
                harness.load = self

            def prepare(self, users: int) -> None:
                if harness.mode == "prepare_exception":
                    raise RuntimeError("password=must never be persisted")

            def set_workers(self, users: int) -> None:
                pass

            def stop(self) -> None:
                self.stopped = True
                self.signals_ignored = all(
                    signal.getsignal(sig) == signal.SIG_IGN
                    for sig in (signal.SIGINT, signal.SIGTERM)
                )
                if harness.mode == "cleanup_exception":
                    raise RuntimeError("bad cleanup")

        class Poller:
            def __init__(self, client: object, settings: Settings, path: Path) -> None:
                assert settings.metrics_interval_s == 1
                self.values: list[ContainerObservation] = []
                self.stopped = False
                harness.poller = self

            def start(self) -> None:
                self.poll()

            def poll(self) -> None:
                self.values.append(
                    ContainerObservation(
                        t=harness.now,
                        name="redis",
                        stats=ContainerStats(
                            cpu_pct=1, mem_mb=2, restarts=0, status=harness.container.status
                        ),
                    )
                )

            def snapshot(self) -> list[ContainerObservation]:
                return self.values[:]

            def stop(self) -> None:
                self.stopped = True

        class Dog(Watchdog):
            def __init__(self, *args: object, **kwargs: object) -> None:
                super().__init__(*args, **kwargs)
                self.stopped = False
                self.last_tick = harness.now
                harness.watchdog = self

            def start(self) -> None:
                pass

            def stop(self) -> None:
                self.stopped = True

        self.fault = get_fault("stop")
        original_replay = UndoJournal.replay_all

        def replay(journal: UndoJournal, **kwargs: object) -> int:
            harness.rollback_calls += 1
            return original_replay(journal, **kwargs)

        monkeypatch.setattr(UndoJournal, "replay_all", replay)
        monkeypatch.setattr(experiment, "get_docker_client", lambda: self.client)
        monkeypatch.setattr(experiment, "LoadGenerator", Load)
        monkeypatch.setattr(experiment, "ContainerStatsPoller", Poller)
        monkeypatch.setattr(experiment, "Watchdog", Dog)
        monkeypatch.setattr(experiment, "get_fault", lambda _: self.fault)
        monkeypatch.setattr(
            experiment, "time", SimpleNamespace(time=lambda: self.now, monotonic=lambda: self.now)
        )
        monkeypatch.setattr(
            experiment,
            "threading",
            SimpleNamespace(
                Event=threading.Event, Lock=threading.Lock, Timer=Timer, Thread=Observer
            ),
        )
        monkeypatch.setattr(safety, "time", SimpleNamespace(time=lambda: self.now))
        monkeypatch.setattr(experiment, "wait_tick", self.advance)

    def advance(self, seconds: float) -> None:
        assert 0 < seconds <= 0.5
        self.waits.append(seconds)
        # Two requests per journey per tick: healthy precheck, observable failure during.
        for name in ("browse", "login"):
            if (
                self.mode == "sparse_recovery"
                and self.timer is not None
                and self.timer.cancelled
                and name == "login"
            ):
                continue
            for offset in (0.1, 0.2):
                self.load.samples.append(
                    sample(self.now + offset, name, ok=self.container.status == "running")
                )
        self.now += seconds
        self.poller.poll()
        active = self.timer is not None and not self.timer.cancelled
        if active:
            elapsed = self.now - (self.timer.deadline - self.timer.duration)
            if self.mode == "KILL" and elapsed >= 2:
                create_kill_flag(self.settings)
            elif self.mode == "external" and elapsed >= 2:
                UndoJournal(
                    self.settings.journal_path(self.run_dir), self.client, self.settings
                ).replay_all()
            elif self.mode == "exception" and elapsed >= 2:
                raise RuntimeError("secret exception payload")
            elif self.mode in {"SIGINT", "SIGTERM"} and elapsed >= 2:
                signum = getattr(signal, self.mode)
                signal.getsignal(signum)(signum, None)
            elif self.mode == "load_failed" and elapsed >= 2:
                self.load.failed.set()
            if not self.watchdog.stopped and self.now - self.watchdog.last_tick >= 1:
                self.watchdog.evaluate_once(now=self.now)
                self.watchdog.last_tick = self.now
            if self.now >= self.timer.deadline:
                self.timer.cancelled = True
                self.timer.callback()

    def run(
        self, *, duration: int = 5, abort_error_rate: float = 0.9, approver: Approver | None = None
    ) -> ExperimentResult:
        self.run_dir = create_run(self.settings.runs_dir)
        decision = policy_gate(
            runner.demo_spec(
                self.baseline,
                duration=duration,
                abort_error_rate=abort_error_rate,
                fault=self.fault.name,
                target=getattr(self, "target", "redis"),
            )
        )
        return experiment.execute_experiment(
            decision,
            self.run_dir,
            settings=self.settings,
            graph=self.graph,
            baseline=self.baseline,
            approver=approver or Approver(yes=True, is_tty=False, ask=lambda _: "y"),
        )


@pytest.fixture
def harness(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(settings, monkeypatch)


def assert_clean(h: Harness, result: ExperimentResult, *, applied: bool = True) -> None:
    assert h.client.close.call_count == 1
    assert result.rollback_ok
    assert h.container.status == "running"
    assert h.load.stopped and h.poller.stopped
    if applied:
        assert h.rollback_calls > 0
        assert h.timer.cancelled and h.timer.joined
        assert h.watchdog.stopped
        journal = read_json(h.run_dir / "journal.json", JournalData)
        assert journal.records and all(record.done for record in journal.records)
    for sig, original in h.signal_handlers.items():
        assert signal.getsignal(sig) == original
    saved = read_json(h.run_dir / "experiments" / result.spec.id / "result.json", ExperimentResult)
    assert saved == result
    assert read_jsonl(h.run_dir / result.samples_path, type(sample(0)))
    assert (
        read_jsonl(
            h.run_dir / "experiments" / result.spec.id / "events.jsonl", type(result.events[0])
        )
        == result.events
    )
    assert "secret exception payload" not in str(saved.model_dump())


def test_normal_hard_cap_and_meaningful_artifacts(harness: Harness) -> None:
    h = harness
    original = h.baseline.model_dump()
    result = h.run()
    assert_clean(h, result)
    assert not result.aborted and not result.skipped
    assert result.recovered and result.recovery_time_s == 3
    assert result.impacted_journeys == ["browse", "login"]
    assert result.during["all"].error_rate == 1 and result.after["all"].error_rate == 0
    assert result.verdict == "refuted"
    assert [item.phase for item in result.events] == [
        "APPROVE",
        "PRECHECK",
        "PRECHECK",
        "INJECT",
        "OBSERVE",
        "UNDO",
        "RECOVER",
        "RESULT",
    ]
    assert h.timer.duration == result.spec.duration_s == 5
    assert h.baseline.model_dump() == original
    assert any("exited" in event for event in result.container_events)
    assert any("running" in event for event in result.container_events)
    assert result.log_excerpts["ecommerce-redis"] == [
        "normal line",
        "[redacted secret-bearing log line]",
    ]
    assert max(h.waits) <= 0.5


def test_final_policy_thresholds_reach_real_watchdog(harness: Harness) -> None:
    h = harness
    original = h.baseline.model_dump()
    result = h.run(duration=30, abort_error_rate=0)  # Policy clamps to .05.
    assert_clean(h, result)
    assert result.aborted and result.verdict == "aborted"
    assert "error rate exceeds 0.05" in result.abort_reason
    assert result.decision.clamps[0].final == 0.05
    assert h.watchdog.thresholds.abort.max_error_rate == 0.05
    assert h.baseline.model_dump() == original
    assert result.recovered  # Abort is distinct from subsequent successful recovery.


@pytest.mark.parametrize(
    "mode,reason",
    [
        ("KILL", "Kill switch"),
        ("external", "another terminal"),
        ("exception", "RuntimeError"),
        ("SIGINT", "Interrupted"),
        ("SIGTERM", "Interrupted"),
        ("load_failed", "Load worker failed"),
    ],
)
def test_every_exit_restores_and_cleans(harness: Harness, mode: str, reason: str) -> None:
    harness.mode = mode
    result = harness.run(duration=30)
    assert_clean(harness, result)
    assert result.aborted and result.verdict == "aborted" and reason in result.abort_reason
    if mode == "KILL":
        assert harness.settings.kill_path.exists()
        assert not result.recovered
    assert harness.now < harness.timer.deadline + 15


def test_precheck_skip_no_mutation_with_recorded_reason(harness: Harness) -> None:
    harness.baseline.stable = False
    result = harness.run()
    assert_clean(harness, result, applied=False)
    assert result.skipped and "S9 timeout" in result.skip_reason
    assert result.verdict == "inconclusive" and result.during == result.after == {}
    assert not harness.container.calls and harness.timer is None
    assert harness.now == 1060


def test_approval_skip_and_non_tty_refusal(harness: Harness) -> None:
    with pytest.raises(ApprovalRequired):
        harness.run(approver=Approver(yes=False, is_tty=False, ask=lambda _: "y"))
    result = harness.run(approver=Approver(yes=False, is_tty=True, ask=lambda _: "n"))
    assert result.skipped and result.skip_reason == "Approval: skip."
    assert not harness.container.calls and harness.client.close.call_count == 0
    assert Path(harness.run_dir / result.samples_path).exists()


def test_prepare_exception_closes_partial_resources(harness: Harness) -> None:
    harness.mode = "prepare_exception"
    result = harness.run()
    assert result.aborted and result.rollback_ok
    assert harness.load.stopped and harness.poller.stopped
    assert harness.client.close.call_count == 1 and not harness.container.calls
    assert "must never be persisted" not in str(result)


def test_cleanup_exception_still_closes_other_resources(harness: Harness) -> None:
    harness.mode = "cleanup_exception"
    result = harness.run()
    assert_clean(harness, result)
    assert result.aborted and "Cleanup failed" in result.abort_reason


def test_apply_exception_after_mutation_is_undone(harness: Harness) -> None:
    original = harness.container.stop

    def fail(timeout: int) -> None:
        original(timeout)
        raise RuntimeError("bad Docker payload")

    harness.container.stop = fail
    result = harness.run()
    assert result.aborted and result.rollback_ok
    assert "start" in harness.container.calls
    assert harness.client.close.call_count == 1
    assert "bad Docker payload" not in str(result)


def test_reconcile_precedes_load_prepare(harness: Harness) -> None:
    h = harness
    path = h.settings.runs_dir / "previous" / "journal.json"
    path.parent.mkdir(parents=True)
    with file_lock(h.settings.operation_lock_path):
        journal = UndoJournal(path, h.client, h.settings)
        journal.record(h.container, "stop")
        h.container.stop()
    result = h.run()
    assert_clean(h, result)
    assert read_json(path, JournalData).records[0].done
    assert h.container.calls[:3] == [("stop", 5), "start", ("stop", 5)]


def test_active_lease_denial_does_not_undo_other_process(harness: Harness) -> None:
    h = harness
    with file_lock(h.settings.active_lock_path, blocking=False):
        result = h.run()
    assert result.aborted and not result.rollback_ok and h.rollback_calls == 0
    assert not h.container.calls
    assert h.client.close.call_count == 1


def test_saved_kill_blocks_new_fault_and_is_not_cleared(harness: Harness) -> None:
    create_kill_flag(harness.settings)
    result = harness.run()
    assert result.aborted and result.rollback_ok
    assert harness.settings.kill_path.exists() and not harness.container.calls


def test_failed_rollback_retains_journal_and_reports_false(harness: Harness) -> None:
    harness.container.start = Mock(side_effect=RuntimeError("bad secret"))
    result = harness.run()
    assert result.aborted or not result.rollback_ok
    assert not result.rollback_ok and not result.recovered
    assert not read_json(harness.run_dir / "journal.json", JournalData).records[0].done
    assert harness.client.close.call_count == 1


def test_live_pipeline_single_measured_result_without_llm(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = harness
    h.settings.target_dir = Path(__file__).parent / "fixtures"
    write_json(h.settings.safety_dir / "baseline.json", h.baseline)
    monkeypatch.setattr(runner, "get_docker_client", lambda: h.client)
    original = runner.execute_experiment

    def execute(decision: object, run_dir: Path, **kwargs: object) -> ExperimentResult:
        h.run_dir = run_dir
        return original(decision, run_dir, **kwargs)

    monkeypatch.setattr(runner, "execute_experiment", execute)
    for stage in ("plan_experiments", "analyze_results", "score_results", "measure_baseline"):
        monkeypatch.setattr(runner, stage, Mock(side_effect=AssertionError(stage)))
    directory = runner.run_pipeline(h.settings, Approver(yes=True, is_tty=False, ask=input))
    assert len(list((directory / "experiments").glob("*/result.json"))) == 1
    assert len(read_json(directory / "plan.json", Plan).root) == 1
    assert not read_json(directory / "run.json", RunMeta).offline
    html = (directory / "report.html").read_text()
    assert "Measured Phase 6" in html and "fixture data only" not in html
    assert read_json(directory / "scores.json", list) == []
    assert read_json(directory / "findings.json", list) == []


def test_cli_inject_refuses_without_tty(cli_workspace: Path) -> None:
    result = CliRunner().invoke(cli.app, ["inject", "stop", "redis"])
    assert result.exit_code == 2 and "No TTY" in result.output
    assert not (cli_workspace / "runs").exists()


def test_cli_inject_clamps_and_prints_result(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    monkeypatch.setattr(cli, "target_config", lambda: h.settings)
    monkeypatch.setattr(cli, "map_target", lambda _: h.graph)
    original = cli.execute_experiment

    def execute(decision: object, run_dir: Path, **kwargs: object) -> ExperimentResult:
        h.run_dir = run_dir
        return original(decision, run_dir, **kwargs)

    monkeypatch.setattr(cli, "execute_experiment", execute)
    write_json(h.settings.safety_dir / "baseline.json", h.baseline)
    result = CliRunner().invoke(
        cli.app, ["inject", "stop", "redis", "--duration", "0", "--abort-error-rate", "0", "--yes"]
    )
    assert result.exit_code == 0, result.output
    assert "0 -> 5" in result.output and "0.0 -> 0.05" in result.output
    assert "Verdict:" in result.output and "Container rollback OK: True" in result.output
    assert "Application recovered:" in result.output


def test_independent_timer_restores_while_observer_is_blocked(
    harness: Harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    h = harness
    entered = threading.Event()
    restored = threading.Event()
    original_start = h.container.start

    def start() -> None:
        original_start()
        restored.set()

    def observe(ctx: object) -> None:
        entered.set()
        assert restored.wait(1), "Timer must undo before observer can finish."

    original_timer = threading.Timer

    def short_timer(duration: float, callback: object) -> threading.Timer:
        assert duration == 5
        timer = original_timer(0.1, callback)
        h.timer = timer
        return timer

    monkeypatch.setattr(
        experiment,
        "threading",
        SimpleNamespace(
            Event=threading.Event, Lock=threading.Lock, Thread=threading.Thread, Timer=short_timer
        ),
    )
    monkeypatch.setattr(experiment, "time", time)
    monkeypatch.setattr(experiment, "near_baseline", lambda *args: (True, "ready"))
    monkeypatch.setattr(experiment, "wait_tick", lambda _: time.sleep(0.01))
    monkeypatch.setattr(h.fault, "observe", observe)
    monkeypatch.setattr(h.container, "start", start)
    h.settings.recovery_timeout_s = 0.01
    result = h.run()
    assert entered.is_set() and restored.is_set()
    assert result.rollback_ok and not result.aborted
    assert not h.timer.is_alive()
    assert h.client.close.call_count == 1 and h.poller.stopped and h.load.stopped


def test_deadline_cancellation_prevents_late_apply(harness: Harness) -> None:
    h = harness

    def apply(ctx: object) -> None:
        # Simulate a caller delayed until AFTER the timer's independent undo attempt.
        h.timer.callback()
        type(h.fault).apply(h.fault, ctx)

    h.fault.apply = apply
    result = h.run()
    assert result.aborted and result.rollback_ok
    assert not h.container.calls
    assert h.timer.cancelled and h.timer.joined


@pytest.mark.parametrize("auto_restart,expected", [(False, False), (True, True)])
def test_kill_self_heal_observed_before_our_undo(
    harness: Harness,
    auto_restart: bool,
    expected: bool,
) -> None:
    h = harness
    h.fault = get_fault("kill")
    h.target = "order-service"
    original_apply = h.fault.apply
    holder = {}

    def apply(ctx: object) -> None:
        holder["ctx"] = ctx
        original_apply(ctx)
        if auto_restart:
            h.container.start()  # Simulates an independent restart policy, not our undo.
        h.fault.observe(ctx)

    h.fault.apply = apply
    # The pre-undo observation establishes true/false; later undo cannot change it.
    result = h.run()
    assert_clean(h, result)
    assert result.self_healed is expected
    assert all(record.done for record in read_json(h.run_dir / "journal.json", JournalData).records)


def test_preparation_timeout_records_skip_at_sixty_seconds(harness: Harness) -> None:
    harness.mode = "prepare_timeout"
    result = harness.run()
    assert result.skipped and "Load preparation did not complete" in result.skip_reason
    assert harness.now == 1060
    assert result.verdict == "inconclusive"
    assert not harness.container.calls and harness.load.stopped
    assert harness.client.close.call_count == 1


def test_empty_recovery_journey_times_out_without_false_health(harness: Harness) -> None:
    harness.mode = "sparse_recovery"
    result = harness.run()
    assert_clean(harness, result)
    assert result.rollback_ok and not result.recovered and result.recovery_time_s is None
    assert result.after["login"].n == 0
    assert harness.now == 1135  # 10s precheck + 5s injection + exactly 120s recovery.
    assert any("sparse" in limitation.lower() for limitation in result.evidence_limits)


def test_signals_ignored_until_all_cleanup_finishes(harness: Harness) -> None:
    h = harness
    h.mode = "SIGTERM"
    result = h.run()
    # Harness records the signal disposition during worker cleanup.
    assert result.aborted and h.load.signals_ignored


def test_cancellation_during_scope_reload_prevents_mutation(harness: Harness) -> None:
    h = harness
    original_apply = h.fault.apply

    def apply(ctx: object) -> None:
        # Cancellation arrives after the entry check, while scope reload is in flight.
        h.container.reload = ctx.cancel_event.set
        original_apply(ctx)

    h.fault.apply = apply
    result = h.run()
    assert result.aborted and result.rollback_ok
    assert not h.container.calls
    assert (
        not read_json(h.run_dir / "journal.json", JournalData).records
        if (h.run_dir / "journal.json").exists()
        else True
    )
