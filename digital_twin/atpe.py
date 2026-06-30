"""ATPE: three-tier free-piston linear generator with additive tier selection."""

from __future__ import annotations

from dataclasses import dataclass

from .config import (
    ATPEConfig,
    GASOLINE_CO2_KG_PER_L,
    GASOLINE_DENSITY_KG_PER_L,
    GASOLINE_LHV_MJ_PER_KG,
    TierSpec,
)
from .single_cylinder import gate1_point_from_load

_LHV_J_PER_KG = GASOLINE_LHV_MJ_PER_KG * 1e6


@dataclass
class GenerationResult:
    electric_w: float       # electrical power actually generated
    fuel_power_w: float     # chemical power consumed
    fuel_l: float           # fuel volume consumed this step
    co2_kg: float           # tailpipe CO2 this step
    active_tier: str        # name of the governing (largest active) tier
    active_index: int       # -1 = engine off, else tier index
    efficiency: float       # instantaneous thermal efficiency (0 if off)
    imep_bar: float = 0.0
    knock_index: float = 0.0
    peak_pressure_bar: float = 0.0
    predicted_tdc_mm: float = 0.0


class ATPE:
    """Free-piston generator. Picks the smallest tier set covering the setpoint."""

    def __init__(self, cfg: ATPEConfig) -> None:
        self.cfg = cfg
        # Cumulative capacity when tiers 0..i are all active.
        self._cumulative: list[float] = []
        running = 0.0
        for tier in cfg.tiers:
            running += tier.max_electric_w
            self._cumulative.append(running)
        # Last delivered electrical output, for the optional ramp-rate limit.
        self._prev_electric_w = 0.0

    @property
    def max_electric_w(self) -> float:
        return self.cfg.max_electric_w

    def _governing_tier(self, setpoint_w: float) -> tuple[int, TierSpec | None]:
        """Smallest cumulative tier set whose capacity covers the setpoint."""
        if setpoint_w <= 0.0:
            return -1, None
        for i, cap in enumerate(self._cumulative):
            if setpoint_w <= cap + 1e-6:
                return i, self.cfg.tiers[i]
        # Exceeds total capacity: run everything, governed by the top tier.
        top = len(self.cfg.tiers) - 1
        return top, self.cfg.tiers[top]

    def generate(self, setpoint_w: float, dt_s: float) -> GenerationResult:
        """Generate electricity toward `setpoint_w`, returning fuel/emissions."""
        setpoint_w = max(0.0, min(setpoint_w, self.max_electric_w))
        # Optional ramp-rate limit: the generator can only chase the setpoint as
        # fast as `max_slew_w_per_s`. This is what makes the inertial buffer
        # necessary during transients instead of merely convenient.
        slew = self.cfg.max_slew_w_per_s
        if slew is not None:
            max_step = slew * dt_s
            if setpoint_w > self._prev_electric_w + max_step:
                setpoint_w = self._prev_electric_w + max_step
            elif setpoint_w < self._prev_electric_w - max_step:
                setpoint_w = self._prev_electric_w - max_step
        self._prev_electric_w = setpoint_w
        index, tier = self._governing_tier(setpoint_w)
        if tier is None or setpoint_w <= 0.0:
            return GenerationResult(0.0, 0.0, 0.0, 0.0, "engine off", -1, 0.0)

        electric_w = setpoint_w
        efficiency = tier.thermal_efficiency
        imep_bar = 0.0
        knock_index = 0.0
        peak_pressure_bar = 0.0
        predicted_tdc_mm = 0.0

        gate1 = self.cfg.gate1
        if gate1 is not None and gate1.enabled:
            tier_cap = max(1.0, self._cumulative[index])
            load_fraction = max(0.05, min(1.0, electric_w / tier_cap))
            point = gate1_point_from_load(
                speed_rpm=gate1.reference_speed_rpm,
                load_fraction=load_fraction,
                displacement_cc=tier.displacement_cc,
                generator_efficiency=self.cfg.generator_efficiency,
                prefer_cantera=gate1.prefer_cantera,
                tier_index=index,
            )
            # Keep Gate 1 map physically plausible and near tier baseline.
            efficiency = max(0.10, min(point.electric_efficiency, 0.58))
            imep_bar = point.imep_bar
            knock_index = point.knock_index
            peak_pressure_bar = point.peak_pressure_bar
            predicted_tdc_mm = point.predicted_tdc_mm

        fuel_power_w = electric_w / efficiency
        fuel_energy_j = fuel_power_w * dt_s
        fuel_kg = fuel_energy_j / _LHV_J_PER_KG
        fuel_l = fuel_kg / GASOLINE_DENSITY_KG_PER_L
        co2_kg = fuel_l * GASOLINE_CO2_KG_PER_L
        return GenerationResult(
            electric_w=electric_w,
            fuel_power_w=fuel_power_w,
            fuel_l=fuel_l,
            co2_kg=co2_kg,
            active_tier=tier.name,
            active_index=index,
            efficiency=efficiency,
            imep_bar=imep_bar,
            knock_index=knock_index,
            peak_pressure_bar=peak_pressure_bar,
            predicted_tdc_mm=predicted_tdc_mm,
        )
