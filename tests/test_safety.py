"""Safety invariants with in-memory Docker objects and real durable journal files."""

import json
import os
import signal
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest
from typer.testing import CliRunner

from chaosai import cli
from chaosai.config import Settings
from chaosai.faults import get_fault
from chaosai.faults.base import FaultContext, hold_fault
from chaosai.fixture_data import fixture
from chaosai.models import ExperimentSpec, Graph, JournalData, Plan
from chaosai.runs import read_json
from chaosai.safety import (
    SafetyError,
    SafetyInterrupted,
    UndoJournal,
    clear_kill_flag,
    create_kill_flag,
    file_lock,
    kill_requested,
    policy_gate,
    replay_journals,
    rollback_signals,
    scope_guard,
)
from chaosai.target import PROJECT_LABEL


class FakeContainer:
    def __init__(self, name: str, project: str) -> None:
        self.name, self.id = name, f"id-{name}"
        self.labels = {PROJECT_LABEL: project}
        self.status = "running"
        self.attrs = {
            "State": {"Running": True, "Paused": False},
            "HostConfig": {"CpuPeriod": 100000, "CpuQuota": 25000, "NanoCpus": 0},
        }
        self.calls: list[object] = []
        self.before_apply = lambda: None

    def reload(self) -> None:
        pass

    def stop(self, timeout: int = 5) -> None:
        self.before_apply()
        self.calls.append(("stop", timeout))
        self.status = "exited"
        self.attrs["State"]["Running"] = False

    def kill(self) -> None:
        self.before_apply()
        self.calls.append("kill")
        self.status = "exited"
        self.attrs["State"]["Running"] = False

    def pause(self) -> None:
        self.before_apply()
        self.calls.append("pause")
        self.attrs["State"]["Paused"] = True
        self.status = "paused"

    def start(self) -> None:
        self.calls.append("start")
        self.status = "running"
        self.attrs["State"]["Running"] = True

    def unpause(self) -> None:
        self.calls.append("unpause")
        self.attrs["State"]["Paused"] = False
        self.status = "running"

    def update(self, *, cpu_period: int, cpu_quota: int, nano_cpus: int) -> None:
        self.calls.append(("update", cpu_period, cpu_quota, nano_cpus))
        self.attrs["HostConfig"].update(
            CpuPeriod=cpu_period, CpuQuota=cpu_quota, NanoCpus=nano_cpus
        )


def spec_for(fault: str = "stop", target: str = "redis", **changes: object) -> ExperimentSpec:
    value = fixture("plan", Plan).root[0].model_dump()
    value.update(fault=fault, target=target, **changes)
    return ExperimentSpec.model_validate(value)


@pytest.fixture
def context(settings: Settings) -> FaultContext:
    graph = fixture("graph", Graph)
    container = FakeContainer(
        next(n.container for n in graph.nodes if n.id == "redis"), settings.compose_project
    )
    client = SimpleNamespace(
        containers=SimpleNamespace(get=Mock(return_value=container)), close=Mock()
    )
    journal = UndoJournal(settings.journal_path(settings.runs_dir / "test"), client, settings)
    return FaultContext(spec_for(), graph, client, settings, journal)


@pytest.mark.parametrize("duration,expected", [(0, 5), (5, 5), (30, 30), (120, 120), (999, 120)])
def test_duration_clamps(duration: int, expected: int) -> None:
    decision = policy_gate(spec_for(duration_s=duration))
    assert decision.final.duration_s == expected
    assert decision.requested.duration_s == duration
    assert len(decision.clamps) == (duration != expected)
    if decision.clamps:
        assert decision.clamps[0].model_dump() == {
            "field": "duration_s",
            "requested": duration,
            "final": expected,
            "reason": "Required range [5, 120].",
        }


@pytest.mark.parametrize(
    "fault,value,expected",
    [
        ("cpu_limit", None, 0.5),
        ("cpu_limit", 0.01, 0.1),
        ("cpu_limit", 2, 0.5),
        ("traffic_spike", None, 2),
        ("traffic_spike", 1, 2),
        ("traffic_spike", 99, 8),
    ],
)
def test_intensity_clamps(fault: str, value: float | None, expected: float) -> None:
    decision = policy_gate(spec_for(fault=fault, intensity=value))
    assert decision.allowed and decision.final.intensity == expected
    assert decision.clamps[0].field == "intensity"
    assert decision.clamps[0].requested == value
    assert decision.clamps[0].final == expected


def test_abort_clamps_and_worker_ceiling() -> None:
    spec = spec_for("traffic_spike", intensity=99)
    spec.abort.max_error_rate, spec.abort.max_p95_multiple = 1, 100
    decision = policy_gate(spec, base_workers=30)
    assert decision.final.intensity == 5
    assert decision.final.abort.max_error_rate == 0.9
    assert decision.final.abort.max_p95_multiple == 20
    assert [c.final for c in decision.clamps if c.field == "intensity"] == [8, 5]
    spec.abort.max_error_rate, spec.abort.max_p95_multiple = 0, 0
    final = policy_gate(spec).final
    assert final.abort.max_error_rate == 0.05 and final.abort.max_p95_multiple == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"active_faults": 1},
        {"experiments_completed": 8},
        {"experiments_completed": 9},
        {"active_faults": -1},
        {"experiments_completed": -1},
        {"base_workers": 76},
        {"base_workers": 0},
    ],
)
def test_run_limits_deny(kwargs: dict[str, int]) -> None:
    decision = policy_gate(spec_for("traffic_spike", intensity=2), **kwargs)
    assert not decision.allowed and decision.deny_reason


def test_run_limit_boundaries() -> None:
    assert policy_gate(spec_for(), experiments_completed=7).allowed
    assert policy_gate(spec_for("traffic_spike", intensity=2), base_workers=75).allowed


@pytest.mark.parametrize(
    "target,reason",
    [
        ("unknown", "Unknown"),
        ("stripe", "External"),
        ("mailhog", "not injectable"),
    ],
)
def test_scope_graph_denials(context: FaultContext, target: str, reason: str) -> None:
    with pytest.raises(SafetyError, match=reason):
        scope_guard(target, context.graph, context.client, context.settings.compose_project)
    context.client.containers.get.assert_not_called()


@pytest.mark.parametrize("labels", [{}, {PROJECT_LABEL: "unrelated"}])
def test_scope_container_denials(context: FaultContext, labels: dict[str, str]) -> None:
    container = context.client.containers.get.return_value
    container.labels = labels
    with pytest.raises(SafetyError, match="label"):
        get_fault("stop").apply(context)
    assert not context.journal.path.exists() and not container.calls


def test_scope_no_container_and_ambiguous(context: FaultContext) -> None:
    node = next(n for n in context.graph.nodes if n.id == "redis")
    node.container = None
    with pytest.raises(SafetyError, match="no container"):
        scope_guard("redis", context.graph, context.client, context.settings.compose_project)
    context.graph.nodes.append(node)
    with pytest.raises(SafetyError, match="ambiguous"):
        scope_guard("redis", context.graph, context.client, context.settings.compose_project)


@pytest.mark.parametrize(
    "fault,target",
    [
        ("stop", "redis"),
        ("pause", "redis"),
        ("kill", "order-service"),
    ],
)
def test_fsync_before_apply_and_crash_replay(
    context: FaultContext,
    fault: str,
    target: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context.spec = spec_for(fault, target)
    container = context.client.containers.get.return_value
    node = next(n for n in context.graph.nodes if n.id == target)
    container.name, container.id = node.container, f"id-{node.container}"
    syncs: list[int] = []
    real_fsync = os.fsync

    def sync(fd: int) -> None:
        real_fsync(fd)
        syncs.append(fd)

    monkeypatch.setattr(os, "fsync", sync)

    def assert_durable() -> None:
        assert len(syncs) >= 2  # The complete file and its containing directory.
        data = read_json(context.journal.path, JournalData)
        assert len(data.records) == 1 and not data.records[0].done
        assert data.records[0].original.running
        assert data.records[0].container_id == container.id

    container.before_apply = assert_durable
    get_fault(fault).apply(context)
    # Simulate process death: instantiate a fresh journal, without the FaultContext.
    fresh = UndoJournal(context.journal.path, context.client, context.settings)
    assert fresh.replay_all() == 1
    assert container.status == "running" and not container.attrs["State"]["Paused"]
    assert read_json(fresh.path, JournalData).records[0].done
    assert fresh.replay_all() == 0
    assert ("stop", 5) in container.calls if fault == "stop" else fault in container.calls


def test_crash_before_apply_does_not_start_healthy_container(context: FaultContext) -> None:
    container = context.client.containers.get.return_value
    context.journal.record(container, "stop")
    assert context.journal.replay_all() == 1
    assert not container.calls


def test_restore_cpu_limits(context: FaultContext) -> None:
    container = context.client.containers.get.return_value
    context.journal.record(container, "cpu_limit")
    container.attrs["HostConfig"].update(CpuPeriod=200000, CpuQuota=10000)
    container.stop()
    assert context.journal.replay_all() == 1
    assert container.calls[-1] == ("update", 100000, 25000, 0)


@pytest.mark.parametrize("failure", ["scope", "identity", "start", "verification"])
def test_failed_replay_keeps_pending(context: FaultContext, failure: str) -> None:
    container = context.client.containers.get.return_value
    get_fault("stop").apply(context)
    if failure == "scope":
        container.labels = {}
    elif failure == "identity":
        container.id = "replacement"
    elif failure == "start":
        container.start = Mock(side_effect=docker.errors.APIError("SECRET must not be logged"))
    else:
        container.start = Mock()  # Docker returned success but still exited.
    with pytest.raises(SafetyError, match="incomplete"):
        context.journal.replay_all()
    assert not read_json(context.journal.path, JournalData).records[0].done


def test_apply_exception_can_be_replayed(context: FaultContext) -> None:
    container = context.client.containers.get.return_value

    def fail_after_mutation(timeout: int) -> None:
        container.status = "exited"
        raise docker.errors.APIError("Secret payload")

    container.stop = fail_after_mutation
    with pytest.raises(docker.errors.APIError):
        get_fault("stop").apply(context)
    assert context.journal.replay_all() == 1
    assert container.status == "running"


def test_kill_observes_self_heal_before_undo(context: FaultContext) -> None:
    context.spec = spec_for("kill", "order-service")
    fault = get_fault("kill")
    fault.apply(context)
    fault.observe(context)
    assert context.self_healed is False
    container = context.client.containers.get.return_value
    container.start()  # Automatic restart simulated.
    fault.observe(context)
    assert context.self_healed is True
    fault.undo(context)
    assert container.calls.count("start") == 1


def test_invalid_fault_kind_and_preexisting_pause(context: FaultContext) -> None:
    with pytest.raises(SafetyError):
        get_fault("kill").validate(spec_for("kill", "redis"), context.graph)
    container = context.client.containers.get.return_value
    container.pause()
    with pytest.raises(SafetyError, match="unpaused"):
        get_fault("stop").apply(context)
    assert not context.journal.path.exists()


def test_kill_flag_blocks_apply_and_polls(context: FaultContext) -> None:
    assert not kill_requested(context.settings)
    create_kill_flag(context.settings)
    assert kill_requested(context.settings)
    with pytest.raises(SafetyError, match="KILL"):
        get_fault("stop").apply(context)
    assert not context.journal.path.exists()
    abort = threading.Event()
    assert hold_fault(get_fault("stop"), context, abort) == "Kill switch requested."
    assert abort.is_set()
    clear_kill_flag(context.settings)
    assert not kill_requested(context.settings)


def test_active_fault_lock_and_flag_clear_denied(context: FaultContext) -> None:
    create_kill_flag(context.settings)
    with file_lock(context.settings.active_lock_path, blocking=False):
        with pytest.raises(SafetyError, match="active"):
            with file_lock(context.settings.active_lock_path, blocking=False):
                pytest.fail("Second active fault admitted")
        with pytest.raises(SafetyError):
            clear_kill_flag(context.settings)
    assert kill_requested(context.settings)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_signals_unwind_then_rollback(context: FaultContext, signum: int) -> None:
    abort = threading.Event()
    previous = signal.getsignal(signum)
    with pytest.raises(SafetyInterrupted):
        with rollback_signals(context.journal.replay_all, abort):
            get_fault("stop").apply(context)
            signal.raise_signal(signum)
    assert abort.is_set()
    assert context.client.containers.get.return_value.status == "running"
    assert signal.getsignal(signum) == previous


def test_journal_replay_continues_on_failure(context: FaultContext) -> None:
    container = context.client.containers.get.return_value
    context.journal.record(container, "stop")
    container.labels = {PROJECT_LABEL: "other"}
    second = FakeContainer("second", context.settings.compose_project)
    context.client.containers.get.side_effect = lambda name: (
        second if name == "second" else container
    )
    context.journal.record(second, "stop")
    second.stop()
    with pytest.raises(SafetyError):
        context.journal.replay_all()
    records = read_json(context.journal.path, JournalData).records
    assert records[1].done and not records[0].done
    assert second.status == "running"


def test_replay_all_journals_and_corrupt_journal(context: FaultContext) -> None:
    get_fault("stop").apply(context)
    other = context.settings.runs_dir / "bad" / "journal.json"
    other.parent.mkdir()
    other.write_text('{"bad": true}')
    with pytest.raises(SafetyError):
        replay_journals(context.client, context.settings)
    assert context.client.containers.get.return_value.status == "running"
    assert other.read_text() == '{"bad": true}'


def test_rollback_waits_for_inflight_apply(context: FaultContext) -> None:
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    container = context.client.containers.get.return_value

    def block() -> None:
        entered.set()
        assert release.wait(3)

    container.before_apply = block
    application = threading.Thread(target=get_fault("stop").apply, args=(context,))
    application.start()
    assert entered.wait(3)

    def rollback() -> None:
        replay_journals(context.client, context.settings)
        finished.set()

    recovery = threading.Thread(target=rollback)
    recovery.start()
    assert not finished.wait(0.02)
    release.set()
    application.join(3)
    recovery.join(3)
    assert finished.is_set() and container.status == "running"


def setup_cli(monkeypatch: pytest.MonkeyPatch, context: FaultContext) -> None:
    monkeypatch.setattr(cli, "target_config", lambda: context.settings)
    monkeypatch.setattr(cli, "map_target", lambda settings: context.graph)
    monkeypatch.setattr(cli, "get_docker_client", lambda: context.client)


def test_cli_non_tty_refuses_before_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    result = CliRunner().invoke(cli.app, ["fault", "apply", "stop", "redis"])
    assert result.exit_code != 0 and "No TTY" in result.output


def test_cli_apply_finally_and_clamps(
    context: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_cli(monkeypatch, context)
    monkeypatch.setattr(cli, "hold_fault", lambda *args: None)
    result = CliRunner().invoke(
        cli.app, ["fault", "apply", "stop", "redis", "--yes", "--duration", "500"]
    )
    assert result.exit_code == 0, result.output
    assert "120s" in result.output and "Rollback complete" in result.output
    assert context.client.containers.get.return_value.status == "running"
    decision_path = next(context.settings.runs_dir.glob("*/decisions.json"))
    decision = json.loads(decision_path.read_text())[0]
    assert decision["clamps"][0]["final"] == 120
    context.client.close.assert_called_once()


def test_cli_hold_exception_recovers(
    context: FaultContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_cli(monkeypatch, context)
    monkeypatch.setattr(cli, "hold_fault", Mock(side_effect=OSError("Secret details")))
    result = CliRunner().invoke(cli.app, ["fault", "apply", "stop", "redis", "--yes"])
    assert result.exit_code != 0 and "Secret details" not in result.output
    assert context.client.containers.get.return_value.status == "running"


def test_cli_rollback_and_kill(context: FaultContext, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_cli(monkeypatch, context)
    get_fault("stop").apply(context)
    result = CliRunner().invoke(cli.app, ["kill"])
    assert result.exit_code == 0 and "KILL set" in result.output
    assert kill_requested(context.settings)
    assert context.client.containers.get.return_value.status == "running"
    result = CliRunner().invoke(cli.app, ["rollback", "--clear-kill"])
    assert result.exit_code == 0 and not kill_requested(context.settings)


def test_cli_kill_sets_flag_even_if_docker_unavailable(
    context: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_cli(monkeypatch, context)
    monkeypatch.setattr(cli, "get_docker_client", Mock(side_effect=SafetyError("Unreachable")))
    result = CliRunner().invoke(cli.app, ["kill"])
    assert result.exit_code != 0 and kill_requested(context.settings)


def test_cli_undo(context: FaultContext, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_cli(monkeypatch, context)
    get_fault("stop").apply(context)
    result = CliRunner().invoke(cli.app, ["fault", "undo", "stop", "redis"])
    assert result.exit_code == 0 and "1 undo records" in result.output
    assert context.client.containers.get.return_value.status == "running"


def test_cli_explicit_kill_clear_cannot_race_active_fault(
    context: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_cli(monkeypatch, context)
    create_kill_flag(context.settings)
    with file_lock(context.settings.active_lock_path):
        result = CliRunner().invoke(cli.app, ["rollback", "--clear-kill"])
    assert result.exit_code != 0 and kill_requested(context.settings)


def test_hard_cap_and_abort(context: FaultContext, monkeypatch: pytest.MonkeyPatch) -> None:
    import chaosai.faults.base as base

    ticks = iter([0.0, 30.0])
    monkeypatch.setattr(base.time, "monotonic", lambda: next(ticks))
    context.spec.duration_s = 5
    assert hold_fault(get_fault("stop"), context, threading.Event()) is None
    monkeypatch.setattr(base.time, "monotonic", lambda: 0)
    abort = threading.Event()
    abort.set()
    assert hold_fault(get_fault("stop"), context, abort) == "Abort requested."


def test_journal_write_failure_prevents_docker_apply(
    context: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(context.journal, "_save", Mock(side_effect=OSError("Disk unavailable")))
    with pytest.raises(OSError):
        get_fault("stop").apply(context)
    assert not context.client.containers.get.return_value.calls


def test_restoration_does_not_count_as_self_heal(context: FaultContext) -> None:
    context.spec = spec_for("kill", "order-service")
    fault = get_fault("kill")
    fault.apply(context)
    fault.observe(context)
    fault.undo(context)
    fault.observe(context)
    assert context.self_healed is False


def test_cli_signal_recovers(context: FaultContext, monkeypatch: pytest.MonkeyPatch) -> None:
    setup_cli(monkeypatch, context)

    def interrupt(*args: object) -> None:
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr(cli, "hold_fault", interrupt)
    result = CliRunner().invoke(cli.app, ["fault", "apply", "stop", "redis", "--yes"])
    assert result.exit_code == 130 and "rollback complete" in result.output
    assert context.client.containers.get.return_value.status == "running"


def test_reconcile_pending_journal_before_new_apply(
    context: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setup_cli(monkeypatch, context)
    get_fault("stop").apply(context)
    monkeypatch.setattr(cli, "hold_fault", lambda *args: None)
    result = CliRunner().invoke(cli.app, ["fault", "apply", "stop", "redis", "--yes"])
    assert result.exit_code == 0, result.output
    assert context.client.containers.get.return_value.calls == [
        ("stop", 5),
        "start",
        ("stop", 5),
        "start",
    ]


def test_legacy_empty_fixture_journals_are_compatible(context: FaultContext) -> None:
    legacy = context.settings.runs_dir / "legacy" / "journal.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("[]\n")
    assert replay_journals(context.client, context.settings) == 0
    assert legacy.read_text() == "[]\n"
    legacy.write_text('[{"container": "untrusted"}]')
    with pytest.raises(SafetyError):
        replay_journals(context.client, context.settings)
    context.client.containers.get.assert_not_called()


def test_raw_duration_cannot_bypass_policy(context: FaultContext) -> None:
    context.spec.duration_s = 121
    with pytest.raises(SafetyError, match="policy gate"):
        get_fault("stop").apply(context)
    assert not context.journal.path.exists()
