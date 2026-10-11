"""Journaled extra workers; shared authentication and checkout caps stay in force."""

import math

from docker.models.containers import Container

from chaosai.catalogue import applicable
from chaosai.faults.base import Fault, FaultContext
from chaosai.load import JOURNEYS
from chaosai.models import ExperimentSpec, Graph
from chaosai.safety import SafetyError, file_lock, scope_guard


class TrafficSpikeFault(Fault):
    name = "traffic_spike"
    kinds: set[str] = set()

    def validate(self, spec: ExperimentSpec, graph: Graph) -> None:
        if (
            spec.fault != self.name
            or not applicable(self.name, spec.target, graph, set(JOURNEYS))
            or not 5 <= spec.duration_s <= 120
            or spec.intensity is None
            or not 2 <= spec.intensity <= 8
        ):
            raise SafetyError(
                "Traffic target/intensity/duration must pass graph and policy checks."
            )

    def apply(self, ctx: FaultContext) -> None:
        self.validate(ctx.spec, ctx.graph)
        with file_lock(ctx.settings.operation_lock_path):
            self._check_cancelled(ctx)
            generator = ctx.generator
            if generator is None or not 1 <= generator.worker_count <= 75:
                raise SafetyError("Traffic spike requires prepared steady workers.")
            total = math.ceil(generator.worker_count * ctx.spec.intensity)
            if total > 150:
                raise SafetyError("Traffic spec exceeds 150 workers; policy gate required.")
            # Scope every injectable participant of the selected mapped journey.
            names = {
                name
                for flow in ctx.graph.flows
                if ctx.spec.target == "all" or flow.name == ctx.spec.target
                for name in flow.path
            }
            for node in ctx.graph.nodes:
                if node.id in names and node.injectable:
                    container = scope_guard(
                        node.id, ctx.graph, ctx.client, ctx.settings.compose_project
                    )
                    if container.status != "running" or container.attrs.get("State", {}).get(
                        "Paused"
                    ):
                        raise SafetyError("Traffic participants must be running and unpaused.")
            self._check_cancelled(ctx)
            ctx.record = ctx.journal.record_load(generator)
            self._check_cancelled(ctx)
            generator.set_workers(
                total, journey=None if ctx.spec.target == "all" else ctx.spec.target
            )

    def _apply(self, container: Container) -> None:
        raise SafetyError("Traffic spike uses workers, never a container mutation.")

    def describe(self) -> str:
        return "Add bounded journey workers; join extra workers on undo."
