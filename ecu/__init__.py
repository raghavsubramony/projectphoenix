"""Vehicle ECU runtime — deterministic Layer-2 software for Phoenix builds.

This package is the code that runs on the vehicle ECUs. The ATPE Brain
(``atpe_brain``) only emits setpoints; these ECUs apply, rate-limit, watchdog,
and hold safe-state. No digital-twin dependency on the hot path.

Subsystems:
  Motion ECU       stroke / enable / load authority
  Combustion ECU   ignition timing scale
  Generator ECU    generator force scale
  Buffer ECU       PCMRITMS assist / burst authority (fast loop)
  Ring ECU         per-slot arbitration for 4/6/2 cartridge ring
"""

from .brain_bridge import BrainToEcuBridge
from .bus import (
    ActuatorCommand,
    BrainSetpointFrame,
    CartridgeSetpoint,
    ModeCode,
    SensorFrame,
)
from .identity import ECU_FLASH_ID, EcuBuildManifest
from .runtime import EcuTickResult, VehicleEcuRuntime
from .safety import SafeState
from .watchdog import Watchdog

__all__ = [
    "ActuatorCommand",
    "BrainSetpointFrame",
    "BrainToEcuBridge",
    "CartridgeSetpoint",
    "ECU_FLASH_ID",
    "EcuBuildManifest",
    "EcuTickResult",
    "ModeCode",
    "SafeState",
    "SensorFrame",
    "VehicleEcuRuntime",
    "Watchdog",
]
