"""Stop a scoped service with a five-second Docker shutdown timeout."""

from docker.models.containers import Container

from chaosai.faults.base import Fault


class StopFault(Fault):
    name = "stop"
    kinds = {"app", "database", "cache", "broker"}

    def _apply(self, container: Container) -> None:
        container.stop(timeout=5)

    def describe(self) -> str:
        return "Stop with timeout=5; start on undo."
