"""Kill an application and observe whether it restarts without our intervention."""

from docker.models.containers import Container

from chaosai.faults.base import Fault, FaultContext
from chaosai.safety import file_lock, scope_guard


class KillFault(Fault):
    name = "kill"
    kinds = {"app"}

    def _apply(self, container: Container) -> None:
        container.kill()

    def observe(self, ctx: FaultContext) -> None:
        with file_lock(ctx.settings.operation_lock_path):
            if ctx.record is None or ctx.journal.is_done(ctx.record.id):
                return
            container = scope_guard(
                ctx.spec.target, ctx.graph, ctx.client, ctx.settings.compose_project
            )
            if container.id != ctx.record.container_id:
                return
            if ctx.self_healed is None:
                ctx.self_healed = False
            if container.status == "running":
                ctx.self_healed = True

    def describe(self) -> str:
        return "Kill application; observe self-heal; start if still stopped on undo."
