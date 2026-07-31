"""Fixed ECU bus messages — shared contract for Python runtime and C firmware.

Units are SI on the wire for the Python reference. C firmware uses the same
field order with scaled fixed-point where annotated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum


class ModeCode(IntEnum):
    """Dispatch mode codes — must match C firmware enum in firmware/c/ecu_bus.h."""

    OFF = 0
    IDLE = 1
    CITY = 2
    HIGHWAY = 3
    OVERTAKE = 4
    TRACK = 5


class SafeMode(IntEnum):
    """Plant protection mode — orthogonal to dispatch ModeCode."""

    NORMAL = 0
    CONTROLLED_SHUTDOWN = 1  # ramp load off, then kill ignition
    EMERGENCY_OFF = 2  # immediate fail-OFF (watchdog / hard fault)


@dataclass(frozen=True)
class CartridgeSetpoint:
    """Per-slot authority from the ATPE Brain."""

    slot_index: int
    enabled: bool
    load_scale: float  # 0..1
    ignition_scale: float = 1.0
    generator_force_scale: float = 1.0


@dataclass(frozen=True)
class BrainSetpointFrame:
    """Slow-loop setpoints from ATPE Brain → Ring ECU (typically 50–100 ms)."""

    sequence: int
    mode: ModeCode
    demand_w: float
    target_power_w: float
    cartridges: tuple[CartridgeSetpoint, ...]
    buffer_assist_w: float = 0.0
    buffer_precharge_w: float = 0.0
    buffer_burst_w: float = 0.0
    brain_alive: bool = True


@dataclass
class SensorFrame:
    """Fast-loop sensor snapshot for one ECU cycle (10 ms).

    ``stamped_t_s is None`` means the sample is taken on the current ECU tick
    (age 0). Set an older stamp in HIL to model bus/DAQ delay.
    """

    t_s: float = 0.0
    stamped_t_s: float | None = None
    valid: bool = True
    bus_demand_w: float = 0.0
    bus_voltage_v: float = 400.0
    buffer_soc: float = 0.7
    buffer_soc_valid: bool = True
    buffer_w: float = 0.0
    coolant_temp_k: float = 293.15
    slot_wall_temp_c: list[float] = field(default_factory=lambda: [180.0] * 12)
    wall_temp_valid: list[bool] = field(default_factory=lambda: [True] * 12)
    slot_gen_temp_c: list[float] = field(default_factory=lambda: [120.0] * 12)
    slot_position_mm: list[float] = field(default_factory=lambda: [0.0] * 12)
    position_valid: list[bool] = field(default_factory=lambda: [True] * 12)
    slot_pressure_bar: list[float] = field(default_factory=lambda: [1.0] * 12)
    pressure_valid: list[bool] = field(default_factory=lambda: [True] * 12)


@dataclass(frozen=True)
class SlotActuatorOut:
    """Actuator commands the Motion/Combustion/Generator ECUs emit per slot."""

    slot_index: int
    enable: bool
    load_fraction: float
    ignition_scale: float
    generator_force_scale: float
    valve_authority: float  # 0 = closed schedule freeze, 1 = normal


@dataclass(frozen=True)
class ActuatorCommand:
    """Full ring + buffer command after one ECU tick."""

    sequence_ack: int
    mode: ModeCode
    slots: tuple[SlotActuatorOut, ...]
    buffer_assist_w: float
    buffer_burst_w: float
    buffer_precharge_w: float
    safe_state: bool
    watchdog_ok: bool
    latency_budget_ok: bool
    notes: str = ""
    safe_mode: SafeMode = SafeMode.NORMAL
    active_dtcs: tuple[str, ...] = ()
