"""Deterministic per-domain ECU laws (Layer-2). Soft-real-time, no twin deps."""

from __future__ import annotations

from dataclasses import dataclass

from .bus import CartridgeSetpoint, SensorFrame


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class MotionEcu:
    """Stroke / enable / load authority with slew limiting."""

    load_slew_per_s: float = 2.0  # full-scale units per second
    _load: dict[int, float] | None = None

    def __post_init__(self) -> None:
        self._load = {}

    def step(
        self,
        setpoints: tuple[CartridgeSetpoint, ...],
        dt_s: float,
    ) -> dict[int, tuple[bool, float]]:
        out: dict[int, tuple[bool, float]] = {}
        max_step = self.load_slew_per_s * max(dt_s, 1e-6)
        assert self._load is not None
        for sp in setpoints:
            target = sp.load_scale if sp.enabled else 0.0
            prev = self._load.get(sp.slot_index, 0.0)
            delta = _clamp(target - prev, -max_step, max_step)
            applied = _clamp(prev + delta, 0.0, 1.0)
            self._load[sp.slot_index] = applied
            out[sp.slot_index] = (sp.enabled and applied > 0.01, applied)
        return out


@dataclass
class CombustionEcu:
    """Ignition scale authority — Brain sets scale; ECU clamps and freezes on inhibit."""

    max_scale: float = 1.15
    min_scale: float = 0.70

    def step(
        self,
        setpoints: tuple[CartridgeSetpoint, ...],
        *,
        thermal_inhibit: set[int] | None = None,
    ) -> dict[int, float]:
        inhibit = thermal_inhibit or set()
        out: dict[int, float] = {}
        for sp in setpoints:
            if not sp.enabled or sp.slot_index in inhibit:
                out[sp.slot_index] = 0.0
            else:
                out[sp.slot_index] = _clamp(sp.ignition_scale, self.min_scale, self.max_scale)
        return out


@dataclass
class GeneratorEcu:
    """Generator force scale — slew + derate near thermal limit."""

    force_slew_per_s: float = 3.0
    wall_derate_c: float = 195.0
    _force: dict[int, float] | None = None

    def __post_init__(self) -> None:
        self._force = {}

    def step(
        self,
        setpoints: tuple[CartridgeSetpoint, ...],
        sensors: SensorFrame,
        dt_s: float,
    ) -> dict[int, float]:
        out: dict[int, float] = {}
        max_step = self.force_slew_per_s * max(dt_s, 1e-6)
        assert self._force is not None
        for sp in setpoints:
            target = sp.generator_force_scale if sp.enabled else 0.0
            wall = sensors.slot_wall_temp_c[sp.slot_index] if sp.slot_index < len(sensors.slot_wall_temp_c) else 180.0
            if wall > self.wall_derate_c:
                target *= max(0.5, 1.0 - (wall - self.wall_derate_c) / 20.0)
            prev = self._force.get(sp.slot_index, 0.0)
            delta = _clamp(target - prev, -max_step, max_step)
            applied = _clamp(prev + delta, 0.0, 1.2)
            self._force[sp.slot_index] = applied
            out[sp.slot_index] = applied
        return out


@dataclass
class BufferEcu:
    """PCMRITMS fast-loop authority — clamps assist/burst to continuous + peak rating."""

    continuous_w: float = 40_000.0
    peak_w: float = 120_000.0
    reserve_soc: float = 0.15

    def step(
        self,
        *,
        assist_w: float,
        burst_w: float,
        precharge_w: float,
        soc: float,
        brain_safe: bool,
    ) -> tuple[float, float, float]:
        if not brain_safe or soc < self.reserve_soc:
            return 0.0, 0.0, max(0.0, precharge_w)
        assist = _clamp(assist_w, 0.0, self.continuous_w)
        burst = _clamp(burst_w, 0.0, self.peak_w)
        precharge = _clamp(precharge_w, 0.0, self.continuous_w * 0.5)
        return assist, burst, precharge
