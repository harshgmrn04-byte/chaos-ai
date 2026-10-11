"""Extended fault boundaries with SDK-shaped fakes, durable files and no network."""

import os
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import docker
import pytest

from chaosai.config import Settings
from chaosai.faults import get_fault
from chaosai.faults.base import FaultContext
from chaosai.fixture_data import fixture
from chaosai.load import LoadGenerator, SampleBuffer
from chaosai.models import Graph, JournalData, LoadUndoRecord
from chaosai.runs import read_json
from chaosai.safety import SafetyError, UndoJournal, policy_gate
from tests.test_safety import FakeContainer, spec_for


@pytest.fixture
def ctx(settings: Settings) -> FaultContext:
    container = FakeContainer("ecommerce-orders", settings.compose_project)
    client = SimpleNamespace(containers=SimpleNamespace(get=Mock(return_value=container)))
    journal = UndoJournal(settings.runs_dir / "cpu" / "journal.json", client, settings)
    return FaultContext(
        spec_for("cpu_limit", "order-service", intensity=0.2),
        fixture("graph", Graph),
        client,
        settings,
        journal,
    )


@pytest.mark.parametrize("period,quota", [(100000, 50000), (200000, -1), (0, -1)])
def test_cpu_durable_originals_and_exact_restoration(
    ctx: FaultContext,
    period: int,
    quota: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container = ctx.client.containers.get.return_value
    original = dict(CpuPeriod=period, CpuQuota=quota, NanoCpus=0)
    container.attrs["HostConfig"] = original.copy()
    synced: list[int] = []
    fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (fsync(fd), synced.append(fd)))

    def durable() -> None:
        record = read_json(ctx.journal.path, JournalData).records[0]
        assert len(synced) >= 3 and not record.done
        assert (record.original.cpu_period, record.original.cpu_quota) == (period, quota)

    container.before_apply = durable
    get_fault("cpu_limit").apply(ctx)
    assert container.attrs["HostConfig"]["CpuQuota"] == 20000
    fresh = UndoJournal(ctx.journal.path, ctx.client, ctx.settings)
    assert fresh.replay_all() == 1
    assert container.attrs["HostConfig"] == original
    assert fresh.replay_all() == 0


@pytest.mark.parametrize(
    "host",
    [
        dict(CpuPeriod=0, CpuQuota=0, NanoCpus=0),
        dict(CpuPeriod=100000, CpuQuota=0, NanoCpus=0),
        dict(CpuPeriod=0, CpuQuota=0, NanoCpus=500000000),
    ],
)
def test_cpu_non_restorable_settings_denied_before_mutation(ctx: FaultContext, host: dict) -> None:
    container = ctx.client.containers.get.return_value
    container.attrs["HostConfig"] = host.copy()
    with pytest.raises(SafetyError):
        get_fault("cpu_limit").apply(ctx)
    assert container.attrs["HostConfig"] == host
    assert not ctx.journal.path.exists() and not container.calls


@pytest.mark.parametrize("intensity", [None, 0, 0.09, 0.51, 99])
def test_cpu_raw_intensity_cannot_bypass_policy(ctx: FaultContext, intensity: float | None) -> None:
    ctx.spec.intensity = intensity
    with pytest.raises(SafetyError):
        get_fault("cpu_limit").apply(ctx)
    assert not ctx.journal.path.exists()


def test_cpu_apply_failure_and_failed_verification_retained(ctx: FaultContext) -> None:
    container = ctx.client.containers.get.return_value
    original = container.update

    def partial(**kwargs: int) -> None:
        original(**kwargs)
        raise docker.errors.APIError("untrusted payload")

    container.update = partial
    with pytest.raises(docker.errors.APIError):
        get_fault("cpu_limit").apply(ctx)
    container.update = Mock()  # Returning success does not restore settings.
    with pytest.raises(SafetyError):
        ctx.journal.replay_all()
    assert not read_json(ctx.journal.path, JournalData).records[0].done
    container.update = original
    assert ctx.journal.replay_all() == 1


def test_legacy_zero_cpu_record_is_not_falsely_restored(ctx: FaultContext) -> None:
    container = ctx.client.containers.get.return_value
    container.attrs["HostConfig"].update(CpuPeriod=0, CpuQuota=0)
    ctx.journal.record(container, "cpu_limit")
    container.attrs["HostConfig"].update(CpuPeriod=100000, CpuQuota=20000)
    with pytest.raises(SafetyError):
        ctx.journal.replay_all()
    assert not read_json(ctx.journal.path, JournalData).records[0].done
    assert not container.calls


def test_installed_sdk_omits_zero_and_rejects_nano_cpus() -> None:
    api = object.__new__(docker.APIClient)
    api._version = "1.45"
    api._url = Mock(return_value="scoped-update-url")
    api._post_json = Mock(return_value=object())
    api._result = Mock(return_value={"Warnings": []})
    api.update_container("scoped-id", cpu_period=0, cpu_quota=-1)
    api._post_json.assert_called_once_with("scoped-update-url", data={"CpuQuota": -1})
    with pytest.raises(TypeError):
        api.update_container("scoped-id", nano_cpus=0)


class FakeLoad:
    def __init__(self, count: int) -> None:
        self.worker_count = count
        self.calls: list[tuple[int, str | None]] = []
        self.joined = False
        self.before_start = lambda: None

    def set_workers(self, count: int, *, journey: str | None = None) -> None:
        self.before_start()
        self.worker_count = count
        self.calls.append((count, journey))

    def restore_workers(self, count: int) -> None:
        self.worker_count = count
        self.joined = True


@pytest.mark.parametrize(
    "workers,multiplier,total,target",
    [
        (10, 8, 80, "all"),
        (30, 8, 150, "checkout"),
        (75, 8, 150, "browse"),
    ],
)
def test_traffic_cap_journal_before_workers_and_joined_cleanup(
    ctx: FaultContext,
    workers: int,
    multiplier: float,
    total: int,
    target: str,
) -> None:
    ctx.spec = policy_gate(
        spec_for("traffic_spike", target, intensity=multiplier),
        base_workers=workers,
    ).final
    ctx.generator = load = FakeLoad(workers)

    def durable() -> None:
        record = read_json(ctx.journal.path, JournalData).records[0]
        assert isinstance(record, LoadUndoRecord)
        assert record.original_workers == workers and record.owner_pid == os.getpid()
        assert not record.done

    load.before_start = durable
    get_fault("traffic_spike").apply(ctx)
    assert load.worker_count == total <= 150
    assert load.calls == [(total, None if target == "all" else target)]
    assert not ctx.client.containers.get.return_value.calls
    get_fault("traffic_spike").undo(ctx)
    assert load.joined and load.worker_count == workers
    assert read_json(ctx.journal.path, JournalData).records[0].done


def test_traffic_partial_start_exception_is_reversible(ctx: FaultContext) -> None:
    ctx.spec = spec_for("traffic_spike", "browse", intensity=2)
    ctx.generator = load = FakeLoad(10)
    original = load.set_workers

    def partial(count: int, **kwargs: object) -> None:
        original(count, **kwargs)
        raise RuntimeError("partial startup")

    load.set_workers = partial
    with pytest.raises(RuntimeError):
        get_fault("traffic_spike").apply(ctx)
    assert load.worker_count == 20
    assert ctx.journal.replay_all() == 1
    assert load.worker_count == 10 and load.joined


@pytest.mark.parametrize("fault", ["cpu_limit", "traffic_spike"])
def test_durable_write_failure_prevents_extended_mutation(
    ctx: FaultContext,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if fault == "traffic_spike":
        ctx.spec = spec_for(fault, "browse", intensity=2)
        ctx.generator = FakeLoad(10)
    monkeypatch.setattr(ctx.journal, "_save", Mock(side_effect=OSError("disk unavailable")))
    with pytest.raises(OSError):
        get_fault(fault).apply(ctx)
    assert not ctx.client.containers.get.return_value.calls
    if ctx.generator:
        assert not ctx.generator.calls


def test_external_traffic_rollback_requires_owner_ack(ctx: FaultContext) -> None:
    ctx.spec = spec_for("traffic_spike", "all", intensity=2)
    ctx.generator = load = FakeLoad(10)
    get_fault("traffic_spike").apply(ctx)
    external = UndoJournal(ctx.journal.path, ctx.client, ctx.settings)
    with pytest.raises(SafetyError):
        external.replay_all()
    record = read_json(ctx.journal.path, JournalData).records[0]
    assert record.stop_requested and not record.done and load.worker_count == 20
    assert ctx.journal.stop_requested(record.id)
    assert ctx.journal.replay_all() == 1
    assert load.joined and external.is_done(record.id)


def test_dead_traffic_owner_replay_does_not_touch_docker(
    ctx: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx.journal.record_load(FakeLoad(10))
    ctx.client.containers.get.reset_mock()
    monkeypatch.setattr(os, "kill", Mock(side_effect=ProcessLookupError))
    fresh = UndoJournal(ctx.journal.path, ctx.client, ctx.settings)
    assert fresh.replay_all() == 1
    ctx.client.containers.get.assert_not_called()


@pytest.mark.parametrize(
    "target,workers,intensity",
    [
        ("stripe", 10, 2),
        ("unknown", 10, 2),
        ("browse", 76, 2),
        ("all", 20, 8),
    ],
)
def test_traffic_denies_unscoped_targets_and_excess_workers(
    ctx: FaultContext,
    target: str,
    workers: int,
    intensity: float,
) -> None:
    ctx.spec = spec_for("traffic_spike", target, intensity=intensity)
    ctx.generator = FakeLoad(workers)
    with pytest.raises(SafetyError):
        get_fault("traffic_spike").apply(ctx)
    assert not ctx.journal.path.exists() and not ctx.generator.calls


def test_real_worker_retirement_is_joined_and_targeted(ctx: FaultContext) -> None:
    gen = LoadGenerator(ctx.settings, ctx.client, SampleBuffer(ctx.settings.runs_dir / "samples"))
    started = threading.Event()
    targets: list[str | None] = []

    def worker(stop: threading.Event, journey: str | None = None) -> None:
        targets.append(journey)
        started.set()
        stop.wait()

    gen._worker = worker
    gen.set_workers(2)
    assert started.wait(1)
    gen.set_workers(4, journey="browse")
    gen.restore_workers(2)
    assert not gen._retiring and gen.worker_count == 2
    assert targets.count("browse") == 2
    gen.stop()
    assert gen.worker_count == 0 and not gen._retiring


def test_traffic_foreign_label_prevents_even_worker_mutation(ctx: FaultContext) -> None:
    ctx.spec = spec_for("traffic_spike", "browse", intensity=2)
    ctx.generator = FakeLoad(10)
    ctx.client.containers.get.return_value.labels = {}
    with pytest.raises(SafetyError):
        get_fault("traffic_spike").apply(ctx)
    assert not ctx.generator.calls and not ctx.journal.path.exists()


def test_traffic_failed_join_retains_pending(ctx: FaultContext) -> None:
    ctx.spec = spec_for("traffic_spike", "all", intensity=2)
    ctx.generator = FakeLoad(10)
    get_fault("traffic_spike").apply(ctx)
    ctx.generator.restore_workers = Mock(side_effect=RuntimeError("join failed"))
    with pytest.raises(SafetyError):
        ctx.journal.replay_all()
    assert not read_json(ctx.journal.path, JournalData).records[0].done


def test_traffic_fsync_failure_after_rename_still_has_cleanup_owner(
    ctx: FaultContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from chaosai import safety

    ctx.spec = spec_for("traffic_spike", "all", intensity=2)
    ctx.generator = FakeLoad(10)
    monkeypatch.setattr(safety, "fsync_directory", Mock(side_effect=OSError("directory fsync")))
    with pytest.raises(OSError):
        get_fault("traffic_spike").apply(ctx)
    assert read_json(ctx.journal.path, JournalData).records
    assert not ctx.generator.calls
    monkeypatch.setattr(safety, "fsync_directory", lambda path: None)
    assert ctx.journal.replay_all() == 1
    assert ctx.generator.joined
