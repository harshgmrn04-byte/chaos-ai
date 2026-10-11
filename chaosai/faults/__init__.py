"""The Phase 5 fault catalogue."""

from chaosai.faults.base import Fault
from chaosai.faults.kill import KillFault
from chaosai.faults.pause import PauseFault
from chaosai.faults.stop import StopFault
from chaosai.safety import SafetyError


def get_fault(name: str) -> Fault:
    implementations = {"kill": KillFault, "stop": StopFault, "pause": PauseFault}
    if name not in implementations:
        raise SafetyError("Unsupported fault; choose kill, stop or pause.")
    return implementations[name]()
