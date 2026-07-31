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
    SafeMode,
    SensorFrame,
)
from .dtc import DtcCode, DtcStore
from .identity import ECU_FLASH_ID, EcuBuildManifest
from .limits import (
    ECU_BRAIN_TIMEOUT_S,
    ECU_CONTROLLED_SHUTDOWN_S,
    ECU_DC_BUS_CONTINUOUS_W,
    ECU_DC_PRECHARGE_READY_V,
    ECU_LATENCY_BUDGET_S,
    ECU_SENSOR_STALE_S,
    ECU_WALL_DERATE_C,
    ECU_WALL_INHIBIT_C,
)
from .runtime import EcuTickResult, VehicleEcuRuntime
from .safety import ControlledShutdown, SafeState
from .watchdog import Watchdog

__all__ = [
    "ActuatorCommand",
    "BrainSetpointFrame",
    "BrainToEcuBridge",
    "CartridgeSetpoint",
    "ControlledShutdown",
    "DtcCode",
    "DtcStore",
    "ECU_BRAIN_TIMEOUT_S",
    "ECU_CONTROLLED_SHUTDOWN_S",
    "ECU_DC_BUS_CONTINUOUS_W",
    "ECU_DC_PRECHARGE_READY_V",
    "ECU_FLASH_ID",
    "ECU_LATENCY_BUDGET_S",
    "ECU_SENSOR_STALE_S",
    "ECU_WALL_DERATE_C",
    "ECU_WALL_INHIBIT_C",
    "EcuBuildManifest",
    "EcuTickResult",
    "ModeCode",
    "SafeMode",
    "SafeState",
    "SensorFrame",
    "VehicleEcuRuntime",
    "Watchdog",
]
