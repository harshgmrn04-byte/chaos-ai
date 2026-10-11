"""Pause a scoped injectable local container."""

from docker.models.containers import Container

from chaosai.faults.base import Fault


class PauseFault(Fault):
    name = "pause"
    kinds = {"app", "database", "cache", "broker", "tool"}

    def _apply(self, container: Container) -> None:
        container.pause()

    def describe(self) -> str:
        return "Pause container; unpause on undo."
