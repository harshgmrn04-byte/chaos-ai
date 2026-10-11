"""Deterministic scope, limits, durable undo and process coordination."""

import fcntl
import json
import logging
import math
import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import FrameType
from uuid import uuid4

import docker
from docker.models.containers import Container

from chaosai.config import Settings
from chaosai.models import (
    Clamp,
    ExperimentSpec,
    Graph,
    JournalData,
    OriginalContainerState,
    PolicyDecision,
    UndoRecord,
)
from chaosai.runs import write_json
from chaosai.target import TargetError, require_scope

logger = logging.getLogger(__name__)


class SafetyError(TargetError):
    """A deterministic denial or incomplete rollback; contains no Docker payload."""


class SafetyInterrupted(InterruptedError):
    """Unwind an in-flight operation before rolling it back."""


def scope_guard(target: str, graph: Graph, client: docker.DockerClient, project: str) -> Container:
    matches = [node for node in graph.nodes if node.id == target]
    if len(matches) != 1:
        raise SafetyError("Unknown or ambiguous graph target.")
    node = matches[0]
    if node.kind == "external":
        raise SafetyError("External services cannot be injected.")
    if not node.injectable:
        raise SafetyError("Graph target is not injectable.")
    if not node.container:
        raise SafetyError("Graph target has no container.")
    try:
        container = client.containers.get(node.container)
        container.reload()
        require_scope(container, project)
    except TargetError as exc:
        raise SafetyError("Container lacks the required compose-project label.") from exc
    except docker.errors.DockerException as exc:
        raise SafetyError("Graph container cannot be resolved.") from exc
    return container


def policy_gate(
    spec: ExperimentSpec,
    *,
    active_faults: int = 0,
    experiments_completed: int = 0,
    base_workers: int = 10,
) -> PolicyDecision:
    """Clamp every numerical S2 limit; deny incompatible run-level limits."""
    final = spec.model_copy(deep=True)
    clamps: list[Clamp] = []

    def bounded(
        field: str, requested: float | int | None, lo: float, hi: float, default: float | int
    ) -> float | int:
        value = default if requested is None else max(lo, min(hi, requested))
        if value != requested:
            clamps.append(
                Clamp(
                    field=field,
                    requested=requested,
                    final=value,
                    reason=f"Required range [{lo:g}, {hi:g}].",
                )
            )
        return value

    final.duration_s = int(bounded("duration_s", spec.duration_s, 5, 120, 30))
    final.abort.max_error_rate = float(
        bounded("abort.max_error_rate", spec.abort.max_error_rate, 0.05, 0.9, 0.5)
    )
    final.abort.max_p95_multiple = float(
        bounded("abort.max_p95_multiple", spec.abort.max_p95_multiple, 2, 20, 10)
    )
    denial = None
    if active_faults < 0 or experiments_completed < 0:
        denial = "Invalid run counters."
    elif active_faults:
        denial = "Only one fault may be active at a time."
    elif experiments_completed >= 8:
        denial = "At most eight experiments may run."
    if spec.fault == "cpu_limit":
        final.intensity = float(bounded("intensity", spec.intensity, 0.1, 0.5, 0.5))
    elif spec.fault == "traffic_spike":
        intensity = float(bounded("intensity", spec.intensity, 2, 8, 2))
        if not 1 <= base_workers <= 75:
            denial = "A multiplier of at least two cannot fit within 150 workers."
        else:
            limit = 150 / base_workers
            if intensity > limit:
                clamps.append(
                    Clamp(
                        field="intensity",
                        requested=intensity,
                        final=limit,
                        reason="Total workers must not exceed 150.",
                    )
                )
                intensity = limit
            final.intensity = intensity
            assert math.ceil(base_workers * intensity) <= 150
    return PolicyDecision(
        experiment_id=spec.id,
        requested=spec.model_copy(deep=True),
        final=final,
        clamps=clamps,
        allowed=denial is None,
        deny_reason=denial,
    )


@contextmanager
def file_lock(path: Path, *, blocking: bool = True) -> Iterator[None]:
    """OS lock shared by all CLI processes; released even after process death."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        except BlockingIOError as exc:
            raise SafetyError(
                "Another fault is active; roll it back or wait for completion."
            ) from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class UndoJournal:
    """Atomic/fsynced records, with scope and container identity checked on replay."""

    def __init__(self, path: Path, client: docker.DockerClient, settings: Settings) -> None:
        self.path, self.client, self.settings = path, client, settings

    def _load(self) -> JournalData:
        if not self.path.exists():
            return JournalData()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        # Phase 1 fixture runs contain []; only an EMPTY legacy journal is harmless.
        return JournalData() if data == [] else JournalData.model_validate(data)

    def _save(self, data: JournalData) -> None:
        write_json(self.path, data)  # Flush + fsync file before atomic rename.
        fsync_directory(self.path.parent)  # Persist rename before any Docker mutation.
        fsync_directory(self.path.parent.parent)  # Persist the run directory entry too.

    def record(self, container: Container, fault: str) -> UndoRecord:
        """Caller holds operation_lock through both recording and applying the fault."""
        container.reload()
        require_scope(container, self.settings.compose_project)
        state, host = container.attrs.get("State", {}), container.attrs.get("HostConfig", {})
        record = UndoRecord(
            id=uuid4().hex,
            container=container.name,
            container_id=container.id,
            project=self.settings.compose_project,
            fault=fault,
            original=OriginalContainerState(
                running=state.get("Running", container.status == "running"),
                paused=state.get("Paused", False),
                cpu_period=host.get("CpuPeriod", 0),
                cpu_quota=host.get("CpuQuota", 0),
                nano_cpus=host.get("NanoCpus", 0),
            ),
        )
        data = self._load()
        data.records.append(record)
        self._save(data)
        return record

    def _restore(self, record: UndoRecord) -> None:
        if record.project != self.settings.compose_project:
            raise SafetyError("Journal project differs from COMPOSE_PROJECT.")
        container = self.client.containers.get(record.container)
        container.reload()
        require_scope(container, self.settings.compose_project)
        if container.id != record.container_id:
            raise SafetyError("Journal container identity changed; refusing a replacement.")
        if record.fault not in {"kill", "stop", "pause", "cpu_limit"}:
            raise SafetyError("Unsupported undo record; retained for manual recovery.")
        if container.attrs.get("State", {}).get("Paused", False) and not record.original.paused:
            container.unpause()
            container.reload()
        if record.original.running and container.status != "running":
            container.start()
            container.reload()
        if record.fault == "cpu_limit":
            original = record.original
            container.update(
                cpu_period=original.cpu_period,
                cpu_quota=original.cpu_quota,
                nano_cpus=original.nano_cpus,
            )
        container.reload()
        require_scope(container, self.settings.compose_project)
        if record.original.running and container.status != "running":
            raise SafetyError("Container did not return to running; undo remains pending.")
        if not record.original.paused and container.attrs.get("State", {}).get("Paused", False):
            raise SafetyError("Container is still paused; undo remains pending.")
        if record.fault == "cpu_limit":
            host = container.attrs.get("HostConfig", {})
            if any(
                host.get(key, 0) != value
                for key, value in (
                    ("CpuPeriod", record.original.cpu_period),
                    ("CpuQuota", record.original.cpu_quota),
                    ("NanoCpus", record.original.nano_cpus),
                )
            ):
                raise SafetyError("CPU limits were not restored; undo remains pending.")

    def is_done(self, record_id: str) -> bool:
        return any(r.id == record_id and r.done for r in self._load().records)

    def replay_all(self, *, container: str | None = None, fault: str | None = None) -> int:
        """Reverse pending operations; retain failures and attempt every remaining record."""
        with file_lock(self.settings.operation_lock_path):
            return self._replay(container=container, fault=fault)

    def _replay(self, *, container: str | None = None, fault: str | None = None) -> int:
        """Caller holds the shared operation lock."""
        restored = 0
        failed = False
        data = self._load()
        for record in reversed(data.records):
            if container is not None and record.container != container:
                continue
            if fault is not None and record.fault != fault:
                continue
            if record.done:
                continue
            try:
                self._restore(record)
                record.done = True
                self._save(data)
                restored += 1
            except (TargetError, docker.errors.DockerException, OSError, ValueError):
                failed = True
                logger.error("Undo remains pending for record %s.", record.id)
        if failed:
            raise SafetyError(
                "Rollback incomplete; pending records retained. Retry chaosai rollback."
            )
        return restored


def replay_journals(
    client: docker.DockerClient,
    settings: Settings,
    *,
    container: str | None = None,
    fault: str | None = None,
) -> int:
    restored = 0
    failed = False
    # Lock BEFORE discovery so an in-flight application cannot create an unseen journal.
    with file_lock(settings.operation_lock_path):
        for path in sorted(settings.runs_dir.glob("*/journal.json")):
            try:
                restored += UndoJournal(path, client, settings)._replay(
                    container=container, fault=fault
                )
            except (TargetError, ValueError, OSError):
                failed = True
                logger.error("A journal could not be fully replayed; retained for recovery.")
    if failed:
        raise SafetyError("Rollback incomplete; repair/retry pending journals before injecting.")
    return restored


def kill_requested(settings: Settings) -> bool:
    return settings.kill_path.exists()


def create_kill_flag(settings: Settings) -> None:
    settings.kill_path.parent.mkdir(parents=True, exist_ok=True)
    with settings.kill_path.open("w") as handle:
        handle.write("Rollback requested\n")
        handle.flush()
        os.fsync(handle.fileno())
    fsync_directory(settings.kill_path.parent)


def clear_kill_flag(settings: Settings) -> None:
    """Only explicitly clear after successful rollback and with no active injector."""
    with file_lock(settings.active_lock_path, blocking=False):
        settings.kill_path.unlink(missing_ok=True)
        if settings.kill_path.parent.exists():
            fsync_directory(settings.kill_path.parent)


@contextmanager
def rollback_signals(rollback: Callable[[], object], abort: threading.Event) -> Iterator[None]:
    """Unwind on SIGINT/TERM; rollback on every exit and restore prior handlers."""
    previous = {}

    def handler(signum: int, frame: FrameType | None) -> None:
        abort.set()
        raise SafetyInterrupted(f"Signal {signum}: rollback requested.")

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, handler)
        yield
    finally:
        # Ignore further interrupts during cleanup; rollback must get a chance to finish.
        for signum in previous:
            signal.signal(signum, signal.SIG_IGN)
        try:
            rollback()
        finally:
            for signum, old in previous.items():
                signal.signal(signum, old)
