"""The Phase 5 fault catalogue."""

from chaosai.faults.base import Fault
from chaosai.faults.cpu_limit import CpuLimitFault
from chaosai.faults.kill import KillFault
from chaosai.faults.pause import PauseFault
from chaosai.faults.stop import StopFault
from chaosai.faults.traffic_spike import TrafficSpikeFault
from chaosai.safety import SafetyError


def get_fault(name: str) -> Fault:
    implementations = {
        "kill": KillFault,
        "stop": StopFault,
        "pause": PauseFault,
        "cpu_limit": CpuLimitFault,
        "traffic_spike": TrafficSpikeFault,
    }
    if name not in implementations:
        raise SafetyError(
            "Unsupported fault; choose kill, stop, pause, cpu_limit or traffic_spike."
        )
    return implementations[name]()
