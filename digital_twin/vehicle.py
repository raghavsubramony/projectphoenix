"""Vehicle longitudinal dynamics: drive cycle -> DC-bus power demand."""

from __future__ import annotations

import math

from .config import AIR_DENSITY, GRAVITY, VehicleConfig


class Vehicle:
    """Computes the electrical power the traction system draws from the DC bus."""

    def __init__(self, cfg: VehicleConfig) -> None:
        self.cfg = cfg

    def tractive_force_n(self, speed_ms: float, accel_ms2: float,
                         grade_rad: float) -> float:
        """Net tractive force at the wheels (N). Negative means braking."""
        c = self.cfg
        f_inertia = c.mass_kg * accel_ms2
        f_grade = c.mass_kg * GRAVITY * math.sin(grade_rad)
        f_roll = c.rolling_resistance * c.mass_kg * GRAVITY * math.cos(grade_rad)
        f_aero = (0.5 * AIR_DENSITY * c.drag_coefficient * c.frontal_area_m2
                  * speed_ms * speed_ms)
        return f_inertia + f_grade + f_roll + f_aero

    def power_demand_w(self, speed_ms: float, accel_ms2: float,
                       grade_rad: float) -> float:
        """DC-bus electrical power demand (W). Positive = draw, negative = regen."""
        c = self.cfg
        wheel_power = self.tractive_force_n(speed_ms, accel_ms2, grade_rad) * speed_ms
        if wheel_power >= 0.0:
            # Motoring: bus must supply more than the wheels need (driveline + motor losses).
            bus_power = wheel_power / c.driveline_efficiency
        else:
            # Braking: only a fraction of kinetic energy returns to the bus.
            bus_power = wheel_power * c.regen_efficiency
        return bus_power + c.aux_load_w
