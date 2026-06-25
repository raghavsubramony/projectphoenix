"""Synthetic drive cycles: time series of (time, speed, grade)."""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class DriveCycle:
    """A drive cycle sampled at fixed dt. speeds in m/s, grades in radians."""

    name: str
    dt_s: float
    speeds_ms: list[float]
    grades_rad: list[float]

    def __post_init__(self) -> None:
        if len(self.speeds_ms) != len(self.grades_rad):
            raise ValueError("speeds and grades must have equal length")

    @property
    def duration_s(self) -> float:
        return self.dt_s * (len(self.speeds_ms) - 1)

    def accelerations(self) -> list[float]:
        """Central-difference accelerations (m/s^2), same length as speeds."""
        v = self.speeds_ms
        n = len(v)
        acc = [0.0] * n
        for i in range(n):
            if i == 0:
                acc[i] = (v[1] - v[0]) / self.dt_s if n > 1 else 0.0
            elif i == n - 1:
                acc[i] = (v[i] - v[i - 1]) / self.dt_s
            else:
                acc[i] = (v[i + 1] - v[i - 1]) / (2 * self.dt_s)
        return acc


def _kmh(v: float) -> float:
    return v / 3.6


def _ramp(profile: list[float], dt_s: float, name: str,
          grade_fn=None) -> DriveCycle:
    grades = [0.0] * len(profile)
    if grade_fn is not None:
        grades = [grade_fn(i * dt_s) for i in range(len(profile))]
    return DriveCycle(name, dt_s, profile, grades)


def _segment(speeds: list[float], target_kmh: float, hold_s: float,
             accel_kmh_s: float, dt_s: float) -> None:
    """Append an accel-to-target then hold segment (in km/h) to `speeds`."""
    current = speeds[-1] * 3.6 if speeds else 0.0
    # Acceleration / deceleration phase.
    step_kmh = accel_kmh_s * dt_s
    if target_kmh >= current:
        while current < target_kmh - 1e-9:
            current = min(target_kmh, current + step_kmh)
            speeds.append(_kmh(current))
    else:
        while current > target_kmh + 1e-9:
            current = max(target_kmh, current - step_kmh)
            speeds.append(_kmh(current))
    # Hold phase.
    for _ in range(int(hold_s / dt_s)):
        speeds.append(_kmh(target_kmh))


class DriveCycles:
    """Factory of representative synthetic cycles."""

    @staticmethod
    def urban(duration_s: float = 600, dt_s: float = 1.0) -> DriveCycle:
        """Stop-and-go city driving: repeated 0 -> 45 km/h surges with stops."""
        speeds: list[float] = [0.0]
        while speeds and len(speeds) * dt_s < duration_s:
            _segment(speeds, 45, hold_s=8, accel_kmh_s=6, dt_s=dt_s)
            _segment(speeds, 20, hold_s=4, accel_kmh_s=5, dt_s=dt_s)
            _segment(speeds, 0, hold_s=6, accel_kmh_s=5, dt_s=dt_s)
        return _ramp(speeds, dt_s, "Urban stop-go")

    @staticmethod
    def highway(duration_s: float = 900, dt_s: float = 1.0) -> DriveCycle:
        """Sustained 100-120 km/h cruise with mild overtakes."""
        speeds: list[float] = [0.0]
        _segment(speeds, 100, hold_s=120, accel_kmh_s=4, dt_s=dt_s)
        while len(speeds) * dt_s < duration_s:
            _segment(speeds, 120, hold_s=60, accel_kmh_s=3, dt_s=dt_s)
            _segment(speeds, 100, hold_s=120, accel_kmh_s=3, dt_s=dt_s)
        return _ramp(speeds, dt_s, "Highway cruise")

    @staticmethod
    def towing_grade(duration_s: float = 900, dt_s: float = 1.0) -> DriveCycle:
        """Highway towing with a sustained climb (worst-case sustained load)."""
        speeds: list[float] = [0.0]
        _segment(speeds, 90, hold_s=120, accel_kmh_s=3, dt_s=dt_s)
        while len(speeds) * dt_s < duration_s:
            _segment(speeds, 90, hold_s=180, accel_kmh_s=2, dt_s=dt_s)

        def grade(t: float) -> float:
            # 6% grade between t=200s and t=600s, flat otherwise.
            return math.atan(0.06) if 200 <= t <= 600 else 0.0

        return _ramp(speeds, dt_s, "Towing + 6% grade", grade_fn=grade)

    @staticmethod
    def mixed(duration_s: float = 1800, dt_s: float = 1.0) -> DriveCycle:
        """Urban + highway + a short aggressive sprint, concatenated."""
        speeds: list[float] = [0.0]
        # Urban block.
        for _ in range(4):
            _segment(speeds, 45, hold_s=8, accel_kmh_s=6, dt_s=dt_s)
            _segment(speeds, 0, hold_s=6, accel_kmh_s=5, dt_s=dt_s)
        # Highway block.
        _segment(speeds, 110, hold_s=300, accel_kmh_s=4, dt_s=dt_s)
        # Aggressive sprint (exercises buffer + Tier 3).
        _segment(speeds, 140, hold_s=15, accel_kmh_s=12, dt_s=dt_s)
        _segment(speeds, 90, hold_s=120, accel_kmh_s=6, dt_s=dt_s)
        # Pad with cruising to reach the requested duration.
        while len(speeds) * dt_s < duration_s:
            _segment(speeds, 90, hold_s=60, accel_kmh_s=3, dt_s=dt_s)
        return _ramp(speeds, dt_s, "Mixed (urban+highway+sprint)")

    @staticmethod
    def transient_stress(repeats: int = 8, dt_s: float = 1.0) -> DriveCycle:
        """Back-to-back hard launches with brief recovery dips.

        Designed to keep the inertial buffer continuously engaged: each launch
        is an aggressive 0->100 km/h sprint, followed by a short partial lift
        (not a full stop) so the buffer only partially recharges before the next
        surge. Under a realistic generation slew limit this is where the buffer
        (and its rotor-coupled burst rating) earns its keep.
        """
        speeds: list[float] = [0.0]
        for _ in range(repeats):
            # Aggressive launch to 100 km/h, brief hold at speed.
            _segment(speeds, 100, hold_s=3, accel_kmh_s=14, dt_s=dt_s)
            # Partial lift to 60 km/h (keeps load high, buffer barely recovers).
            _segment(speeds, 60, hold_s=2, accel_kmh_s=10, dt_s=dt_s)
        # Recovery cruise to settle the buffer/battery state.
        _segment(speeds, 80, hold_s=20, accel_kmh_s=4, dt_s=dt_s)
        return _ramp(speeds, dt_s, "Transient stress (hard launches)")
