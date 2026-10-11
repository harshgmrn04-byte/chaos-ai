"""Scoped fault contract and a durable application boundary."""

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass

import docker
from docker.models.containers import Container

from chaosai.config import Settings
from chaosai.load import LoadGenerator
from chaosai.models import ExperimentSpec, Graph, LoadUndoRecord, UndoRecord
from chaosai.safety import SafetyError, UndoJournal, file_lock, kill_requested, scope_guard
from chaosai.target import require_scope


@dataclass
class FaultContext:
    spec: ExperimentSpec
    graph: Graph
    client: docker.DockerClient
    settings: Settings
    journal: UndoJournal
    record: UndoRecord | LoadUndoRecord | None = None
    self_healed: bool | None = None
    cancel_event: threading.Event | None = None
    generator: LoadGenerator | None = None


class Fault(ABC):
    name: str
    kinds: set[str]

    def validate(self, spec: ExperimentSpec, graph: Graph) -> None:
        nodes = [node for node in graph.nodes if node.id == spec.target]
        if spec.fault != self.name or len(nodes) != 1:
            raise SafetyError("Fault/spec mismatch or unknown graph target.")
        node = nodes[0]
        if not node.injectable or not node.container or node.kind not in self.kinds:
            raise SafetyError("Fault is not permitted on this graph node.")
        if not 5 <= spec.duration_s <= 120:
            raise SafetyError("Fault duration must pass the policy gate first.")

    @staticmethod
    def _check_cancelled(ctx: FaultContext) -> None:
        if ctx.cancel_event is not None and ctx.cancel_event.is_set():
            raise SafetyError("Experiment cancelled before apply; no new fault permitted.")
        if kill_requested(ctx.settings):
            raise SafetyError("KILL flag present; rollback and explicitly clear it first.")

    def apply(self, ctx: FaultContext) -> None:
        """Keep journal + Docker operation indivisible relative to second-terminal rollback."""
        self.validate(ctx.spec, ctx.graph)
        with file_lock(ctx.settings.operation_lock_path):
            self._check_cancelled(ctx)
            container = scope_guard(
                ctx.spec.target, ctx.graph, ctx.client, ctx.settings.compose_project
            )
            if container.status != "running" or container.attrs.get("State", {}).get("Paused"):
                raise SafetyError("Injection requires a running, unpaused container.")
            self._check_cancelled(ctx)
            self.check_original(container)
            ctx.record = ctx.journal.record(container, self.name)
            require_scope(container, ctx.settings.compose_project)
            self._check_cancelled(ctx)
            self.apply_container(container, ctx)

    def check_original(self, container: Container) -> None:
        """Reject states which the mechanism cannot restore exactly."""

    def apply_container(self, container: Container, ctx: FaultContext) -> None:
        self._apply(container)

    @abstractmethod
    def _apply(self, container: Container) -> None:
        """Only called after a durable undo record exists."""

    def undo(self, ctx: FaultContext) -> None:
        ctx.journal.replay_all()

    def observe(self, ctx: FaultContext) -> None:
        """Optional read-only observation during the holding interval."""

    @abstractmethod
    def describe(self) -> str:
        """Describe the mechanism and recovery."""


def hold_fault(fault: Fault, ctx: FaultContext, abort: threading.Event) -> str | None:
    """Independent hard time cap; check kill every 0.5s, with no watchdog dependency."""
    deadline = time.monotonic() + ctx.spec.duration_s
    while time.monotonic() < deadline:
        if kill_requested(ctx.settings):
            abort.set()
            return "Kill switch requested."
        if abort.is_set():
            return "Abort requested."
        # A second-terminal rollback marks the journal done; stop holding immediately.
        if ctx.record and ctx.journal.is_done(ctx.record.id):
            return "Rolled back from another terminal."
        fault.observe(ctx)
        abort.wait(min(0.5, max(0, deadline - time.monotonic())))
    return None
