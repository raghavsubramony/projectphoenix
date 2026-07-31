"""Diagnostic trouble codes — latched until explicitly cleared."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class DtcCode(str, Enum):
    """UDS-style sparse codes for lab / mule diagnostics."""

    BRAIN_TIMEOUT = "P1001_BRAIN_TIMEOUT"
    BRAIN_NOT_ALIVE = "P1002_BRAIN_NOT_ALIVE"
    CYCLE_OVERRUN = "P1003_CYCLE_OVERRUN"
    NO_BRAIN_FRAME = "P1004_NO_BRAIN_FRAME"
    SENSOR_STALE = "P1101_SENSOR_STALE"
    SENSOR_INVALID_SOC = "P1102_SENSOR_INVALID_SOC"
    SENSOR_INVALID_WALL = "P1103_SENSOR_INVALID_WALL"
    SENSOR_INVALID_POSITION = "P1104_SENSOR_INVALID_POSITION"
    WALL_OVERTEMP = "P1201_WALL_OVERTEMP"
    DC_PRECHARGE_NOT_READY = "P1301_DC_PRECHARGE_NOT_READY"
    CONTROLLED_SHUTDOWN = "U1001_CONTROLLED_SHUTDOWN"
    EMERGENCY_OFF = "U1002_EMERGENCY_OFF"


@dataclass
class DtcStore:
    """Latches active DTCs; clear requires an explicit recover action."""

    _active: set[DtcCode] = field(default_factory=set)
    _history: list[DtcCode] = field(default_factory=list)

    def raise_(self, code: DtcCode) -> None:
        if code not in self._active:
            self._history.append(code)
        self._active.add(code)

    def clear(self) -> None:
        self._active.clear()

    def clear_code(self, code: DtcCode) -> None:
        self._active.discard(code)

    @property
    def active(self) -> tuple[DtcCode, ...]:
        return tuple(sorted(self._active, key=lambda c: c.value))

    @property
    def active_names(self) -> tuple[str, ...]:
        return tuple(c.value for c in self.active)

    def has(self, code: DtcCode) -> bool:
        return code in self._active
