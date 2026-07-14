"""Cycle-resolved combustion variability."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from designs.phoenix_v3.config import PhoenixV3Config, CycleState, TransientFault
from designs.phoenix_v3.constants import GAMMA, R_AIR
from designs.phoenix_v3.physics import wiebe_fraction


@dataclass(frozen=True)
class CycleCombustionDraw:
    cycle_index: int
    fuel_energy_j: float
    ignition_offset_ms: float
    load_scale: float
    partial_burn: bool


class StochasticCombustionModel:
    """Per-cycle perturbations to fuel energy, timing, and burn completeness."""

    def __init__(self, cfg: PhoenixV3Config) -> None:
        seed = cfg.combustion_rng_seed
        self._rng = np.random.default_rng(seed)
        self._draws: dict[int, CycleCombustionDraw] = {}

    def reset(self) -> None:
        self._draws.clear()

    def draw_for_cycle(self, cycle_index: int, cfg: PhoenixV3Config) -> CycleCombustionDraw:
        if cycle_index in self._draws:
            return self._draws[cycle_index]
        if not cfg.stochastic_combustion_enabled:
            draw = CycleCombustionDraw(
                cycle_index=cycle_index,
                fuel_energy_j=cfg.fuel_energy_j,
                ignition_offset_ms=0.0,
                load_scale=1.0,
                partial_burn=False,
            )
        else:
            jitter = cfg.fuel_energy_jitter_frac
            fuel_scale = float(self._rng.uniform(1.0 - jitter, 1.0 + jitter))
            ignition_offset = float(
                self._rng.uniform(-cfg.ignition_jitter_ms, cfg.ignition_jitter_ms)
            )
            partial = bool(self._rng.random() < cfg.partial_burn_probability)
            load_scale = cfg.partial_burn_fraction if partial else 1.0
            draw = CycleCombustionDraw(
                cycle_index=cycle_index,
                fuel_energy_j=cfg.fuel_energy_j * fuel_scale,
                ignition_offset_ms=ignition_offset,
                load_scale=load_scale,
                partial_burn=partial,
            )
        self._draws[cycle_index] = draw
        return draw

    def combustion_update(
        self,
        t_ms: float,
        burn_frac: float,
        state: CycleState,
        *,
        cycle_index: int,
        cfg: PhoenixV3Config,
        fault: TransientFault | None,
        time_scale: float,
    ) -> tuple[float, float, float, float]:
        fuel_delta_j = 0.0
        if fault and cycle_index >= fault.trigger_cycle:
            if fault.kind == "misfire" and cycle_index == fault.trigger_cycle:
                return state.pressure_pa, state.temperature_k, burn_frac, fuel_delta_j
            if fault.kind == "injector_failure":
                return state.pressure_pa, state.temperature_k, burn_frac, fuel_delta_j
            if fault.kind == "pressure_sensor_fault":
                q_scale = 0.85
            else:
                q_scale = 1.0
        else:
            q_scale = 1.0

        draw = self.draw_for_cycle(cycle_index, cfg)
        ignition_ms = (cfg.ignition_ms + draw.ignition_offset_ms) * time_scale
        target = (
            wiebe_fraction(t_ms, ignition_ms, cfg.burn_duration_ms * time_scale)
            * cfg.load_fraction
            * draw.load_scale
        )
        delta = max(0.0, target - burn_frac)
        if delta <= 0.0:
            return state.pressure_pa, state.temperature_k, burn_frac, fuel_delta_j

        q = delta * draw.fuel_energy_j * cfg.combustion_pressure_gain * q_scale
        fuel_delta_j = q
        m = state.mass_kg
        u = m * R_AIR * state.temperature_k / (GAMMA - 1.0) + q
        t_new = max(state.temperature_k, (GAMMA - 1.0) * u / (m * R_AIR))
        from designs.phoenix_v3.physics import chamber_volume_m3

        v = chamber_volume_m3(state.x_a_m, state.x_b_m, cfg)
        p_new = m * R_AIR * t_new / v
        p_new = min(p_new, cfg.max_pressure_bar * 1e5)
        burn_frac = target
        return p_new, t_new, burn_frac, fuel_delta_j
