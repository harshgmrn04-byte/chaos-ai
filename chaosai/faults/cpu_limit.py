"""CFS limits with exact, verified restoration through the installed SDK."""

from docker.models.containers import Container

from chaosai.faults.base import Fault, FaultContext
from chaosai.models import ExperimentSpec, Graph, UndoRecord
from chaosai.safety import SafetyError


class CpuLimitFault(Fault):
    name = "cpu_limit"
    kinds = {"app", "database"}

    def validate(self, spec: ExperimentSpec, graph: Graph) -> None:
        super().validate(spec, graph)
        if spec.intensity is None or not 0.1 <= spec.intensity <= 0.5:
            raise SafetyError("CPU intensity must pass the policy gate first.")

    def check_original(self, container: Container) -> None:
        host = container.attrs.get("HostConfig", {})
        if host.get("NanoCpus", 0):
            raise SafetyError("NanoCpus conflicts with CFS updates; refusing CPU mutation.")
        if host.get("CpuQuota", 0) == 0:
            raise SafetyError(
                "Docker cannot restore an original zero CpuQuota exactly via update; "
                "refusing CPU mutation (explicit unlimited quota -1 is supported)."
            )

    def apply_container(self, container: Container, ctx: FaultContext) -> None:
        if not isinstance(ctx.record, UndoRecord) or ctx.spec.intensity is None:
            raise SafetyError("CPU update requires a durable container undo record.")
        self.check_original(container)
        # A zero period is an implicit 100000us default. Keep its metadata unchanged.
        period = ctx.record.original.cpu_period
        options = {"cpu_quota": int(100000 * ctx.spec.intensity)}
        if period:
            options["cpu_period"] = 100000
        container.update(**options)
        container.reload()
        host = container.attrs.get("HostConfig", {})
        if (
            host.get("CpuQuota", 0) != options["cpu_quota"]
            or host.get("CpuPeriod", 0) != (100000 if period else 0)
            or host.get("NanoCpus", 0) != 0
        ):
            raise SafetyError("CPU update could not be verified; rollback required.")

    def _apply(self, container: Container) -> None:
        raise SafetyError("CPU update requires a journaled fault context.")

    def describe(self) -> str:
        return (
            "Limit CFS CPU; restore original period/quota exactly; reject non-restorable settings."
        )
