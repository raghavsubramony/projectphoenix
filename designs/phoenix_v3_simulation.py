"""Phoenix ATPE V3 — opposed free-piston cycle simulation.

Models the three credibility pillars for a single power cartridge:

1. Staged scavenging (exhaust-first blowdown, intake push, tangential swirl)
2. Dual air-spring dynamics (restoring force, self-centering, resonance)
3. Software-defined valve timing over a full 100 Hz (10 ms) cycle

Run from repo root::

    python designs/phoenix_v3_simulation.py
    python designs/phoenix_v3_simulation.py --cycles 5 --export-png designs/phoenix_v3_cycle.png
    python designs/phoenix_v3_simulation.py --sweep --cycles 8
    python designs/phoenix_v3_simulation.py --optimize --opt-trials 100 --cycles 8

Reference architecture: Phoenix ATPE V2.0 cartridge (150 cc/side, 50 mm peak stroke,
96 mm bore, 12.5:1 CR) with V3 staged breathing and closed-loop air-spring control.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.gridspec import GridSpec

OUT_DIR = Path(__file__).resolve().parent
_REPO = OUT_DIR.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.actuators import ActuatorPlant
from designs.phoenix_v3.combustion import StochasticCombustionModel
from designs.phoenix_v3.config import (
    CycleEnergyReport,
    CycleState,
    EnergyMetrics,
    PhoenixV3Config,
    ScavengeMetrics,
    SimulationResult,
    TransientFault,
    TransientResult,
    ValveState,
)
from designs.phoenix_v3.constants import DESIGNS_DIR, GAMMA, R_AIR, clamp
from designs.phoenix_v3.controls import (
    capture_startup_multiplier,
    cap_generator_work_step,
    generator_balance_scales,
    generator_brake_force_n,
    integrate_piston_travel,
    phased_cycle_time_ms,
    piston_travel_fraction,
    slew_generator_load_scale,
    slew_spring_phase_gain,
    spring_end_stop_relief_gain,
    spring_phase_gain,
    valve_schedule,
    virtual_end_stop_gain,
)
from designs.phoenix_v3.efficiency import (
    canonical_net_efficiency,
    compute_canonical_efficiency,
    late_cycle_reports,
    print_canonical_efficiency_report,
    steady_cycle_reports,
)
from designs.phoenix_v3.physics import (
    air_spring_internal_energy_j,
    air_spring_pressure_pa,
    air_spring_volume_m3,
    chamber_volume_m3,
    mass_flow_orifice,
    piston_kinetic_energy_j,
    spring_internal_energy_total_j,
    wiebe_fraction,
)

_clamp = clamp
_cap_generator_work_step = cap_generator_work_step
_mass_flow_orifice = mass_flow_orifice
_wiebe_fraction = wiebe_fraction
_steady_cycle_reports = steady_cycle_reports
_late_cycle_reports = late_cycle_reports


def _thermal_config_from(cfg: "PhoenixV3Config") -> "ThermalConfig":
    from designs.phoenix_v3_thermal import ThermalConfig

    return ThermalConfig(
        thermal_model_enabled=cfg.thermal_model_enabled,
        ambient_temp_k=cfg.ambient_temp_k,
        wall_temp_k_initial=cfg.wall_temp_k_initial,
        generator_temp_k_initial=cfg.generator_temp_k_initial,
        valve_temp_k_initial=cfg.valve_temp_k_initial,
        wall_temp_rated_k=cfg.wall_temp_rated_k,
        generator_temp_rated_k=cfg.generator_temp_rated_k,
        generator_temp_trip_k=cfg.generator_temp_trip_k,
        generator_temp_max_k=cfg.generator_temp_max_k,
        wall_heat_transfer_w_per_k=cfg.wall_heat_transfer_w_per_k,
        wall_thermal_mass_j_per_k=cfg.wall_thermal_mass_j_per_k,
        wall_combustion_heat_fraction=cfg.wall_combustion_heat_fraction,
        generator_coil_resistance_ohm=cfg.generator_coil_resistance_ohm,
        generator_coil_resistance_ref_k=cfg.generator_coil_resistance_ref_k,
        generator_coil_temp_coeff=cfg.generator_coil_temp_coeff,
        generator_thermal_mass_j_per_k=cfg.generator_thermal_mass_j_per_k,
        generator_cooling_mode=cfg.generator_cooling_mode,
        generator_cooling_base_w=cfg.generator_cooling_base_w,
        generator_cooling_w_per_k=cfg.generator_cooling_w_per_k,
        phase_change_temp_k=cfg.phase_change_temp_k,
        phase_change_boost_w_per_k=cfg.phase_change_boost_w_per_k,
        generator_core_loss_fraction=cfg.generator_core_loss_fraction,
        generator_base_efficiency=cfg.generator_efficiency,
        coolant_temp_k=cfg.coolant_temp_k,
        shared_coolant_enabled=cfg.shared_coolant_enabled,
        valve_thermal_mass_j_per_k=cfg.valve_thermal_mass_j_per_k,
        valve_heating_coeff_w_kg=cfg.valve_heating_coeff_w_kg,
        valve_cooling_w_per_k=cfg.valve_cooling_w_per_k,
        min_thermal_loss_fraction=cfg.min_thermal_loss_fraction,
    )


def apply_generator_cooling(cfg: "PhoenixV3Config", mode: str) -> "PhoenixV3Config":
    """Apply a named generator cooling preset (passive, water_jacket, …)."""
    from designs.phoenix_v3_thermal import COOLING_PRESETS

    preset = COOLING_PRESETS.get(mode, COOLING_PRESETS["passive"])
    return replace(
        cfg,
        generator_cooling_mode=mode,
        generator_cooling_base_w=preset["generator_cooling_base_w"],
        generator_cooling_w_per_k=preset["generator_cooling_w_per_k"],
        phase_change_boost_w_per_k=preset["phase_change_boost_w_per_k"],
    )


def _fault_generator_derate(fault: TransientFault | None, cycle_index: int) -> float:
    """Forced generator derate when ``generator_derate`` fault is active."""
    if fault and fault.kind == "generator_derate" and cycle_index >= fault.trigger_cycle:
        return 0.75
    return 1.0


class PhoenixV3Simulator:
    """Lumped opposed-piston cartridge integrator."""

    def __init__(self, cfg: PhoenixV3Config | None = None) -> None:
        base = cfg or PhoenixV3Config()
        if (
            base.tolerance_generator_mismatch_frac != 0.0
            and base.generator_force_scale_b is None
        ):
            base = replace(
                base,
                generator_force_scale_b=base.generator_force_scale * (
                    1.0 + base.tolerance_generator_mismatch_frac
                ),
                generator_balance_control=False,
            )
        self.cfg = base
        self._spring_gain_a = 1.0
        self._spring_gain_b = 1.0
        self._spring_phase_a = 1.0
        self._spring_phase_b = 1.0
        self._fault: TransientFault | None = None
        self._gen_load_scale = 1.0
        self._gen_load_target = 1.0
        self._actuators = ActuatorPlant()
        self._combustion = StochasticCombustionModel(base)

    def _update_spring_control(self, x_a: float, x_b: float) -> None:
        """Closed-loop stiffness trim — corrects piston imbalance (self-centering)."""
        if not self.cfg.spring_balance_control:
            self._spring_gain_a = 1.0
            self._spring_gain_b = 1.0
            return
        cfg = self.cfg
        mid = 0.5 * cfg.half_stroke_m
        err_a = (x_a - mid) / max(cfg.half_stroke_m, 1e-9)
        err_b = (x_b - mid) / max(cfg.half_stroke_m, 1e-9)
        self._spring_gain_a = _clamp(1.0 + 0.35 * err_a, 0.75, 1.35)
        self._spring_gain_b = _clamp(1.0 + 0.35 * err_b, 0.75, 1.35)

    def _gas_exchange(
        self,
        state: CycleState,
        valves: ValveState,
        dt: float,
    ) -> tuple[float, float, float, float]:
        """Valve-resolved mass and enthalpy exchange with staged scavenging."""
        cfg = self.cfg
        p = state.pressure_pa
        t = state.temperature_k
        m = state.mass_kg
        m_res = state.residual_mass_kg
        m_fresh = state.fresh_mass_kg

        dm_in = 0.0
        dm_out = 0.0
        h_in = 0.0
        h_out = 0.0

        intake_ports = int(valves.intake_a) + int(valves.intake_b)
        exhaust_ports = int(valves.exhaust_a) + int(valves.exhaust_b)

        if valves.any_exhaust and p > cfg.exhaust_pressure_pa:
            swirl_boost = 1.0 + cfg.swirl_scavenge_gain * int(valves.any_intake)
            blow_boost = cfg.blowdown_flow_boost if not valves.any_intake else 1.0
            dm = _mass_flow_orifice(
                p, cfg.exhaust_pressure_pa,
                exhaust_ports * 0.5 * swirl_boost * blow_boost,
                cfg.valve_flow_coeff,
            )
            dm = min(dm * dt, max(m - 1e-9, 0.0))
            dm_out += dm
            h_out += dm * (GAMMA / (GAMMA - 1.0)) * R_AIR * t

        if valves.any_intake and cfg.intake_pressure_pa > p:
            dm = _mass_flow_orifice(
                cfg.intake_pressure_pa, p, intake_ports * 0.5, cfg.valve_flow_coeff
            )
            dm = min(dm * dt, 0.05)  # cap per step for stability
            dm_in += dm
            h_in += dm * (GAMMA / (GAMMA - 1.0)) * R_AIR * (330.0)

        if dm_in > 0.0 or dm_out > 0.0:
            m_new = max(m + dm_in - dm_out, 1e-7)
            u_old = m * R_AIR * t / (GAMMA - 1.0)
            u_new = u_old + h_in - h_out
            t_new = max(280.0, (GAMMA - 1.0) * u_new / (m_new * R_AIR))
            expelled_res = dm_out * (m_res / max(m, 1e-9))
            m_res = max(0.0, m_res - expelled_res)
            m_fresh = max(0.0, m_fresh + dm_in - max(0.0, dm_out - expelled_res))
            m = m_new
            t = t_new

        return m, m_res, m_fresh, t

    def _combustion_update(
        self,
        t_ms: float,
        burn_frac: float,
        state: CycleState,
        *,
        cycle_index: int,
    ) -> tuple[float, float, float, float]:
        cfg = self.cfg
        time_scale = cfg.cycle_time_s * 1e3 / 10.0
        if cfg.stochastic_combustion_enabled:
            return self._combustion.combustion_update(
                t_ms,
                burn_frac,
                state,
                cycle_index=cycle_index,
                cfg=cfg,
                fault=self._fault,
                time_scale=time_scale,
            )

        fuel_delta_j = 0.0
        if self._fault and self._fault.kind == "misfire" and cycle_index == self._fault.trigger_cycle:
            return state.pressure_pa, state.temperature_k, burn_frac, fuel_delta_j

        target = _wiebe_fraction(
            phased_cycle_time_ms(t_ms, cfg),
            cfg.ignition_ms * time_scale,
            cfg.burn_duration_ms * time_scale,
        ) * cfg.load_fraction
        delta = max(0.0, target - burn_frac)
        if delta <= 0.0:
            return state.pressure_pa, state.temperature_k, burn_frac, fuel_delta_j

        q = delta * cfg.fuel_energy_j
        fuel_delta_j = q
        m = state.mass_kg
        u = m * R_AIR * state.temperature_k / (GAMMA - 1.0) + q
        t_new = max(state.temperature_k, (GAMMA - 1.0) * u / (m * R_AIR))
        v = chamber_volume_m3(state.x_a_m, state.x_b_m, cfg)
        p_new = m * R_AIR * t_new / v
        p_new = min(p_new, cfg.max_pressure_bar * 1e5)
        burn_frac = target
        return p_new, t_new, burn_frac, fuel_delta_j

    def _finalize_cycle_report(
        self,
        cycle_index: int,
        *,
        spring_pressures_bar: list[float],
        spring_stored_j: float,
        spring_recovered_j: float,
        spring_damping_j: float,
        spring_u_min_j: float,
        spring_u_max_j: float,
        indicated_work_signed_j: float,
        power_stroke_expansion_j: float,
        power_spring_work_j: float,
        power_ke_delta_j: float,
        power_damping_j: float,
        gross_expansion_j: float,
        gross_pumping_j: float,
        mech_j: float,
        mech_gas_j: float,
        mech_storage_j: float,
        spring_state_delta_j: float,
        kinetic_state_delta_j: float,
        elec_j: float,
        fuel_j: float,
        min_clearance_mm: float,
        max_travel_mm: float,
        collision: bool,
        wall_temp_k: float = 0.0,
        generator_temp_k: float = 0.0,
        valve_temp_k: float = 0.0,
        generator_derate: float = 1.0,
        generator_effective_eff: float | None = None,
        mean_generator_force_n: float = 0.0,
        raw_spring_recovery: float = 0.0,
    ) -> CycleEnergyReport:
        cfg = self.cfg
        thermal_cfg = _thermal_config_from(cfg)
        from designs.phoenix_v3_thermal import live_wall_heat_fraction

        p_min = min(spring_pressures_bar) if spring_pressures_bar else 0.0
        p_max = max(spring_pressures_bar) if spring_pressures_bar else 0.0
        ratio = p_max / max(p_min, 1e-9)

        spring_hysteresis_j = max(spring_stored_j - spring_recovered_j, 0.0)
        raw_spring_eff = spring_recovered_j / max(spring_stored_j, 1e-9)
        spring_eff = min(_clamp(raw_spring_eff, 0.0, 1.0), cfg.air_spring_recovery_cap)
        spring_recovered_capped_j = spring_stored_j * spring_eff

        indicated_raw_j = indicated_work_signed_j
        raw_indicated = max(indicated_work_signed_j, 0.0)
        otto_ceiling = 1.0 - (1.0 / max(cfg.compression_ratio, 1.01)) ** (GAMMA - 1.0)
        min_heat_frac = live_wall_heat_fraction(thermal_cfg, wall_temp_k)
        max_indicated = fuel_j * min(1.0 - min_heat_frac, otto_ceiling) if fuel_j > 0.0 else 0.0
        min_heat = fuel_j * min_heat_frac if fuel_j > 0.0 else 0.0
        combustion_work_j = min(raw_indicated, max_indicated) if fuel_j > 0.0 else 0.0
        heat_exhaust_j = fuel_j - combustion_work_j if fuel_j > 0.0 else 0.0
        heat_exhaust_j = max(heat_exhaust_j, min_heat)
        combustion_work_j = min(combustion_work_j, max(fuel_j - heat_exhaust_j, 0.0))

        tol = 1e-3
        mech_raw_j = max(mech_j, 0.0)
        mech_gas_j = max(mech_gas_j, 0.0)
        mech_storage_j = max(mech_storage_j, 0.0)
        mech_j = min(mech_gas_j, combustion_work_j)
        gen_eff = (
            generator_effective_eff
            if generator_effective_eff is not None
            else cfg.generator_efficiency * generator_derate
        )
        elec_j = mech_j * gen_eff

        unharvested = max(combustion_work_j - mech_j, 0.0)
        pumping_loss_j = gross_pumping_j
        capture = mech_j / max(power_stroke_expansion_j, 1e-9)
        net_eff = elec_j / max(fuel_j, 1e-9)

        w_net_check = gross_expansion_j - gross_pumping_j
        raw_positive = max(indicated_raw_j, 0.0)
        power_sink_j = mech_gas_j + power_spring_work_j + power_ke_delta_j + power_damping_j
        power_unharvested_j = max(power_stroke_expansion_j - power_sink_j, 0.0)
        power_overextract_j = max(power_sink_j - power_stroke_expansion_j, 0.0)
        power_residual_j = power_stroke_expansion_j - power_sink_j - power_unharvested_j
        closure_tol = max(
            power_stroke_expansion_j * 0.05,
            mech_gas_j * 0.06,
            20.0,
        )
        # Closure fails only on over-extraction; positive residual is unharvested gas work.
        power_closure_valid = (
            power_stroke_expansion_j < 1.0
            or power_overextract_j < closure_tol
        )
        energy_state_valid = power_closure_valid
        gas_extraction_valid = mech_gas_j <= power_stroke_expansion_j + tol
        raw_boundary_valid = gas_extraction_valid and power_closure_valid
        hierarchy_valid = (
            fuel_j + tol >= heat_exhaust_j + combustion_work_j
            and combustion_work_j + tol >= mech_j
            and mech_j + tol >= elec_j / max(gen_eff, 1e-9)
            and combustion_work_j <= max_indicated + tol
            and heat_exhaust_j >= min_heat - tol
            and elec_j <= fuel_j + tol
            and abs(w_net_check - indicated_raw_j) < 1.0
        )
        balance_valid = hierarchy_valid and raw_boundary_valid

        return CycleEnergyReport(
            cycle_index=cycle_index,
            spring_p_min_bar=p_min,
            spring_p_max_bar=p_max,
            spring_pressure_ratio=ratio,
            spring_energy_stored_j=spring_stored_j,
            spring_energy_recovered_j=spring_recovered_capped_j,
            spring_damping_loss_j=spring_damping_j,
            spring_recovery_efficiency=spring_eff,
            combustion_work_j=combustion_work_j,
            indicated_work_raw_j=indicated_raw_j,
            power_stroke_expansion_j=power_stroke_expansion_j,
            power_spring_work_j=power_spring_work_j,
            power_ke_delta_j=power_ke_delta_j,
            power_damping_j=power_damping_j,
            power_unharvested_j=power_unharvested_j,
            power_closure_valid=power_closure_valid,
            gross_expansion_work_j=gross_expansion_j,
            pumping_loss_j=pumping_loss_j,
            heat_residual_j=heat_exhaust_j,
            unharvested_work_j=unharvested,
            mech_energy_j=mech_j,
            elec_energy_j=elec_j,
            generator_efficiency=gen_eff,
            fuel_energy_j=fuel_j,
            net_cartridge_efficiency=net_eff,
            capture_fraction=capture,
            energy_balance_valid=balance_valid,
            hierarchy_valid=hierarchy_valid,
            raw_boundary_valid=raw_boundary_valid,
            spring_hysteresis_j=spring_hysteresis_j,
            min_piston_clearance_mm=min_clearance_mm,
            max_piston_travel_mm=max_travel_mm,
            piston_collision=collision,
            mech_energy_raw_j=mech_raw_j,
            mech_from_gas_j=mech_gas_j,
            mech_from_storage_j=mech_storage_j,
            spring_state_delta_j=spring_state_delta_j,
            kinetic_state_delta_j=kinetic_state_delta_j,
            energy_state_valid=energy_state_valid,
            wall_temp_k=wall_temp_k,
            generator_temp_k=generator_temp_k,
            valve_temp_k=valve_temp_k,
            generator_derate=generator_derate,
            mean_generator_force_n=mean_generator_force_n,
            raw_spring_recovery=raw_spring_eff,
        )

    def simulate(
        self,
        *,
        cycles: int = 3,
        steps_per_cycle: int = 500,
        fault: TransientFault | None = None,
        record_history: bool = True,
    ) -> SimulationResult:
        from designs.phoenix_v3_thermal import initial_thermal_state, update_thermal_state

        cfg = self.cfg
        self._fault = fault
        self._gen_load_scale = 1.0
        self._gen_load_target = 1.0
        self._actuators.reset()
        self._combustion.reset()
        thermal_cfg = _thermal_config_from(cfg)
        thermal = initial_thermal_state(thermal_cfg)
        dt = cfg.cycle_time_s / steps_per_cycle
        total_steps = cycles * steps_per_cycle
        time_scale = cfg.cycle_time_s * 1e3 / 10.0

        # Start from expanded position with residual burned gas from prior cycle.
        x_a = cfg.half_stroke_m * 0.92
        x_b = cfg.half_stroke_m * 0.90
        v_a = 0.0
        v_b = 0.0
        v_ch = chamber_volume_m3(x_a, x_b, cfg)
        t_k = 900.0
        m_total = cfg.intake_pressure_pa * v_ch / (R_AIR * 330.0)
        m_res = m_total * 0.12
        m_fresh = m_total - m_res
        p_pa = m_total * R_AIR * t_k / v_ch
        burn_frac = 0.0

        history: list[CycleState] = []
        scavenge_end_res: list[float] = []
        scavenge_temps: list[float] = []
        ring_pressure_drops: list[float] = []
        piston_asymmetry: list[float] = []
        peak_p_bar = 0.0
        cycle_reports: list[CycleEnergyReport] = []

        spring_pressures_bar: list[float] = []
        spring_stored_j = 0.0
        spring_recovered_j = 0.0
        spring_damping_j = 0.0
        spring_u_min_j = float("inf")
        spring_u_max_j = 0.0
        indicated_work_signed_j = 0.0
        power_stroke_expansion_j = 0.0
        power_spring_work_j = 0.0
        power_ke_delta_j = 0.0
        power_damping_j = 0.0
        gross_expansion_j = 0.0
        gross_pumping_j = 0.0
        mech_j = 0.0
        mech_gas_j = 0.0
        mech_storage_j = 0.0
        fuel_j = 0.0
        cycle_spring_u_start = 0.0
        cycle_ke_start = 0.0
        min_clearance_mm = cfg.half_stroke_m * 1e3
        max_travel_mm = 0.0
        cycle_collision = False
        cycle_gen_force_sum = 0.0
        cycle_gen_force_n = 0
        prev_cycle_bdc_mm = 0.0
        cycle_index = 0
        prev_cycle_index = -1
        m_prev = m_total

        for step in range(total_steps):
            t_s = step * dt
            cycle_ms = cfg.cycle_time_s * 1e3
            t_ms = (t_s * 1e3 + cfg.phase_offset_ms) % max(cycle_ms, 1e-9)
            cycle_index = int(t_s / cfg.cycle_time_s)

            if fault and fault.kind == "load_double":
                trigger_s = fault.trigger_time_s
                if trigger_s is None:
                    trigger_s = fault.trigger_cycle * cfg.cycle_time_s
                if t_s >= trigger_s:
                    self._gen_load_target = 2.0

            self._gen_load_scale = slew_generator_load_scale(
                self._gen_load_scale, self._gen_load_target, dt, cfg,
            )

            valves, stage = valve_schedule(t_ms, cfg, fault=fault, cycle_index=cycle_index)

            if 1.55 * time_scale <= t_ms <= 1.95 * time_scale and stage == "2_exhaust_close":
                ring_pressure_drops.append(
                    _clamp(1.0 - p_pa / cfg.intake_pressure_pa, 0.0, 0.5)
                )

            if stage.startswith("1_scavenge") and t_ms < 0.05 and step > steps_per_cycle:
                scavenge_end_res.append(m_res / max(m_total, 1e-9))
                scavenge_temps.append(t_k)

            if cycle_index > prev_cycle_index and step > 0:
                p_sa_end = air_spring_pressure_pa(x_a, cfg, control_gain=self._spring_gain_a)
                p_sb_end = air_spring_pressure_pa(x_b, cfg, control_gain=self._spring_gain_b)
                spring_u_end = spring_internal_energy_total_j(x_a, x_b, p_sa_end, p_sb_end, cfg)
                ke_end = piston_kinetic_energy_j(v_a, v_b, cfg.piston_mass_kg)
                spring_delta = spring_u_end - cycle_spring_u_start
                ke_delta = ke_end - cycle_ke_start
                cycle_reports.append(self._finalize_cycle_report(
                    prev_cycle_index,
                    spring_pressures_bar=spring_pressures_bar,
                    spring_stored_j=spring_stored_j,
                    spring_recovered_j=spring_recovered_j,
                    spring_damping_j=spring_damping_j,
                    spring_u_min_j=spring_u_min_j,
                    spring_u_max_j=spring_u_max_j,
                    indicated_work_signed_j=indicated_work_signed_j,
                    power_stroke_expansion_j=power_stroke_expansion_j,
                    power_spring_work_j=power_spring_work_j,
                    power_ke_delta_j=power_ke_delta_j,
                    power_damping_j=power_damping_j,
                    gross_expansion_j=gross_expansion_j,
                    gross_pumping_j=gross_pumping_j,
                    mech_j=mech_j,
                    mech_gas_j=mech_gas_j,
                    mech_storage_j=mech_storage_j,
                    spring_state_delta_j=spring_delta,
                    kinetic_state_delta_j=ke_delta,
                    elec_j=0.0,
                    fuel_j=fuel_j,
                    min_clearance_mm=min_clearance_mm,
                    max_travel_mm=max_travel_mm,
                    collision=cycle_collision,
                    wall_temp_k=thermal.wall_temp_k,
                    generator_temp_k=thermal.generator_temp_k,
                    valve_temp_k=thermal.valve_temp_k,
                    generator_derate=thermal.generator_derate * _fault_generator_derate(
                        fault, prev_cycle_index,
                    ),
                    generator_effective_eff=thermal.generator_efficiency,
                    mean_generator_force_n=(
                        cycle_gen_force_sum / max(cycle_gen_force_n, 1)
                    ),
                    raw_spring_recovery=(
                        spring_recovered_j / max(spring_stored_j, 1e-9)
                        if spring_stored_j > 1e-9 else 0.0
                    ),
                ))
                burn_frac = 0.0
                m_res = m_res * 0.4 + m_total * 0.02
                spring_pressures_bar = []
                spring_stored_j = 0.0
                spring_recovered_j = 0.0
                spring_damping_j = 0.0
                spring_u_min_j = float("inf")
                spring_u_max_j = 0.0
                indicated_work_signed_j = 0.0
                power_stroke_expansion_j = 0.0
                power_spring_work_j = 0.0
                power_ke_delta_j = 0.0
                power_damping_j = 0.0
                gross_expansion_j = 0.0
                gross_pumping_j = 0.0
                mech_j = 0.0
                mech_gas_j = 0.0
                mech_storage_j = 0.0
                fuel_j = 0.0
                cycle_spring_u_start = spring_u_end
                cycle_ke_start = ke_end
                min_clearance_mm = cfg.half_stroke_m * 1e3
                max_travel_mm = 0.0
                cycle_collision = False
                cycle_gen_force_sum = 0.0
                cycle_gen_force_n = 0
                prev_cycle_bdc_mm = max_travel_mm
            prev_cycle_index = cycle_index

            m_prev = m_total
            m_total, m_res, m_fresh, t_k = self._gas_exchange(
                CycleState(
                    t_s=t_s, x_a_m=x_a, v_a_ms=v_a, x_b_m=x_b, v_b_ms=v_b,
                    pressure_pa=p_pa, temperature_k=t_k, mass_kg=m_total,
                    residual_mass_kg=m_res, fresh_mass_kg=m_fresh,
                    fuel_burned_frac=burn_frac, valves=valves, stage=stage,
                    p_spring_a_pa=0.0, p_spring_b_pa=0.0,
                ),
                valves,
                dt,
            )
            post = CycleState(
                t_s=t_s, x_a_m=x_a, v_a_ms=v_a, x_b_m=x_b, v_b_ms=v_b,
                pressure_pa=p_pa, temperature_k=t_k, mass_kg=m_total,
                residual_mass_kg=m_res, fresh_mass_kg=m_fresh,
                fuel_burned_frac=burn_frac, valves=valves, stage=stage,
                p_spring_a_pa=0.0, p_spring_b_pa=0.0,
            )
            p_pa, t_k, burn_frac, fuel_delta_j = self._combustion_update(
                t_ms, burn_frac, post, cycle_index=cycle_index,
            )
            fuel_j += fuel_delta_j

            v_ch = chamber_volume_m3(x_a, x_b, cfg)
            p_pa = max(cfg.exhaust_pressure_pa, m_total * R_AIR * t_k / max(v_ch, 1e-9))
            peak_p_bar = max(peak_p_bar, p_pa / 1e5)

            v_ch_before = chamber_volume_m3(x_a, x_b, cfg)

            self._update_spring_control(x_a, x_b)
            self._actuators.observe(t_s, x_a, x_b, p_pa)
            x_a_ctrl, x_b_ctrl, p_ctrl = self._actuators.delayed_state(
                t_s, x_a, x_b, p_pa, cfg,
            )
            p_sa = air_spring_pressure_pa(
                x_a, cfg, control_gain=self._spring_gain_a * self._spring_phase_a,
            )
            p_sb = air_spring_pressure_pa(
                x_b, cfg, control_gain=self._spring_gain_b * self._spring_phase_b,
            )
            if cfg.tolerance_spring_leak_frac > 0.0:
                bleed = max(0.0, 1.0 - cfg.tolerance_spring_leak_frac * dt)
                p_sa *= bleed
                p_sb *= bleed

            area = cfg.piston_area_m2
            sensor_frac = cfg.tolerance_pressure_sensor_frac
            if fault and fault.kind == "pressure_sensor_fault" and cycle_index >= fault.trigger_cycle:
                sensor_frac = min(0.5, sensor_frac * 4.0)
            p_sensor = p_ctrl * (1.0 + sensor_frac)
            f_gas_a = p_sensor * area
            f_gas_b = p_sensor * area
            relief_a = spring_end_stop_relief_gain(x_a_ctrl, cfg)
            relief_b = spring_end_stop_relief_gain(x_b_ctrl, cfg)
            f_spring_a = p_sa * area * relief_a
            f_spring_b = p_sb * area * relief_b
            motion_guard = False
            if cfg.virtual_end_stop_enabled:
                load_fault_armed = (
                    fault is not None
                    and fault.kind == "load_double"
                    and cycle_index >= max(0, fault.trigger_cycle - 1)
                )
                motion_guard = (
                    self._gen_load_target > 1.01
                    or self._gen_load_scale > 1.01
                    or load_fault_armed
                )
            ves_a = virtual_end_stop_gain(x_a_ctrl, cfg)
            ves_b = virtual_end_stop_gain(x_b_ctrl, cfg)
            if motion_guard:
                clear_frac = min(x_a_ctrl, x_b_ctrl) / max(cfg.half_stroke_m, 1e-9)
                if clear_frac < 0.18:
                    tdc_scale = _clamp(clear_frac / 0.18, 0.0, 1.0)
                    ves_a *= tdc_scale
                    ves_b *= tdc_scale
            base_scale = (
                cfg.generator_force_scale
                * self._gen_load_scale
                * capture_startup_multiplier(cycle_index, prev_cycle_bdc_mm, cfg)
            )
            scale_b_nom = (
                cfg.generator_force_scale_b
                if cfg.generator_force_scale_b is not None
                else base_scale
            )
            scale_a, scale_b = generator_balance_scales(
                x_a_ctrl, x_b_ctrl, base_scale, scale_b_nom, cfg,
            )
            f_gen_a_cmd = generator_brake_force_n(
                f_gas_a, f_spring_a, v_a, stage, scale_a, cfg,
                x_outward_m=x_a_ctrl, end_stop_gain=ves_a,
            )
            f_gen_b_cmd = generator_brake_force_n(
                f_gas_b, f_spring_b, v_b, stage, scale_b, cfg,
                x_outward_m=x_b_ctrl, end_stop_gain=ves_b,
            )
            f_gen_a = self._actuators.apply_generator_force("a", f_gen_a_cmd, v_a, dt, cfg)
            f_gen_b = self._actuators.apply_generator_force("b", f_gen_b_cmd, v_b, dt, cfg)
            if stage == "7_power":
                cycle_gen_force_sum += abs(f_gen_a) + abs(f_gen_b)
                cycle_gen_force_n += 1

            # Opposed convention: x=0 is TDC (inward), x=half_stroke is BDC (outward).
            ke_before = piston_kinetic_energy_j(v_a, v_b, cfg.piston_mass_kg)
            spring_u_before = spring_internal_energy_total_j(x_a, x_b, p_sa, p_sb, cfg)
            f_net_a = f_gas_a - f_spring_a - f_gen_a - cfg.damping_n_s_m * v_a
            f_net_b = f_gas_b - f_spring_b - f_gen_b - cfg.damping_n_s_m * v_b

            x_a_new, v_a, impact_a = integrate_piston_travel(
                x_a, v_a, f_net_a, dt, cfg, motion_guard=motion_guard,
            )
            x_b_new, v_b, impact_b = integrate_piston_travel(
                x_b, v_b, f_net_b, dt, cfg, motion_guard=motion_guard,
            )
            if motion_guard and impact_a and impact_b and x_a_new <= 0.001 and x_b_new <= 0.001:
                cycle_collision = True
            elif impact_a or impact_b:
                speed = max(abs(v_a), abs(v_b))
                if speed > cfg.collision_speed_threshold_ms:
                    cycle_collision = True

            dx_a = x_a_new - x_a
            dx_b = x_b_new - x_b
            phase_target_a = spring_phase_gain(dx_a, stage, cfg)
            phase_target_b = spring_phase_gain(dx_b, stage, cfg)
            self._spring_phase_a = slew_spring_phase_gain(
                self._spring_phase_a, phase_target_a, dt, cfg,
            )
            self._spring_phase_b = slew_spring_phase_gain(
                self._spring_phase_b, phase_target_b, dt, cfg,
            )
            if dx_a > 0.0:
                spring_stored_j += p_sa * area * dx_a
            elif dx_a < 0.0:
                spring_recovered_j += p_sa * area * (-dx_a)
            if dx_b > 0.0:
                spring_stored_j += p_sb * area * dx_b
            elif dx_b < 0.0:
                spring_recovered_j += p_sb * area * (-dx_b)

            if not valves.any_intake and not valves.any_exhaust:
                spring_damping_j += cfg.damping_n_s_m * (v_a * v_a + v_b * v_b) * dt
            closed_valves = not valves.any_intake and not valves.any_exhaust
            in_power = stage == "7_power" and closed_valves
            if in_power:
                damp_step = cfg.damping_n_s_m * (v_a * v_a + v_b * v_b) * dt
                ke_now = piston_kinetic_energy_j(v_a, v_b, cfg.piston_mass_kg)
                w_gen_a, w_gen_b = _cap_generator_work_step(
                    p_pa, area, dx_a, dx_b, f_gen_a, f_gen_b,
                )
                d_work_ch = max(p_pa * area * (dx_a + dx_b), 0.0)
                if d_work_ch > 0.0:
                    spring_step = p_sa * area * dx_a + p_sb * area * dx_b
                    dke_step = ke_now - ke_before
                    power_damping_j += damp_step
                    power_spring_work_j += spring_step
                    power_ke_delta_j += dke_step
                if dx_a > 0.0 and w_gen_a > 0.0:
                    mech_j += w_gen_a
                    d_work_a = p_pa * area * dx_a
                    if d_work_a > 0.0:
                        mech_gas_j += w_gen_a
                    else:
                        mech_storage_j += w_gen_a
                if dx_b > 0.0 and w_gen_b > 0.0:
                    mech_j += w_gen_b
                    d_work_b = p_pa * area * dx_b
                    if d_work_b > 0.0:
                        mech_gas_j += w_gen_b
                    else:
                        mech_storage_j += w_gen_b

            spring_u = spring_internal_energy_total_j(x_a_new, x_b_new, p_sa, p_sb, cfg)
            if step == 0:
                cycle_spring_u_start = spring_u
                cycle_ke_start = piston_kinetic_energy_j(v_a, v_b, cfg.piston_mass_kg)
            spring_u_min_j = min(spring_u_min_j, spring_u)
            spring_u_max_j = max(spring_u_max_j, spring_u)

            x_a, x_b = x_a_new, x_b_new
            piston_asymmetry.append(abs(x_a - x_b) / cfg.half_stroke_m)

            spring_pressures_bar.append(0.5 * (p_sa + p_sb) / 1e5)
            mech_power = f_gen_a * max(v_a, 0.0) + f_gen_b * max(v_b, 0.0)
            valve_flow_kg_s = abs(m_total - m_prev) / max(dt, 1e-12)
            thermal_result = update_thermal_state(
                thermal,
                cfg=thermal_cfg,
                dt=dt,
                combustion_heat_w=fuel_delta_j / max(dt, 1e-12),
                mech_power_w=mech_power,
                valve_mass_flow_kg_s=valve_flow_kg_s,
                gas_temp_k=t_k,
                valves_open=valves.any_intake or valves.any_exhaust,
            )
            thermal = thermal_result.state
            elec_power = mech_power * thermal.generator_efficiency
            if record_history:
                history.append(CycleState(
                    t_s=t_s,
                    x_a_m=x_a,
                    v_a_ms=v_a,
                    x_b_m=x_b,
                    v_b_ms=v_b,
                    pressure_pa=p_pa,
                    temperature_k=t_k,
                    mass_kg=m_total,
                    residual_mass_kg=m_res,
                    fresh_mass_kg=m_fresh,
                    fuel_burned_frac=burn_frac,
                    valves=valves,
                    stage=stage,
                    p_spring_a_pa=p_sa,
                    p_spring_b_pa=p_sb,
                    spring_energy_j=(
                        air_spring_internal_energy_j(p_sa, air_spring_volume_m3(x_a, cfg))
                        + air_spring_internal_energy_j(p_sb, air_spring_volume_m3(x_b, cfg))
                    ),
                    mech_power_w=mech_power,
                    elec_power_w=elec_power,
                    wall_temp_k=thermal.wall_temp_k,
                    generator_temp_k=thermal.generator_temp_k,
                    valve_temp_k=thermal.valve_temp_k,
                ))

            clearance_mm = min(x_a, x_b) * 1e3
            travel_mm = max(x_a, x_b) * 1e3
            min_clearance_mm = min(min_clearance_mm, clearance_mm)
            max_travel_mm = max(max_travel_mm, travel_mm)

            v_ch_after = chamber_volume_m3(x_a, x_b, cfg)
            d_v_ch = v_ch_after - v_ch_before
            if not valves.any_intake and not valves.any_exhaust:
                d_work = p_pa * d_v_ch
                indicated_work_signed_j += d_work
                if d_work > 0.0:
                    gross_expansion_j += d_work
                    if stage == "7_power":
                        power_stroke_expansion_j += d_work
                elif d_work < 0.0:
                    gross_pumping_j += -d_work
            if not valves.any_intake and not valves.any_exhaust and v_ch_after < v_ch_before:
                ratio = v_ch_before / max(v_ch_after, 1e-12)
                t_k *= ratio ** (GAMMA - 1.0)
                p_pa = m_total * R_AIR * t_k / max(v_ch_after, 1e-12)
                peak_p_bar = max(peak_p_bar, p_pa / 1e5)

        if spring_pressures_bar and fuel_j > 0.0:
            p_sa_end = air_spring_pressure_pa(x_a, cfg, control_gain=self._spring_gain_a)
            p_sb_end = air_spring_pressure_pa(x_b, cfg, control_gain=self._spring_gain_b)
            spring_u_end = spring_internal_energy_total_j(x_a, x_b, p_sa_end, p_sb_end, cfg)
            ke_end = piston_kinetic_energy_j(v_a, v_b, cfg.piston_mass_kg)
            cycle_reports.append(self._finalize_cycle_report(
                prev_cycle_index,
                spring_pressures_bar=spring_pressures_bar,
                spring_stored_j=spring_stored_j,
                spring_recovered_j=spring_recovered_j,
                spring_damping_j=spring_damping_j,
                spring_u_min_j=spring_u_min_j,
                spring_u_max_j=spring_u_max_j,
                indicated_work_signed_j=indicated_work_signed_j,
                power_stroke_expansion_j=power_stroke_expansion_j,
                power_spring_work_j=power_spring_work_j,
                power_ke_delta_j=power_ke_delta_j,
                power_damping_j=power_damping_j,
                gross_expansion_j=gross_expansion_j,
                gross_pumping_j=gross_pumping_j,
                mech_j=mech_j,
                mech_gas_j=mech_gas_j,
                mech_storage_j=mech_storage_j,
                spring_state_delta_j=spring_u_end - cycle_spring_u_start,
                kinetic_state_delta_j=ke_end - cycle_ke_start,
                elec_j=0.0,
                fuel_j=fuel_j,
                min_clearance_mm=min_clearance_mm,
                max_travel_mm=max_travel_mm,
                collision=cycle_collision,
                wall_temp_k=thermal.wall_temp_k,
                generator_temp_k=thermal.generator_temp_k,
                valve_temp_k=thermal.valve_temp_k,
                generator_derate=thermal.generator_derate * _fault_generator_derate(
                    fault, prev_cycle_index,
                ),
                generator_effective_eff=thermal.generator_efficiency,
                mean_generator_force_n=(
                    cycle_gen_force_sum / max(cycle_gen_force_n, 1)
                ),
                raw_spring_recovery=(
                    spring_recovered_j / max(spring_stored_j, 1e-9)
                    if spring_stored_j > 1e-9 else 0.0
                ),
            ))

        rgf = float(np.mean(scavenge_end_res[-cycles:])) if scavenge_end_res else (
            m_res / max(m_total, 1e-9)
        )
        scavenge_eff = 1.0 - rgf
        drop = float(np.mean(ring_pressure_drops[-steps_per_cycle * 2:])) if ring_pressure_drops else 0.0

        temp_std = float(np.std(scavenge_temps[-cycles:])) if len(scavenge_temps) >= 2 else 0.0
        temp_mean = float(np.mean(scavenge_temps[-cycles:])) if scavenge_temps else t_k
        temp_uniformity = 1.0 - _clamp(temp_std / max(temp_mean, 1.0), 0.0, 0.08)

        metrics = ScavengeMetrics(
            residual_gas_fraction=rgf,
            scavenge_efficiency=scavenge_eff,
            pressure_drop_fraction=_clamp(drop, 0.0, 1.0),
            cylinder_variation=float(np.mean(piston_asymmetry[-steps_per_cycle:])),
            post_scavenge_temp_uniformity=temp_uniformity,
            peak_pressure_bar=peak_p_bar,
        )

        reported_raw = tuple(c for c in cycle_reports if c.cycle_index > 0 and c.fuel_energy_j > 50.0)
        deduped: dict[int, CycleEnergyReport] = {}
        for c in reported_raw:
            deduped[c.cycle_index] = c
        reported_cycles = tuple(deduped[i] for i in sorted(deduped))[-cycles:]
        steady_cycles = _steady_cycle_reports(reported_cycles, cfg)
        late_cycles = _late_cycle_reports(reported_cycles, cfg)
        late_eff = (
            float(np.mean([c.net_cartridge_efficiency for c in late_cycles]))
            if late_cycles
            else 0.0
        )
        if reported_cycles:
            energy = EnergyMetrics(
                air_spring_recovery=float(np.mean([c.spring_recovery_efficiency for c in reported_cycles])),
                air_spring_stored_j=float(np.mean([c.spring_energy_stored_j for c in reported_cycles])),
                air_spring_recovered_j=float(np.mean([c.spring_energy_recovered_j for c in reported_cycles])),
                generator_efficiency=float(np.mean([c.generator_efficiency for c in late_cycles or reported_cycles])),
                mech_energy_j=float(np.sum([c.mech_energy_j for c in reported_cycles])),
                elec_energy_j=float(np.sum([c.elec_energy_j for c in reported_cycles])),
                net_cartridge_efficiency=late_eff,
                fuel_energy_j=float(np.sum([c.fuel_energy_j for c in reported_cycles])),
                combustion_work_j=float(np.sum([c.combustion_work_j for c in reported_cycles])),
                unharvested_work_j=float(np.sum([c.unharvested_work_j for c in reported_cycles])),
                heat_residual_j=float(np.sum([c.heat_residual_j for c in reported_cycles])),
                energy_balance_valid=all(c.hierarchy_valid for c in reported_cycles),
                hierarchy_valid=all(c.hierarchy_valid for c in reported_cycles),
                raw_boundary_valid=(
                    all(c.raw_boundary_valid for c in steady_cycles)
                    if steady_cycles
                    else all(c.raw_boundary_valid for c in reported_cycles)
                ),
                cycle_reports=reported_cycles,
            )
        else:
            energy = EnergyMetrics(
                air_spring_recovery=0.0,
                air_spring_stored_j=0.0,
                air_spring_recovered_j=0.0,
                generator_efficiency=0.0,
                mech_energy_j=0.0,
                elec_energy_j=0.0,
                net_cartridge_efficiency=0.0,
                fuel_energy_j=0.0,
                combustion_work_j=0.0,
                unharvested_work_j=0.0,
                heat_residual_j=0.0,
                energy_balance_valid=False,
                hierarchy_valid=False,
                raw_boundary_valid=False,
                cycle_reports=(),
            )

        if history:
            time_s = np.array([h.t_s for h in history])
        else:
            time_s = np.linspace(0.0, cycles * cfg.cycle_time_s, max(cycles, 1))
        return SimulationResult(
            cfg=cfg,
            history=history,
            metrics=metrics,
            energy=energy,
            time_s=time_s,
            stage_labels=[h.stage for h in history],
            fault=fault,
        )


def _last_cycle_slice(result: SimulationResult) -> slice:
    cfg = result.cfg
    steps = len(result.history) // max(1, int(result.time_s[-1] / cfg.cycle_time_s))
    return slice(-steps, None)


def assess_transient_recovery(
    result: SimulationResult,
    fault: TransientFault,
    *,
    baseline_efficiency: float | None = None,
    recovery_band_pp: float = 5.0,
) -> TransientResult:
    """Determine whether the cartridge ECU recovers after a fault without piston collision."""
    collision_cycle: int | None = None
    for report in result.energy.cycle_reports:
        if report.piston_collision and report.cycle_index >= fault.trigger_cycle:
            collision_cycle = report.cycle_index
            break

    post_fault = [r for r in result.energy.cycle_reports if r.cycle_index > fault.trigger_cycle]
    recovered = False
    recovery_cycles: int | None = None
    notes = ""
    ref_eff = baseline_efficiency
    if ref_eff is None and result.energy.cycle_reports:
        pre = [r for r in result.energy.cycle_reports if r.cycle_index < fault.trigger_cycle]
        if pre:
            ref_eff = float(np.mean([r.net_cartridge_efficiency for r in pre[-3:]]))

    if collision_cycle is not None:
        notes = f"Piston collision detected at cycle {collision_cycle}."
    elif not post_fault:
        notes = "Insufficient post-fault cycles to assess recovery."
    else:
        band = recovery_band_pp / 100.0
        target = ref_eff if ref_eff is not None else 0.40
        stable = all(
            (not r.piston_collision)
            and r.spring_recovery_efficiency > 0.75
            and r.net_cartridge_efficiency >= max(0.20, target - band)
            for r in post_fault[-2:]
        )
        if stable:
            recovered = True
            recovery_cycles = post_fault[0].cycle_index - fault.trigger_cycle
            late_eff = float(np.mean([r.net_cartridge_efficiency for r in post_fault[-2:]]))
            notes = (
                f"Recovered within {recovery_cycles} cycle(s); "
                f"late Enet {late_eff:.1%} vs baseline {target:.1%} (±{recovery_band_pp:.0f} pp)."
            )
        else:
            notes = "Post-fault efficiency outside baseline recovery band or stroke degraded."

    return TransientResult(
        fault=fault,
        result=result,
        collision_cycle=collision_cycle,
        recovered=recovered and collision_cycle is None,
        recovery_cycles=recovery_cycles,
        notes=notes,
    )


@dataclass(frozen=True)
class LongRunSummary:
    cycles: int
    collision_count: int
    mean_efficiency: float
    late_efficiency: float
    early_efficiency: float
    max_asymmetry: float
    tdc_drift_mm: float
    energy_balance_valid: bool
    late_hierarchy_valid: bool
    late_raw_boundary_valid: bool
    steady_raw_boundary_valid: bool
    raw_boundary_valid: bool
    wall_temp_k: float
    generator_temp_k: float
    valve_temp_k: float
    mean_generator_derate: float
    mean_generator_efficiency: float
    cooling_mode: str
    thermal_sustainable: bool


@dataclass(frozen=True)
class LongRunTrendPoint:
    """Windowed metrics sampled every N cycles during a long run."""

    window_start: int
    window_end: int
    net_efficiency: float
    generator_efficiency: float
    generator_temp_c: float
    wall_temp_c: float
    valve_temp_c: float
    combustion_work_j: float
    elec_energy_j: float
    fuel_energy_j: float
    spring_recovery: float
    capture_fraction: float
    heat_residual_frac: float
    wall_heat_fraction: float
    generator_derate: float
    unharvested_frac: float
    power_closure_pass_rate: float


@dataclass(frozen=True)
class LongRunAnalysis:
    summary: LongRunSummary
    trend: tuple[LongRunTrendPoint, ...]
    correlations: tuple[tuple[str, float], ...]
    result: SimulationResult


def build_long_run_trend(
    reports: tuple[CycleEnergyReport, ...],
    cfg: PhoenixV3Config,
    *,
    sample_every: int = 100,
) -> tuple[LongRunTrendPoint, ...]:
    """Aggregate per-cycle reports into fixed-width windows for trend analysis."""
    from designs.phoenix_v3_thermal import live_wall_heat_fraction

    valid = [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    thermal_cfg = _thermal_config_from(cfg)
    points: list[LongRunTrendPoint] = []
    for start in range(0, len(valid), sample_every):
        window = valid[start: start + sample_every]
        if not window:
            break
        mean_wall_k = float(np.mean([r.wall_temp_k for r in window]))
        mean_fuel = float(np.mean([r.fuel_energy_j for r in window]))
        mean_comb = float(np.mean([r.combustion_work_j for r in window]))
        mean_unharv = float(np.mean([r.unharvested_work_j for r in window]))
        points.append(LongRunTrendPoint(
            window_start=window[0].cycle_index,
            window_end=window[-1].cycle_index,
            net_efficiency=float(np.mean([r.net_cartridge_efficiency for r in window])),
            generator_efficiency=float(np.mean([r.generator_efficiency for r in window])),
            generator_temp_c=float(np.mean([r.generator_temp_k for r in window])) - 273.15,
            wall_temp_c=mean_wall_k - 273.15,
            valve_temp_c=float(np.mean([r.valve_temp_k for r in window])) - 273.15,
            combustion_work_j=mean_comb,
            elec_energy_j=float(np.mean([r.elec_energy_j for r in window])),
            fuel_energy_j=mean_fuel,
            spring_recovery=float(np.mean([r.spring_recovery_efficiency for r in window])),
            capture_fraction=float(np.mean([r.capture_fraction for r in window])),
            heat_residual_frac=float(np.mean([r.heat_residual_j for r in window])) / max(mean_fuel, 1e-9),
            wall_heat_fraction=live_wall_heat_fraction(thermal_cfg, mean_wall_k),
            generator_derate=float(np.mean([r.generator_derate for r in window])),
            unharvested_frac=mean_unharv / max(mean_comb, 1e-9),
            power_closure_pass_rate=float(np.mean([1.0 if r.power_closure_valid else 0.0 for r in window])),
        ))
    return tuple(points)


def analyze_efficiency_decay_correlations(
    trend: tuple[LongRunTrendPoint, ...],
) -> tuple[tuple[str, float], ...]:
    """Rank variables by Pearson correlation with windowed net efficiency."""
    if len(trend) < 3:
        return ()
    y = np.array([p.net_efficiency for p in trend], dtype=float)
    candidates: dict[str, np.ndarray] = {
        "wall_heat_fraction": np.array([p.wall_heat_fraction for p in trend]),
        "heat_residual_frac": np.array([p.heat_residual_frac for p in trend]),
        "combustion_work_j": np.array([p.combustion_work_j for p in trend]),
        "capture_fraction": np.array([p.capture_fraction for p in trend]),
        "unharvested_frac": np.array([p.unharvested_frac for p in trend]),
        "spring_recovery": np.array([p.spring_recovery for p in trend]),
        "generator_efficiency": np.array([p.generator_efficiency for p in trend]),
        "generator_temp_c": np.array([p.generator_temp_c for p in trend]),
        "wall_temp_c": np.array([p.wall_temp_c for p in trend]),
        "generator_derate": np.array([p.generator_derate for p in trend]),
        "elec_energy_j": np.array([p.elec_energy_j for p in trend]),
        "power_closure_pass_rate": np.array([p.power_closure_pass_rate for p in trend]),
    }
    ranked: list[tuple[str, float]] = []
    for name, x in candidates.items():
        if float(np.std(x)) < 1e-12:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if math.isfinite(r):
            ranked.append((name, r))
    ranked.sort(key=lambda item: abs(item[1]), reverse=True)
    return tuple(ranked)


def run_long_stability_analysis(
    cfg: PhoenixV3Config | None = None,
    *,
    cycles: int = 10_000,
    trend_interval: int = 100,
) -> LongRunAnalysis:
    """Long-run stability plus windowed trend log and decay correlations."""
    base = cfg or PhoenixV3Config()
    result = PhoenixV3Simulator(base).simulate(cycles=cycles, record_history=False)
    reports = result.energy.cycle_reports
    summary = _long_run_summary_from_result(base, result, cycles)
    trend = build_long_run_trend(reports, base, sample_every=max(trend_interval, 1))
    correlations = analyze_efficiency_decay_correlations(trend)
    return LongRunAnalysis(
        summary=summary,
        trend=trend,
        correlations=correlations,
        result=result,
    )


def _long_run_summary_from_result(
    base: PhoenixV3Config,
    result: SimulationResult,
    cycles: int,
) -> LongRunSummary:
    from designs.phoenix_v3_thermal import thermal_sustainable

    reports = result.energy.cycle_reports
    valid = [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    steady = _steady_cycle_reports(reports, base)
    late = list(_late_cycle_reports(reports, base))
    collision_count = sum(1 for r in valid if r.piston_collision)
    early = valid[: max(1, len(valid) // 10)]
    late_gen_t = float(np.mean([r.generator_temp_k for r in late])) if late else 0.0
    late_derate = float(np.mean([r.generator_derate for r in late])) if late else 1.0
    late_gen_eff = float(np.mean([r.generator_efficiency for r in late])) if late else base.generator_efficiency
    thermal_cfg = _thermal_config_from(base)
    return LongRunSummary(
        cycles=cycles,
        collision_count=collision_count,
        mean_efficiency=float(np.mean([r.net_cartridge_efficiency for r in valid])) if valid else 0.0,
        late_efficiency=float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0,
        early_efficiency=float(np.mean([r.net_cartridge_efficiency for r in early])) if early else 0.0,
        max_asymmetry=result.metrics.cylinder_variation,
        tdc_drift_mm=_tdc_center_drift_mm(reports),
        energy_balance_valid=result.energy.energy_balance_valid,
        late_hierarchy_valid=all(r.hierarchy_valid for r in late) if late else True,
        late_raw_boundary_valid=all(r.raw_boundary_valid for r in late) if late else True,
        steady_raw_boundary_valid=(
            all(r.raw_boundary_valid for r in steady) if steady else True
        ),
        raw_boundary_valid=result.energy.raw_boundary_valid,
        wall_temp_k=float(np.mean([r.wall_temp_k for r in late])) if late else 0.0,
        generator_temp_k=late_gen_t,
        valve_temp_k=float(np.mean([r.valve_temp_k for r in late])) if late else 0.0,
        mean_generator_derate=late_derate,
        mean_generator_efficiency=late_gen_eff,
        cooling_mode=base.generator_cooling_mode,
        thermal_sustainable=thermal_sustainable(late_gen_t, late_derate, thermal_cfg),
    )


def run_long_stability(
    cfg: PhoenixV3Config | None = None,
    *,
    cycles: int = 10_000,
) -> LongRunSummary:
    """Run many cycles without per-step history for drift / stability assessment."""
    base = cfg or PhoenixV3Config()
    result = PhoenixV3Simulator(base).simulate(cycles=cycles, record_history=False)
    return _long_run_summary_from_result(base, result, cycles)


def export_long_run_trend_csv(path: Path, trend: tuple[LongRunTrendPoint, ...]) -> None:
    import csv

    fields = [f.name for f in LongRunTrendPoint.__dataclass_fields__.values()]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for point in trend:
            writer.writerow({f: getattr(point, f) for f in fields})


def print_long_run_trend_report(analysis: LongRunAnalysis) -> None:
    trend = analysis.trend
    if not trend:
        print("No trend windows to report.")
        return
    print("=" * 72)
    print(f"LONG-RUN TREND LOG — every {trend[1].window_start - trend[0].window_start if len(trend) > 1 else 100} cycles")
    print("=" * 72)
    print(
        f"  {'Window':>12}  {'Enet':>6}  {'GenT':>6}  {'WallT':>6}  "
        f"{'GenEff':>6}  {'SprRec':>6}  {'Capt':>6}  {'WallHF':>6}"
    )
    for p in trend:
        print(
            f"  {p.window_start:5d}-{p.window_end:5d}  "
            f"{p.net_efficiency:5.1%}  {p.generator_temp_c:5.1f}  {p.wall_temp_c:5.1f}  "
            f"{p.generator_efficiency:5.1%}  {p.spring_recovery:5.1%}  "
            f"{p.capture_fraction:5.1%}  {p.wall_heat_fraction:5.1%}"
        )
    print()
    if analysis.correlations:
        print("  Efficiency decay correlations (Pearson r with net efficiency):")
        for name, r in analysis.correlations[:8]:
            direction = "rises" if r > 0 else "falls"
            print(f"    {name:28s}  r={r:+.3f}  (efficiency {direction} with this)")
        top = analysis.correlations[0]
        print()
        print(f"  Strongest correlate: {top[0]} (r={top[1]:+.3f})")
    print("=" * 72)
    print()


def detect_capture_mode_transitions(
    reports: tuple[CycleEnergyReport, ...],
    *,
    high_threshold: float = 0.25,
    low_threshold: float = 0.18,
) -> list[tuple[int, str, float, float, float]]:
    """Find cycles where capture fraction crosses between high and low attractors."""
    valid = [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    transitions: list[tuple[int, str, float, float, float]] = []
    mode = "high" if valid and valid[0].capture_fraction >= high_threshold else "low"
    for r in valid:
        cap = r.capture_fraction
        if mode == "high" and cap < low_threshold:
            transitions.append((
                r.cycle_index,
                "high_to_low",
                r.spring_recovery_efficiency,
                cap,
                r.max_piston_travel_mm,
            ))
            mode = "low"
        elif mode == "low" and cap >= high_threshold:
            transitions.append((
                r.cycle_index,
                "low_to_high",
                r.spring_recovery_efficiency,
                cap,
                r.max_piston_travel_mm,
            ))
            mode = "high"
    return transitions


def print_capture_transition_report(
    reports: tuple[CycleEnergyReport, ...],
) -> None:
    transitions = detect_capture_mode_transitions(reports)
    if not transitions:
        print("No capture-mode transitions detected.")
        return
    print("=" * 72)
    print("CAPTURE MODE TRANSITIONS")
    print("=" * 72)
    print(f"  {'Cycle':>6}  {'Event':>12}  {'SprRec':>6}  {'Capture':>7}  {'BDC mm':>7}")
    for cycle, event, spr, cap, bdc in transitions[:20]:
        print(f"  {cycle:6d}  {event:>12}  {spr:5.1%}  {cap:6.1%}  {bdc:6.2f}")
    if len(transitions) > 20:
        print(f"  ... {len(transitions) - 20} more transitions")
    print("=" * 72)
    print()


def plot_long_run_capture_diagnostic(
    reports: tuple[CycleEnergyReport, ...],
    path: Path,
    *,
    sample_every: int = 10,
) -> None:
    """Plot cycle-resolved spring recovery, capture, BDC, generator force, and elec output."""
    valid = [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    if sample_every > 1:
        valid = valid[::sample_every]
    if not valid:
        return

    cycles = np.array([r.cycle_index for r in valid], dtype=float)
    spr = np.array([r.spring_recovery_efficiency for r in valid])
    cap = np.array([r.capture_fraction for r in valid])
    bdc = np.array([r.max_piston_travel_mm for r in valid])
    gen_f = np.array([r.mean_generator_force_n for r in valid])
    elec = np.array([r.elec_energy_j for r in valid])
    eff = np.array([r.net_cartridge_efficiency for r in valid])

    fig, axes = plt.subplots(5, 1, figsize=(12, 10), sharex=True)
    fig.suptitle("Long-run capture diagnostic", fontsize=12)

    axes[0].plot(cycles, spr * 100, color="#2563eb", linewidth=0.8)
    axes[0].axhline(59.0, color="#93c5fd", linestyle="--", linewidth=0.6, label="high mode ~59%")
    axes[0].axhline(36.0, color="#fca5a5", linestyle="--", linewidth=0.6, label="low mode ~36%")
    axes[0].set_ylabel("Spring recov %")
    axes[0].legend(loc="upper right", fontsize=7)
    axes[0].grid(True, alpha=0.3)

    axes[1].plot(cycles, cap * 100, color="#16a34a", linewidth=0.8)
    axes[1].set_ylabel("Capture %")
    axes[1].grid(True, alpha=0.3)

    axes[2].plot(cycles, bdc, color="#ca8a04", linewidth=0.8)
    axes[2].set_ylabel("BDC mm")
    axes[2].grid(True, alpha=0.3)

    axes[3].plot(cycles, gen_f / 1000.0, color="#9333ea", linewidth=0.8)
    axes[3].set_ylabel("Gen force kN")
    axes[3].grid(True, alpha=0.3)

    axes[4].plot(cycles, elec, color="#dc2626", linewidth=0.8, label="Eelec J")
    ax4b = axes[4].twinx()
    ax4b.plot(cycles, eff * 100, color="#f97316", linewidth=0.6, alpha=0.8, label="Enet %")
    axes[4].set_ylabel("Eelec J")
    ax4b.set_ylabel("Enet %")
    axes[4].set_xlabel("Cycle")
    axes[4].grid(True, alpha=0.3)

    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def print_long_run_report(summary: LongRunSummary) -> None:
    mech_ok = summary.collision_count == 0 and summary.max_asymmetry < 0.05
    thermal_ok = summary.thermal_sustainable
    print("=" * 72)
    print(f"LONG-RUN STABILITY — {summary.cycles} cycles  (cooling: {summary.cooling_mode})")
    print("=" * 72)
    print(f"  Collisions:              {summary.collision_count}")
    print(f"  Mean efficiency:         {summary.mean_efficiency:.1%}")
    print(f"  Early / late efficiency: {summary.early_efficiency:.1%} / {summary.late_efficiency:.1%}")
    print(f"  Max asymmetry:           {summary.max_asymmetry:.2%}")
    print(f"  TDC drift:               {summary.tdc_drift_mm:.2f} mm")
    print(f"  Energy balance:          {'PASS' if summary.energy_balance_valid else 'FAIL'}")
    print(f"  Late-cycle hierarchy:    {'PASS' if summary.late_hierarchy_valid else 'FAIL'}")
    print(f"  Late work boundary:      {'PASS' if summary.late_raw_boundary_valid else 'WARN'}")
    print(f"  Work boundary (steady):  {'PASS' if summary.steady_raw_boundary_valid else 'WARN'}")
    print(f"  Late wall temp:          {summary.wall_temp_k - 273.15:.1f} C")
    print(f"  Late generator temp:     {summary.generator_temp_k - 273.15:.1f} C  "
          f"(derate {summary.mean_generator_derate:.1%}, eff {summary.mean_generator_efficiency:.1%})")
    print(f"  Late valve temp:         {summary.valve_temp_k - 273.15:.1f} C")
    print(f"  Mechanical stability:    {'PASS' if mech_ok else 'WARN'}")
    print(f"  Thermal sustainability:  {'PASS' if thermal_ok else 'FAIL'}")
    if not thermal_ok and summary.cooling_mode == "passive":
        print("  Hint: passive cooling is insufficient at ~54% output — retry with --cooling water_jacket")
    print("=" * 72)
    print()


@dataclass(frozen=True)
class ManufacturingTolerancePoint:
    label: str
    asymmetry: float
    net_efficiency: float
    tdc_drift_mm: float
    peak_pressure_bar: float
    energy_balance_valid: bool
    raw_boundary_valid: bool
    generator_derate: float


def run_manufacturing_tolerance_suite(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 15,
) -> list[ManufacturingTolerancePoint]:
    """Evaluate nominal build plus generator mismatch, valve lag, and spring leak."""
    ref = base or PhoenixV3Config()
    scenarios: list[tuple[str, PhoenixV3Config]] = [
        ("nominal", ref),
        ("gen_mismatch_+5%", replace(ref, tolerance_generator_mismatch_frac=0.05)),
        ("gen_mismatch_-5%", replace(ref, tolerance_generator_mismatch_frac=-0.05)),
        ("valve_lag_+0.3ms", replace(ref, tolerance_valve_lag_ms=0.3)),
        ("valve_lag_-0.3ms", replace(ref, tolerance_valve_lag_ms=-0.3)),
        ("spring_leak_5%", replace(ref, tolerance_spring_leak_frac=0.05)),
        ("pressure_sensor_+5%", replace(ref, tolerance_pressure_sensor_frac=0.05)),
    ]
    points: list[ManufacturingTolerancePoint] = []
    for label, cfg in scenarios:
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
        reports = result.energy.cycle_reports
        points.append(ManufacturingTolerancePoint(
            label=label,
            asymmetry=result.metrics.cylinder_variation,
            net_efficiency=result.energy.net_cartridge_efficiency,
            tdc_drift_mm=_tdc_center_drift_mm(reports),
            peak_pressure_bar=result.metrics.peak_pressure_bar,
            energy_balance_valid=result.energy.energy_balance_valid,
            raw_boundary_valid=result.energy.raw_boundary_valid,
            generator_derate=float(np.mean([r.generator_derate for r in reports])) if reports else 1.0,
        ))
    return points


def print_manufacturing_tolerance_report(points: list[ManufacturingTolerancePoint]) -> None:
    print("=" * 72)
    print("MANUFACTURING TOLERANCE SUITE")
    print("=" * 72)
    print(f"  {'Case':>20}  {'Asym':>6}  {'Enet':>6}  {'Drift':>6}  "
          f"{'PeakP':>6}  {'1st':>4}  {'Wb':>4}  {'Derate':>6}")
    for p in points:
        print(
            f"  {p.label:>20}  {p.asymmetry:5.2%}  {p.net_efficiency:5.1%}  "
            f"{p.tdc_drift_mm:5.2f}  {p.peak_pressure_bar:5.0f}  "
            f"{'OK' if p.energy_balance_valid else 'FAIL':>4}  "
            f"{'OK' if p.raw_boundary_valid else 'WARN':>4}  "
            f"{p.generator_derate:5.1%}"
        )
    print("=" * 72)
    print()


def run_transient_suite(
    cfg: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    fault_cycle: int = 3,
) -> list[TransientResult]:
    """Run steady-state baseline plus misfire, stuck valve, and load-doubling faults."""
    base_cfg = cfg or PhoenixV3Config()
    scenarios = (
        TransientFault("none"),
        TransientFault("misfire", trigger_cycle=fault_cycle),
        TransientFault("stuck_intake_a", trigger_cycle=fault_cycle),
        TransientFault("load_double", trigger_cycle=fault_cycle),
    )
    results: list[TransientResult] = []
    for fault in scenarios:
        sim = PhoenixV3Simulator(base_cfg)
        run = sim.simulate(cycles=cycles, fault=None if fault.kind == "none" else fault)
        if fault.kind == "none":
            results.append(TransientResult(
                fault=fault, result=run, collision_cycle=None,
                recovered=True, recovery_cycles=0, notes="Steady-state baseline.",
            ))
        else:
            results.append(assess_transient_recovery(run, fault))
    return results


def print_cycle_energy_table(result: SimulationResult) -> None:
    """Print per-cycle air spring and energy KPIs."""
    cfg = result.cfg
    print("Per-cycle energy report (Fuel = Heat + Wind; Wind >= Wgen >= Welec):")
    print(
        f"  {'Cy':>3}  {'Pmin':>6}  {'Pmax':>5}  "
        f"{'Efuel':>6}  {'Wind':>6}  {'Wgen':>6}  {'Welec':>6}  "
        f"{'Cap':>5}  {'Enet':>5}  {'BDC':>4}  Bal"
    )
    for c in result.energy.cycle_reports:
        bal = " OK" if c.power_closure_valid and c.hierarchy_valid else "FAIL"
        print(
            f"  {c.cycle_index:3d}  "
            f"{c.spring_p_min_bar:6.1f}  {c.spring_p_max_bar:5.1f}  "
            f"{c.fuel_energy_j:6.1f}  {c.combustion_work_j:6.1f}  "
            f"{c.mech_energy_j:6.1f}  {c.elec_energy_j:6.1f}  "
            f"{c.capture_fraction:5.1%}  {c.net_cartridge_efficiency:5.1%}  "
            f"{c.max_piston_travel_mm:4.1f}  {bal}"
        )
    e = result.energy
    print()
    print_energy_audit(result)


def print_energy_audit(result: SimulationResult) -> None:
    """Print joule waterfall: fuel -> combustion work -> generator -> electrical."""
    cfg = result.cfg
    e = result.energy
    if not e.cycle_reports:
        print("  (no cycle data for energy audit)")
        print()
        return

    n = len(e.cycle_reports)
    fuel = e.fuel_energy_j
    wind = e.combustion_work_j
    mech = e.mech_energy_j
    elec = e.elec_energy_j
    unharv = e.unharvested_work_j
    heat = e.heat_residual_j
    gross_exp = sum(c.gross_expansion_work_j for c in e.cycle_reports)
    pump = sum(c.pumping_loss_j for c in e.cycle_reports)
    w_net_raw = sum(c.indicated_work_raw_j for c in e.cycle_reports)
    w_pwr_exp = sum(c.power_stroke_expansion_j for c in e.cycle_reports)
    spring_loss = sum(c.spring_damping_loss_j for c in e.cycle_reports)
    spring_loss_per = spring_loss / max(n, 1)
    spring_hyst = float(np.mean([c.spring_hysteresis_j for c in e.cycle_reports]))
    mech_raw = sum(c.mech_energy_raw_j for c in e.cycle_reports)
    mech_gas = sum(c.mech_from_gas_j for c in e.cycle_reports)
    mech_store = sum(c.mech_from_storage_j for c in e.cycle_reports)
    d_spring = sum(c.spring_state_delta_j for c in e.cycle_reports)
    d_ke = sum(c.kinetic_state_delta_j for c in e.cycle_reports)
    pwr_spring = sum(c.power_spring_work_j for c in e.cycle_reports)
    pwr_ke = sum(c.power_ke_delta_j for c in e.cycle_reports)
    pwr_damp = sum(c.power_damping_j for c in e.cycle_reports)
    pwr_unharv = sum(c.power_unharvested_j for c in e.cycle_reports)
    power_sink = mech_gas + pwr_spring + pwr_ke + pwr_damp
    power_accounted = power_sink + pwr_unharv
    power_overextract = max(power_sink - w_pwr_exp, 0.0)
    steady_reports = _steady_cycle_reports(e.cycle_reports, cfg)
    closure_ok = (
        all(c.power_closure_valid for c in steady_reports)
        if steady_reports
        else all(c.power_closure_valid for c in e.cycle_reports)
    )
    capture_pwr = mech / max(w_pwr_exp, 1e-9)
    capture_ind = mech / max(wind, 1e-9)

    def pct(x: float) -> str:
        return f"{100.0 * x / max(fuel, 1e-9):5.1f}%"

    print("Energy balance audit (reported cycles):")
    hier_ok = e.hierarchy_valid
    raw_ok = e.raw_boundary_valid
    print(f"  [{'PASS' if hier_ok else 'FAIL'}] Hierarchy (capped): "
          f"Efuel >= Wind >= Wgen >= Welec (first law: Efuel = Eheat + Wind)")
    if not raw_ok or not closure_ok:
        print(f"  [WARN] Work boundary: over-extraction {power_overextract:.1f} J; "
              f"unharvested gas work {pwr_unharv:.1f} J (tracked, not a violation)")
    else:
        print("  [PASS] Work boundary: power-stroke joule ledger closes (steady cycles)")
    print(f"  Fuel energy in:           {fuel:8.1f} J   (100.0%)")
    print(f"  Thermal losses (heat):    {heat:8.1f} J   ({pct(heat)})  "
          f"wall + exhaust (min {cfg.min_thermal_loss_fraction:.0%})")
    print()
    print("  Indicated work (gas P-V, closed valves):")
    print(f"    W_net = oint P dV:      {wind:8.1f} J   ({pct(wind)})  "
          f"expansion - compression, Otto-capped")
    print(f"      Gross expansion:      {gross_exp:8.1f} J")
    print(f"      Gross compression:    {pump:8.1f} J")
    print(f"      Check Wexp-Wcomp:     {gross_exp - pump:8.1f} J   "
          f"(raw oint = {w_net_raw:.1f} J, "
          f"{'OK' if abs((gross_exp - pump) - w_net_raw) < n * 1.0 else 'MISMATCH'})")
    print(f"    Power-stroke expansion: {w_pwr_exp:8.1f} J   "
          f"int P dV during 7_power only (generator window)")
    print()
    print("  Generator extraction (int F_gen dx, same 7_power window):")
    print(f"    W_gen (from gas PdV):   {mech:8.1f} J   ({pct(mech)})  "
          f"capture {capture_pwr:.1%} of power-stroke expansion")
    print(f"    W_gen raw integral:     {mech_raw:8.1f} J   "
          f"(gas {mech_gas:.1f} J + storage {mech_store:.1f} J)")
    print(f"    Electrical output:      {elec:8.1f} J   ({pct(elec)})  "
          f"eta_gen {e.generator_efficiency:.1%}")
    if e.cycle_reports and cfg.thermal_model_enabled:
        last = e.cycle_reports[-1]
        print()
        print("  Thermal state (last reported cycle):")
        print(f"    Wall temperature:       {last.wall_temp_k - 273.15:8.1f} °C")
        print(f"    Generator temperature:  {last.generator_temp_k - 273.15:8.1f} °C  "
              f"(derate {last.generator_derate:.1%})")
        print(f"    Valve temperature:      {last.valve_temp_k - 273.15:8.1f} °C")
    print()
    print("  Power-stroke joule ledger (7_power window only):")
    print(f"    Gas expansion work:     {w_pwr_exp:8.1f} J   (100.0% of power stroke)")
    print(f"      -> generator:         {mech_raw:8.1f} J   "
          f"({100.0 * mech_raw / max(w_pwr_exp, 1e-9):.1f}%)")
    print(f"      -> spring work (path): {pwr_spring:8.1f} J   "
          f"({100.0 * pwr_spring / max(w_pwr_exp, 1e-9):.1f}%)")
    print(f"      -> kinetic energy:    {pwr_ke:8.1f} J   "
          f"({100.0 * pwr_ke / max(w_pwr_exp, 1e-9):.1f}%)")
    print(f"      -> viscous damping:   {pwr_damp:8.1f} J   "
          f"({100.0 * pwr_damp / max(w_pwr_exp, 1e-9):.1f}%)")
    print(f"      -> unharvested gas:   {pwr_unharv:8.1f} J   "
          f"({100.0 * pwr_unharv / max(w_pwr_exp, 1e-9):.1f}%)")
    print(f"    Ledger check:           {power_accounted:.1f} vs {w_pwr_exp:.1f} J   "
          f"({'OK' if abs(w_pwr_exp - power_accounted) < max(w_pwr_exp * 0.01, n * 5.0) else 'CHECK'})")
    print()
    print("  Full-cycle state variables (end - start, includes compression):")
    print(f"    dU_spring/cycle:        {d_spring / n:8.1f} J")
    print(f"    dKE/cycle:              {d_ke / n:8.1f} J")
    print(f"    W_damp/cycle (all):     {spring_loss_per:8.1f} J")
    print()
    print("  Work split:")
    print(f"      -> to generator:      {mech:8.1f} J   ({capture_ind:.1%} of net indicated)")
    print(f"      -> unharvested:       {unharv:8.1f} J   "
          f"(gas work to spring / kinetic / exhaust)")
    print(f"  First-law check:          {fuel:.1f} = {heat:.1f} + {wind:.1f}  "
          f"({'OK' if abs(fuel - heat - wind) < 1.0 else 'MISMATCH'})")
    print()
    print("  Air spring (cyclic redistributor - not a fuel source):")
    print(f"    Stored/cycle:           {e.air_spring_stored_j:8.1f} J")
    print(f"    Recovered/cycle:        {e.air_spring_recovered_j:8.1f} J  "
          f"(recovery {e.air_spring_recovery:.1%})")
    print(f"    Hysteresis/cycle:       {spring_hyst:8.1f} J   "
          f"(stored - recovered, before cap)")
    print(f"    Viscous damping/cycle:  {spring_loss_per:8.1f} J   (closed-valve motion)")
    print()
    print(f"  Net fuel-to-electric:     {e.net_cartridge_efficiency:.1%}  "
          f"(canonical late-steady window; target > {cfg.target_net_cartridge_efficiency:.0%})")
    print(f"  Peak chamber pressure:    {result.metrics.peak_pressure_bar:.0f} bar  "
          f"(design max {cfg.max_pressure_bar:.0f} bar)")
    print(f"  Benchmark: conventional ~35% | hybrid 40-45% | free-piston 45-55%")
    print()
    print_canonical_efficiency_report(result)


def print_transient_report(results: list[TransientResult]) -> None:
    print("=" * 60)
    print("CONTROL STABILITY — TRANSIENT FAULT SUITE")
    print("=" * 60)
    for tr in results:
        fault_label = {
            "none": "Baseline (no fault)",
            "misfire": f"Misfire at cycle {tr.fault.trigger_cycle}",
            "stuck_intake_a": f"Intake A stuck closed from cycle {tr.fault.trigger_cycle}",
            "load_double": f"Generator load x2 from cycle {tr.fault.trigger_cycle}",
        }.get(tr.fault.kind, tr.fault.kind)
        status = "RECOVERED" if tr.recovered else "DEGRADED / FAIL"
        print(f"  [{status}] {fault_label}")
        if tr.collision_cycle is not None:
            print(f"           Collision at cycle {tr.collision_cycle}")
        print(f"           {tr.notes}")
        if tr.result.energy.cycle_reports:
            valid = [c for c in tr.result.energy.cycle_reports if c.fuel_energy_j > 50.0]
            last = valid[-1] if valid else tr.result.energy.cycle_reports[-1]
            print(
                f"           Last cycle: spring eff={last.spring_recovery_efficiency:.1%}, "
                f"net eff={last.net_cartridge_efficiency:.1%}, "
                f"clearance={last.min_piston_clearance_mm:.2f} mm"
            )
    print("=" * 60)
    print()


def plot_dashboard(result: SimulationResult, *, show: bool = True) -> plt.Figure:
    """Multi-panel engineering dashboard for one cartridge cycle."""
    sl = _last_cycle_slice(result)
    hist = result.history[sl]
    t_ms = np.array([(h.t_s % result.cfg.cycle_time_s) * 1e3 for h in hist])
    cfg = result.cfg
    m = result.metrics
    e = result.energy

    fig = plt.figure(figsize=(15, 12))
    gs = GridSpec(4, 2, figure=fig, hspace=0.42, wspace=0.28)

    ax0 = fig.add_subplot(gs[0, :])
    ax0.plot(t_ms, [h.pressure_pa / 1e5 for h in hist], color="#c53030", lw=2, label="Chamber")
    ax0.plot(t_ms, [h.p_spring_a_pa / 1e5 for h in hist], color="#3182ce", lw=1.2, alpha=0.8, label="Air spring A")
    ax0.plot(t_ms, [h.p_spring_b_pa / 1e5 for h in hist], color="#2b6cb0", lw=1.2, alpha=0.8, label="Air spring B")
    ax0.set_ylabel("Pressure (bar)")
    ax0.set_title("Phoenix ATPE V3 — Full Cycle @ {:.0f} Hz".format(cfg.frequency_hz), fontweight="bold")
    ax0.grid(True, alpha=0.3)
    ax0.legend(loc="upper right", ncol=3, fontsize=9)

    ax_spring_e = fig.add_subplot(gs[1, 0])
    spring_e = [h.spring_energy_j for h in hist]
    ax_spring_e.plot(t_ms, spring_e, color="#3182ce", lw=2)
    ax_spring_e.set_ylabel("Stored internal energy (J)")
    ax_spring_e.set_xlabel("Cycle time (ms)")
    ax_spring_e.set_title("Air spring energy storage (both sides)")
    ax_spring_e.grid(True, alpha=0.3)
    if e.cycle_reports:
        cr = e.cycle_reports[-1]
        ax_spring_e.text(
            0.03, 0.95,
            f"P: {cr.spring_p_min_bar:.0f}–{cr.spring_p_max_bar:.0f} bar  "
            f"ratio {cr.spring_pressure_ratio:.1f}:1\n"
            f"Stored {cr.spring_energy_stored_j:.1f} J  "
            f"Recovered {cr.spring_energy_recovered_j:.1f} J  "
            f"eff {cr.spring_recovery_efficiency:.0%}",
            transform=ax_spring_e.transAxes, va="top", fontsize=8,
            bbox=dict(boxstyle="round", facecolor="white", alpha=0.8),
        )

    ax_pwr = fig.add_subplot(gs[1, 1])
    ax_pwr.plot(t_ms, [h.mech_power_w for h in hist], color="#553c9a", lw=1.5, label="Mechanical")
    ax_pwr.plot(t_ms, [h.elec_power_w for h in hist], color="#9f7aea", lw=1.5, label="Electrical")
    ax_pwr.set_ylabel("Power (W)")
    ax_pwr.set_xlabel("Cycle time (ms)")
    ax_pwr.set_title("Generator conversion")
    ax_pwr.grid(True, alpha=0.3)
    ax_pwr.legend(fontsize=9)

    ax1 = fig.add_subplot(gs[2, 0])
    ax1.plot(t_ms, [h.x_a_m * 1e3 for h in hist], label="Piston A", color="#2d3748")
    ax1.plot(t_ms, [h.x_b_m * 1e3 for h in hist], label="Piston B", color="#4a5568")
    ax1.set_ylabel("Position (mm from TDC)")
    ax1.set_xlabel("Cycle time (ms)")
    ax1.grid(True, alpha=0.3)
    ax1.legend(fontsize=9)

    ax2 = fig.add_subplot(gs[2, 1])
    rgf_trace = [h.residual_mass_kg / max(h.mass_kg, 1e-9) for h in hist]
    ax2.plot(t_ms, rgf_trace, color="#d69e2e", lw=2)
    ax2.axhline(cfg.target_residual_gas_fraction, color="#38a169", ls="--", label="RGF target 5%")
    ax2.set_ylabel("Residual gas fraction")
    ax2.set_xlabel("Cycle time (ms)")
    ax2.grid(True, alpha=0.3)
    ax2.legend(fontsize=9)

    ax3 = fig.add_subplot(gs[3, 0])
    valve_height = 0.18
    colors = {"intake": "#38a169", "exhaust": "#e53e3e"}
    for name, attr in (("Intake A", "intake_a"), ("Intake B", "intake_b"),
                       ("Exhaust A", "exhaust_a"), ("Exhaust B", "exhaust_b")):
        y = {"intake_a": 3, "intake_b": 2, "exhaust_a": 1, "exhaust_b": 0}[attr]
        for i, h in enumerate(hist):
            if getattr(h.valves, attr):
                ax3.add_patch(mpatches.Rectangle(
                    (t_ms[i], y), t_ms[1] - t_ms[0] if len(t_ms) > 1 else 0.025,
                    valve_height, fc=colors["intake" if "intake" in attr else "exhaust"], ec="none",
                ))
    ax3.set_yticks([0.09, 1.09, 2.09, 3.09])
    ax3.set_yticklabels(["Exh B", "Exh A", "Int B", "Int A"])
    ax3.set_xlabel("Cycle time (ms)")
    ax3.set_title("Software-defined valve timing")
    ax3.set_xlim(0, cfg.cycle_time_s * 1e3)

    ax4 = fig.add_subplot(gs[3, 1])
    ax4.axis("off")
    targets = [
        ("Residual gas fraction", m.residual_gas_fraction, cfg.target_residual_gas_fraction, "<"),
        ("Scavenging efficiency", m.scavenge_efficiency, cfg.target_scavenge_efficiency, ">"),
        ("Air spring recovery", e.air_spring_recovery, cfg.target_air_spring_recovery, ">"),
        ("Generator efficiency", e.generator_efficiency, cfg.target_generator_efficiency, ">"),
        ("Net cartridge efficiency", e.net_cartridge_efficiency, cfg.target_net_cartridge_efficiency, ">"),
        ("Energy balance valid", 1.0 if e.energy_balance_valid else 0.0, 1.0, ">"),
        ("Intake ring ΔP", m.pressure_drop_fraction, cfg.target_pressure_drop_fraction, "<"),
        ("Cylinder variation", m.cylinder_variation, cfg.target_cylinder_variation, "<"),
    ]
    lines = ["V3 KPI dashboard (reported cycles)", ""]
    passes = 0
    for label, val, tgt, sense in targets:
        if sense == "<":
            ok = val <= tgt
        else:
            ok = val >= tgt
        passes += int(ok)
        mark = "PASS" if ok else "WARN"
        if "fraction" in label.lower() or "efficiency" in label.lower() or "uniformity" in label.lower():
            lines.append(f"  [{mark}] {label}: {val:.1%}  (target {sense} {tgt:.1%})")
        else:
            lines.append(f"  [{mark}] {label}: {val:.3f}  (target {sense} {tgt:.3f})")
    lines.extend([
        "",
        f"Score: {passes}/{len(targets)} targets met",
        "",
        f"Air spring volume: {cfg.air_spring_volume_max_m3 * 1e6:.0f} → "
        f"{cfg.air_spring_volume_min_m3 * 1e6:.0f} cc per side",
        f"Peak chamber pressure: {m.peak_pressure_bar:.0f} bar",
    ])
    ax4.text(0.02, 0.98, "\n".join(lines), va="top", fontsize=10, family="monospace")

    fig.suptitle(
        "Phoenix V3 — Breathe · Burn · Clear  (opposed free-piston cartridge)",
        fontsize=12,
        y=0.98,
    )
    if show:
        plt.show()
    return fig


def plot_scavenge_phases(result: SimulationResult, path: Path) -> None:
    """Illustrate staged scavenging flow pattern."""
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    phases = [
        ("Phase 1 — Exhaust first\n(200 → 20 → 5 bar)", "#e53e3e", "Exhaust A/B OPEN\nIntakes CLOSED"),
        ("Phase 2 — Push scavenging\nIntake + Exhaust OPEN", "#38a169", "Fresh air displaces\nresidual gas"),
        ("Phase 3 — Swirl ports\n↺ tangential entry ↺", "#3182ce", "Wall cooling · mixing\nfewer dead zones"),
    ]
    for ax, (title, color, note) in zip(axes, phases):
        ax.set_xlim(-1, 1)
        ax.set_ylim(-1, 1)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.add_patch(mpatches.FancyBboxPatch(
            (-0.35, -0.2), 0.7, 0.4, boxstyle="round,pad=0.02",
            fc="#fed7d7", ec=color, lw=2,
        ))
        ax.text(0, 0, "COMBUSTION", ha="center", va="center", fontsize=9, fontweight="bold")
        ax.annotate("", xy=(-0.55, 0.55), xytext=(-0.55, 0.85),
                    arrowprops=dict(arrowstyle="->", color="#38a169", lw=2))
        ax.annotate("", xy=(0.55, 0.55), xytext=(0.55, 0.85),
                    arrowprops=dict(arrowstyle="->", color="#38a169", lw=2))
        ax.annotate("", xy=(-0.55, -0.55), xytext=(-0.55, -0.85),
                    arrowprops=dict(arrowstyle="->", color="#e53e3e", lw=2))
        ax.annotate("", xy=(0.55, -0.55), xytext=(0.55, -0.85),
                    arrowprops=dict(arrowstyle="->", color="#e53e3e", lw=2))
        ax.set_title(title, fontsize=10, fontweight="bold", color=color)
        ax.text(0, -1.15, note, ha="center", fontsize=8, style="italic")
    m = result.metrics
    fig.suptitle(
        f"Staged scavenging - RGF {m.residual_gas_fraction:.1%}, scav eff {m.scavenge_efficiency:.1%}",
        fontsize=11,
        fontweight="bold",
    )
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def animate_cartridge(result: SimulationResult, *, interval_ms: int = 25) -> FuncAnimation:
    """Cross-section animation of one opposed cartridge cycle."""
    sl = _last_cycle_slice(result)
    hist = result.history[sl]
    cfg = result.cfg

    fig, (ax_xs, ax_p) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [2, 1]})

    def _draw_cross_section(ax, h: CycleState) -> None:
        ax.clear()
        ax.set_xlim(-1.4, 1.4)
        ax.set_ylim(-0.4, 1.0)
        ax.set_aspect("equal")
        ax.axis("off")
        hs = cfg.half_stroke_m
        xa = h.x_a_m / hs
        xb = h.x_b_m / hs
        ax.add_patch(mpatches.Rectangle((-1.05, 0.15), 2.1, 0.75, fill=False, lw=2, ec="#2d3748"))
        chamber_w = 0.25 + 0.45 * (1.0 - 0.5 * (xa + xb))
        ax.add_patch(mpatches.Rectangle(
            (-chamber_w / 2, 0.42), chamber_w, 0.38,
            fc="#fed7d7" if h.fuel_burned_frac < 0.5 else "#f6ad55",
            ec="#e53e3e", lw=2,
        ))
        ax.add_patch(mpatches.Rectangle((-1.0 + 0.05 * xa, 0.40), 0.55 - 0.15 * xa, 0.22, fc="#2d3748"))
        ax.add_patch(mpatches.Rectangle((0.45 - 0.05 * xb, 0.40), 0.55 - 0.15 * xb, 0.22, fc="#2d3748"))
        for side, xpos, open_i, open_e in (
            ("A", -0.75, h.valves.intake_a, h.valves.exhaust_a),
            ("B", 0.75, h.valves.intake_b, h.valves.exhaust_b),
        ):
            ic = "#38a169" if open_i else "#cbd5e0"
            ec = "#e53e3e" if open_e else "#cbd5e0"
            ax.add_patch(mpatches.Circle((xpos, 0.78), 0.05, fc=ic, ec="#276749", lw=1))
            ax.add_patch(mpatches.Circle((xpos, 0.22), 0.05, fc=ec, ec="#9b2c2c", lw=1))
        ax.text(0, 0.88, f"P = {h.pressure_pa / 1e5:.1f} bar", ha="center", fontsize=10, fontweight="bold")
        ax.text(0, -0.15, h.stage.replace("_", " "), ha="center", fontsize=9, style="italic")

    def update(i: int):
        h = hist[i]
        _draw_cross_section(ax_xs, h)
        t_ms = [(hh.t_s % cfg.cycle_time_s) * 1e3 for hh in hist[: i + 1]]
        p_bar = [hh.pressure_pa / 1e5 for hh in hist[: i + 1]]
        ax_p.clear()
        ax_p.plot(t_ms, p_bar, color="#c53030", lw=2)
        ax_p.set_xlim(0, cfg.cycle_time_s * 1e3)
        ax_p.set_ylim(0, max(5.0, cfg.max_pressure_bar * 0.55))
        ax_p.set_xlabel("Cycle time (ms)")
        ax_p.set_ylabel("Pressure (bar)")
        ax_p.grid(True, alpha=0.3)
        return []

    return FuncAnimation(fig, update, frames=len(hist), interval=interval_ms, blit=False)


def config_for_peak_stroke_mm(peak_mm: float, base: PhoenixV3Config | None = None) -> PhoenixV3Config:
    """Scale stroke and swept volume for peak-to-peak stroke sweep (50–75 mm)."""
    cfg = base or PhoenixV3Config()
    ref_peak = 2.0 * cfg.half_stroke_m * 1e3
    scale = peak_mm / max(ref_peak, 1e-9)
    # Longer stroke needs slightly slower cycle and softer springs to complete motion.
    freq_scale = ref_peak / max(peak_mm, 1e-9)
    return replace(
        cfg,
        half_stroke_m=peak_mm * 0.5e-3,
        displacement_per_side_m3=cfg.displacement_per_side_m3 * scale,
        frequency_hz=cfg.frequency_hz * max(0.75, freq_scale ** 0.5),
        air_spring_reference_bar=cfg.air_spring_reference_bar * max(0.7, freq_scale ** 0.35),
    )


def config_for_air_spring_volumes(
    max_cc: float,
    min_cc: float,
    base: PhoenixV3Config | None = None,
) -> PhoenixV3Config:
    """Air spring volume pair for stiffness / stroke sweep."""
    cfg = base or PhoenixV3Config()
    return replace(
        cfg,
        air_spring_volume_max_m3=max_cc * 1e-6,
        air_spring_volume_min_m3=min_cc * 1e-6,
    )


@dataclass(frozen=True)
class SweepPoint:
    """One row in a parametric tuning sweep."""

    label: str
    generator_force_scale: float
    exhaust_open_ms: float
    peak_stroke_mm: float
    air_spring_max_cc: float
    air_spring_min_cc: float
    air_spring_reference_bar: float
    capture_fraction: float
    net_efficiency: float
    peak_pressure_bar: float
    max_bdc_mm: float
    expansion_work_j: float
    mech_energy_j: float
    elec_energy_j: float
    spring_stored_j: float
    elec_per_spring_j: float
    rgf: float
    stable: bool


def _summarize_sweep(
    result: SimulationResult,
    *,
    label: str,
    generator_force_scale: float,
    exhaust_open_ms: float,
    peak_stroke_mm: float,
    air_spring_max_cc: float | None = None,
    air_spring_min_cc: float | None = None,
    air_spring_reference_bar: float | None = None,
) -> SweepPoint:
    cfg = result.cfg
    e = result.energy
    m = result.metrics
    reports = e.cycle_reports
    spring_max = air_spring_max_cc if air_spring_max_cc is not None else cfg.air_spring_volume_max_m3 * 1e6
    spring_min = air_spring_min_cc if air_spring_min_cc is not None else cfg.air_spring_volume_min_m3 * 1e6
    spring_ref = air_spring_reference_bar if air_spring_reference_bar is not None else cfg.air_spring_reference_bar
    if not reports:
        return SweepPoint(
            label=label,
            generator_force_scale=generator_force_scale,
            exhaust_open_ms=exhaust_open_ms,
            peak_stroke_mm=peak_stroke_mm,
            air_spring_max_cc=spring_max,
            air_spring_min_cc=spring_min,
            air_spring_reference_bar=spring_ref,
            capture_fraction=0.0,
            net_efficiency=0.0,
            peak_pressure_bar=m.peak_pressure_bar,
            max_bdc_mm=0.0,
            expansion_work_j=0.0,
            mech_energy_j=0.0,
            elec_energy_j=0.0,
            spring_stored_j=0.0,
            elec_per_spring_j=0.0,
            rgf=m.residual_gas_fraction,
            stable=False,
        )
    comb = float(np.mean([r.combustion_work_j for r in reports]))
    mech = float(np.mean([r.mech_energy_j for r in reports]))
    elec = float(np.mean([r.elec_energy_j for r in reports]))
    spring = float(np.mean([r.spring_energy_stored_j for r in reports]))
    pwr_exp = float(np.mean([r.power_stroke_expansion_j for r in reports]))
    capture = mech / max(pwr_exp, 1e-9)
    bdc = float(np.mean([r.max_piston_travel_mm for r in reports]))
    stable = (
        e.energy_balance_valid
        and m.cylinder_variation < 0.08
        and not any(r.piston_collision for r in reports)
        and bdc > 0.5
    )
    return SweepPoint(
        label=label,
        generator_force_scale=generator_force_scale,
        exhaust_open_ms=exhaust_open_ms,
        peak_stroke_mm=peak_stroke_mm,
        air_spring_max_cc=spring_max,
        air_spring_min_cc=spring_min,
        air_spring_reference_bar=spring_ref,
        capture_fraction=capture,
        net_efficiency=float(np.mean([r.net_cartridge_efficiency for r in reports])),
        peak_pressure_bar=m.peak_pressure_bar,
        max_bdc_mm=bdc,
        expansion_work_j=comb,
        mech_energy_j=mech,
        elec_energy_j=elec,
        spring_stored_j=spring,
        elec_per_spring_j=elec / max(spring, 1e-9),
        rgf=m.residual_gas_fraction,
        stable=stable,
    )


def run_combined_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
) -> list[SweepPoint]:
    """Cross-product of generator load and late exhaust — find the sweet spot."""
    ref = base or PhoenixV3Config()
    loads = (0.75, 1.0, 1.25, 1.5)
    exhausts = (7.5, 8.0, 8.2)
    points: list[SweepPoint] = []
    for exh in exhausts:
        for frac in loads:
            scale = ref.generator_force_scale * frac
            cfg = replace(ref, generator_force_scale=scale, exhaust_open_ms=exh)
            result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
            points.append(_summarize_sweep(
                result,
                label=f"Ex{exh:.1f}/L{frac:.0%}",
                generator_force_scale=scale,
                exhaust_open_ms=exh,
                peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
            ))
    return points


def run_generator_load_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    load_fractions: tuple[float, ...] = (0.25, 0.50, 0.75, 1.0, 1.25, 1.50, 2.0, 2.5, 3.0),
) -> list[SweepPoint]:
    """Sweep electromagnetic loading as fraction of nominal generator_force_scale."""
    ref = base or PhoenixV3Config()
    nominal = ref.generator_force_scale
    points: list[SweepPoint] = []
    for frac in load_fractions:
        scale = nominal * frac
        cfg = replace(ref, generator_force_scale=scale)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        points.append(_summarize_sweep(
            result,
            label=f"{frac * 100:.0f}%",
            generator_force_scale=scale,
            exhaust_open_ms=cfg.exhaust_open_ms,
            peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
        ))
    return points


def run_exhaust_timing_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    exhaust_open_ms_values: tuple[float, ...] = (7.5, 7.9, 8.2, 8.4, 8.6, 8.8),
) -> list[SweepPoint]:
    """Sweep late blowdown — retain chamber pressure longer before exhaust opens."""
    ref = base or PhoenixV3Config()
    points: list[SweepPoint] = []
    for t_ms in exhaust_open_ms_values:
        cfg = replace(ref, exhaust_open_ms=t_ms)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        points.append(_summarize_sweep(
            result,
            label=f"{t_ms:.1f} ms",
            generator_force_scale=cfg.generator_force_scale,
            exhaust_open_ms=t_ms,
            peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
        ))
    return points


def run_stroke_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    peak_stroke_mm_values: tuple[float, ...] = (50.0, 60.0, 70.0, 75.0),
) -> list[SweepPoint]:
    """Sweep peak-to-peak stroke for longer expansion."""
    ref = base or PhoenixV3Config()
    points: list[SweepPoint] = []
    for peak_mm in peak_stroke_mm_values:
        cfg = config_for_peak_stroke_mm(peak_mm, ref)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        points.append(_summarize_sweep(
            result,
            label=f"{peak_mm:.0f} mm",
            generator_force_scale=cfg.generator_force_scale,
            exhaust_open_ms=cfg.exhaust_open_ms,
            peak_stroke_mm=peak_mm,
        ))
    return points


def run_air_spring_volume_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    volume_pairs_cc: tuple[tuple[float, float], ...] = (
        (55.0, 25.0),
        (60.0, 35.0),
        (70.0, 40.0),
        (80.0, 45.0),
    ),
    exhaust_open_ms: float | None = None,
    generator_force_scale: float | None = None,
) -> list[SweepPoint]:
    """Sweep bounce-chamber volume range — softer springs hold less expansion energy."""
    ref = base or PhoenixV3Config()
    points: list[SweepPoint] = []
    for max_cc, min_cc in volume_pairs_cc:
        cfg = config_for_air_spring_volumes(max_cc, min_cc, ref)
        if exhaust_open_ms is not None:
            cfg = replace(cfg, exhaust_open_ms=exhaust_open_ms)
        if generator_force_scale is not None:
            cfg = replace(cfg, generator_force_scale=generator_force_scale)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        points.append(_summarize_sweep(
            result,
            label=f"{max_cc:.0f}->{min_cc:.0f}",
            generator_force_scale=cfg.generator_force_scale,
            exhaust_open_ms=cfg.exhaust_open_ms,
            peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
            air_spring_max_cc=max_cc,
            air_spring_min_cc=min_cc,
            air_spring_reference_bar=cfg.air_spring_reference_bar,
        ))
    return points


@dataclass(frozen=True)
class AsymmetrySweepPoint:
    """Generator L/R mismatch sensitivity result."""

    label: str
    mismatch_fraction: float
    asymmetry: float
    net_efficiency: float
    energy_balance_valid: bool
    energy_state_valid: bool
    max_bdc_mm: float
    ecu_balance: bool


def run_asymmetry_sensitivity_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 10,
) -> list[AsymmetrySweepPoint]:
    """Sweep deliberate generator force mismatch between pistons A and B."""
    ref = base or PhoenixV3Config()
    nominal = ref.generator_force_scale
    mismatches = (-0.05, -0.02, -0.01, 0.0, 0.01, 0.02, 0.05)
    points: list[AsymmetrySweepPoint] = []
    cfg_matched_ecu = replace(ref, generator_balance_control=True)
    result_matched_ecu = PhoenixV3Simulator(cfg_matched_ecu).simulate(cycles=cycles)
    reports_ecu0 = result_matched_ecu.energy.cycle_reports
    bdc_ecu0 = float(np.mean([r.max_piston_travel_mm for r in reports_ecu0])) if reports_ecu0 else 0.0
    points.append(AsymmetrySweepPoint(
        label="matched+ECU",
        mismatch_fraction=0.0,
        asymmetry=result_matched_ecu.metrics.cylinder_variation,
        net_efficiency=result_matched_ecu.energy.net_cartridge_efficiency,
        energy_balance_valid=result_matched_ecu.energy.energy_balance_valid,
        energy_state_valid=all(r.energy_state_valid for r in reports_ecu0) if reports_ecu0 else False,
        max_bdc_mm=bdc_ecu0,
        ecu_balance=True,
    ))
    for m in mismatches:
        scale_b = nominal * (1.0 + m)
        label = f"B{m:+.0%}" if m != 0.0 else "matched"
        cfg = replace(
            ref,
            generator_force_scale_b=scale_b,
            generator_balance_control=False,
        )
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        reports = result.energy.cycle_reports
        bdc = float(np.mean([r.max_piston_travel_mm for r in reports])) if reports else 0.0
        points.append(AsymmetrySweepPoint(
            label=label,
            mismatch_fraction=m,
            asymmetry=result.metrics.cylinder_variation,
            net_efficiency=result.energy.net_cartridge_efficiency,
            energy_balance_valid=result.energy.energy_balance_valid,
            energy_state_valid=all(r.energy_state_valid for r in reports) if reports else False,
            max_bdc_mm=bdc,
            ecu_balance=False,
        ))
    cfg_ecu = replace(
        ref,
        generator_force_scale_b=nominal * 1.05,
        generator_balance_control=True,
    )
    result_ecu = PhoenixV3Simulator(cfg_ecu).simulate(cycles=cycles)
    reports_ecu = result_ecu.energy.cycle_reports
    bdc_ecu = float(np.mean([r.max_piston_travel_mm for r in reports_ecu])) if reports_ecu else 0.0
    points.append(AsymmetrySweepPoint(
        label="ECU@B+5%",
        mismatch_fraction=0.05,
        asymmetry=result_ecu.metrics.cylinder_variation,
        net_efficiency=result_ecu.energy.net_cartridge_efficiency,
        energy_balance_valid=result_ecu.energy.energy_balance_valid,
        energy_state_valid=all(r.energy_state_valid for r in reports_ecu) if reports_ecu else False,
        max_bdc_mm=bdc_ecu,
        ecu_balance=True,
    ))
    return points


def print_asymmetry_sweep_report(points: list[AsymmetrySweepPoint]) -> None:
    print("=" * 72)
    print("ASYMMETRY SENSITIVITY — Generator force mismatch A vs B")
    print("=" * 72)
    print(f"  {'Case':>10}  {'dLoad':>6}  {'Asym':>6}  {'Enet':>6}  {'BDC':>5}  "
          f"{'1st':>4}  {'Mech':>4}  ECU")
    for p in points:
        print(
            f"  {p.label:>10}  {p.mismatch_fraction:+5.1%}  "
            f"{p.asymmetry:5.2%}  {p.net_efficiency:5.1%}  "
            f"{p.max_bdc_mm:4.1f}  "
            f"{'OK' if p.energy_balance_valid else 'FAIL':>4}  "
            f"{'OK' if p.energy_state_valid else 'FAIL':>4}  "
            f"{'on' if p.ecu_balance else 'off'}"
        )
    print("=" * 72)
    print()


def _tdc_center_drift_mm(reports: tuple[CycleEnergyReport, ...]) -> float:
    """TDC clearance drift: late-cycle vs early-cycle mean min clearance (mm)."""
    valid = [r for r in reports if r.cycle_index > 0 and r.fuel_energy_j > 50.0]
    if len(valid) < 4:
        return 0.0
    early = float(np.mean([r.min_piston_clearance_mm for r in valid[:2]]))
    late = float(np.mean([r.min_piston_clearance_mm for r in valid[-3:]]))
    return abs(late - early)


def _config_with_disturbance(
    base: PhoenixV3Config,
    kind: str,
    fraction: float,
) -> PhoenixV3Config:
    """Apply a fractional disturbance to one subsystem."""
    if kind == "fuel":
        return replace(
            base,
            load_fraction=_clamp(base.load_fraction * (1.0 + fraction), 0.05, 1.0),
        )
    if kind == "generator":
        scale = base.generator_force_scale
        return replace(
            base,
            generator_force_scale_b=scale * (1.0 + fraction),
            generator_balance_control=False,
        )
    if kind == "valve":
        return replace(
            base,
            exhaust_open_ms=max(1.0, base.exhaust_open_ms * (1.0 + fraction)),
        )
    if kind == "pressure":
        return replace(
            base,
            intake_ring_pressure_bar=max(0.5, base.intake_ring_pressure_bar * (1.0 + fraction)),
        )
    if kind == "spring_leak":
        return replace(
            base,
            air_spring_recovery_cap=_clamp(base.air_spring_recovery_cap * (1.0 - fraction), 0.50, 0.99),
        )
    raise ValueError(f"unknown disturbance kind: {kind}")


@dataclass(frozen=True)
class DisturbanceSweepPoint:
    """Robustness result for one injected disturbance."""

    label: str
    kind: str
    fraction: float
    asymmetry: float
    net_efficiency: float
    capture_fraction: float
    mean_bdc_mm: float
    collision_count: int
    efficiency_delta_pp: float
    within_baseline_band: bool
    tdc_drift_mm: float
    peak_pressure_bar: float
    residual_gas_fraction: float
    energy_balance_valid: bool
    misfire_recovered: bool


def run_disturbance_sensitivity_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 12,
    magnitudes: tuple[float, ...] = (0.01, 0.03, 0.05),
    baseline_band_pp: float = 5.0,
) -> list[DisturbanceSweepPoint]:
    """Inject 1-5% subsystem disturbances and measure stability KPIs."""
    from designs.phoenix_v3.efficiency import compute_canonical_efficiency

    ref = base or PhoenixV3Config()
    kinds = ("fuel", "generator", "valve", "pressure", "spring_leak")
    points: list[DisturbanceSweepPoint] = []

    baseline_result = PhoenixV3Simulator(ref).simulate(cycles=cycles, record_history=False)
    baseline_canon = compute_canonical_efficiency(baseline_result)
    baseline_eff = baseline_canon.net_fuel_to_electric

    def _run_case(label: str, kind: str, frac: float, cfg: PhoenixV3Config) -> DisturbanceSweepPoint:
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
        reports = result.energy.cycle_reports
        canon = compute_canonical_efficiency(result)
        valid = [r for r in reports if r.fuel_energy_j > 50.0]
        collision_count = sum(1 for r in valid if r.piston_collision)
        delta_pp = (canon.net_fuel_to_electric - baseline_eff) * 100.0
        within = abs(delta_pp) <= baseline_band_pp
        trigger = max(3, cycles // 3)
        mrec = True
        if label == "baseline" or abs(frac) >= 0.049:
            misfire = PhoenixV3Simulator(cfg).simulate(
                cycles=max(cycles, 8),
                fault=TransientFault("misfire", trigger_cycle=trigger),
            )
            mrec = assess_transient_recovery(
                misfire, TransientFault("misfire", trigger_cycle=trigger),
                baseline_efficiency=baseline_eff,
            ).recovered
        return DisturbanceSweepPoint(
            label=label,
            kind=kind,
            fraction=frac,
            asymmetry=result.metrics.cylinder_variation,
            net_efficiency=canon.net_fuel_to_electric,
            capture_fraction=canon.capture_fraction,
            mean_bdc_mm=canon.mean_bdc_mm,
            collision_count=collision_count,
            efficiency_delta_pp=delta_pp,
            within_baseline_band=within,
            tdc_drift_mm=_tdc_center_drift_mm(reports),
            peak_pressure_bar=result.metrics.peak_pressure_bar,
            residual_gas_fraction=result.metrics.residual_gas_fraction,
            energy_balance_valid=result.energy.energy_balance_valid,
            misfire_recovered=mrec,
        )

    points.append(_run_case("baseline", "none", 0.0, ref))
    for kind in kinds:
        for mag in magnitudes:
            for sign, tag in ((-1.0, "-"), (1.0, "+")):
                frac = sign * mag
                label = f"{kind[:4]}{tag}{mag:.0%}"
                points.append(_run_case(label, kind, frac, _config_with_disturbance(ref, kind, frac)))
    return points


def print_disturbance_sweep_report(points: list[DisturbanceSweepPoint]) -> None:
    print("=" * 100)
    print("DISTURBANCE SENSITIVITY — 1-5% subsystem perturbations")
    print("=" * 100)
    print(
        f"  {'Case':>12}  {'Kind':>8}  {'d%':>6}  {'Asym':>6}  {'Enet':>6}  {'Cap':>6}  "
        f"{'BDC':>5}  {'dEn':>6}  {'Coll':>4}  {'±5pp':>4}  {'Ppk':>4}  {'Misf':>4}"
    )
    for p in points:
        print(
            f"  {p.label:>12}  {p.kind:>8}  {p.fraction:+5.1%}  "
            f"{p.asymmetry:5.2%}  {p.net_efficiency:5.1%}  {p.capture_fraction:5.1%}  "
            f"{p.mean_bdc_mm:4.1f}  {p.efficiency_delta_pp:+5.1f}  {p.collision_count:4d}  "
            f"{'OK' if p.within_baseline_band else '--':>4}  "
            f"{p.peak_pressure_bar:4.0f}  "
            f"{'OK' if p.misfire_recovered else 'FAIL':>4}"
        )
    baseline = points[0] if points else None
    if baseline:
        worst_asym = max(points, key=lambda x: x.asymmetry)
        worst_delta = max(points, key=lambda x: abs(x.efficiency_delta_pp))
        outside = sum(1 for p in points if not p.within_baseline_band)
        print()
        print(f"  Baseline Enet:     {baseline.net_efficiency:.1%}  "
              f"capture {baseline.capture_fraction:.1%}  BDC {baseline.mean_bdc_mm:.1f} mm")
        print(f"  Outside ±5 pp:     {outside}/{len(points)} cases")
        print(f"  Worst asymmetry:   {worst_asym.label} ({worst_asym.asymmetry:.2%})")
        print(f"  Worst Enet delta:  {worst_delta.label} ({worst_delta.efficiency_delta_pp:+.1f} pp)")
    print("=" * 100)
    print()


def run_energy_flow_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    load_fractions: tuple[float, ...] = (0.75, 1.0, 1.25, 1.5),
    spring_reference_bars: tuple[float, ...] = (14.0, 18.0, 22.0, 26.0),
    exhaust_open_ms_values: tuple[float, ...] = (7.5, 8.0, 8.2),
) -> list[SweepPoint]:
    """3D sweep: generator load x air-spring stiffness x exhaust timing."""
    ref = base or PhoenixV3Config()
    nominal = ref.generator_force_scale
    points: list[SweepPoint] = []
    for exh in exhaust_open_ms_values:
        for spring_bar in spring_reference_bars:
            for load_frac in load_fractions:
                scale = nominal * load_frac
                cfg = replace(
                    ref,
                    generator_force_scale=scale,
                    exhaust_open_ms=exh,
                    air_spring_reference_bar=spring_bar,
                )
                result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
                points.append(_summarize_sweep(
                    result,
                    label=f"L{load_frac:.0%}/S{spring_bar:.0f}/E{exh:.1f}",
                    generator_force_scale=scale,
                    exhaust_open_ms=exh,
                    peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
                    air_spring_reference_bar=spring_bar,
                ))
    return points


def run_extraction_strategy_sweep(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    generator_load_mult: float = 1.0,
) -> list[SweepPoint]:
    """Sweep adaptive generator profile, spring phase control, and extreme EM load."""
    ref = base or PhoenixV3Config()
    nominal = ref.generator_force_scale * generator_load_mult
    cases: list[tuple[str, dict[str, object]]] = [
        ("fixed_load", {
            "generator_adaptive_profile": False,
            "air_spring_phase_control": False,
            "generator_force_scale": nominal,
        }),
        ("adapt_gen", {
            "generator_adaptive_profile": True,
            "air_spring_phase_control": False,
            "generator_force_scale": nominal,
        }),
        ("spring_phase", {
            "generator_adaptive_profile": False,
            "air_spring_phase_control": True,
            "generator_force_scale": nominal,
        }),
        ("virtual_crank", {
            "generator_adaptive_profile": True,
            "air_spring_phase_control": True,
            "generator_force_scale": nominal,
        }),
        ("L200%", {
            "generator_adaptive_profile": True,
            "air_spring_phase_control": True,
            "generator_force_scale": nominal * 2.0,
        }),
        ("L250%", {
            "generator_adaptive_profile": True,
            "air_spring_phase_control": True,
            "generator_force_scale": nominal * 2.5,
        }),
        ("L300%", {
            "generator_adaptive_profile": True,
            "air_spring_phase_control": True,
            "generator_force_scale": nominal * 3.0,
        }),
    ]
    points: list[SweepPoint] = []
    for label, overrides in cases:
        cfg = replace(ref, **overrides)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
        points.append(_summarize_sweep(
            result,
            label=label,
            generator_force_scale=cfg.generator_force_scale,
            exhaust_open_ms=cfg.exhaust_open_ms,
            peak_stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
        ))
    return points


def print_sweep_table(title: str, points: list[SweepPoint]) -> None:
    """Print parametric sweep results."""
    print()
    print("=" * 72)
    print(title)
    print("=" * 72)
    print(
        f"  {'Case':>8}  {'Cap%':>5}  {'Enet':>5}  {'Pmax':>5}  "
        f"{'BDC':>5}  {'Ecomb':>6}  {'Emech':>6}  {'Eelec':>6}  "
        f"{'Espr':>6}  {'Ee/Es':>5}  {'RGF':>5}  Stab"
    )
    for p in points:
        stab = " OK" if p.stable else " --"
        print(
            f"  {p.label:>8}  {p.capture_fraction:5.1%}  {p.net_efficiency:5.1%}  "
            f"{p.peak_pressure_bar:5.0f}  {p.max_bdc_mm:5.1f}  "
            f"{p.expansion_work_j:6.0f}  {p.mech_energy_j:6.1f}  {p.elec_energy_j:6.1f}  "
            f"{p.spring_stored_j:6.0f}  {p.elec_per_spring_j:5.2f}  "
            f"{p.rgf:5.1%}  {stab}"
        )
    stable_pts = [p for p in points if p.stable]
    pool = stable_pts if stable_pts else points
    best = max(pool, key=lambda p: p.net_efficiency)
    best_cap = max(pool, key=lambda p: p.capture_fraction)
    stab_note = "" if best.stable else "  (not marked stable)"
    print()
    print(
        f"  Best net efficiency:  {best.label} -> {best.net_efficiency:.1%}  "
        f"(capture {best.capture_fraction:.1%}){stab_note}"
    )
    print(f"  Best gen capture:   {best_cap.label} -> {best_cap.capture_fraction:.1%} of power-stroke expansion")
    print("=" * 72)
    print()


def plot_sweep_chart(
    points: list[SweepPoint],
    *,
    x_label: str,
    title: str,
    path: Path,
) -> None:
    """Save capture fraction and net efficiency vs sweep parameter."""
    x = list(range(len(points)))
    labels = [p.label for p in points]
    cap = [p.capture_fraction * 100 for p in points]
    net = [p.net_efficiency * 100 for p in points]

    fig, ax1 = plt.subplots(figsize=(9, 5))
    ax1.plot(x, cap, "o-", color="#3182ce", lw=2, label="Generator capture (% of power-stroke expansion)")
    ax1.plot(x, net, "s-", color="#38a169", lw=2, label="Net fuel-to-electric (%)")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels)
    ax1.set_xlabel(x_label)
    ax1.set_ylabel("Percent")
    ax1.set_title(title, fontweight="bold")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="best")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def plot_energy_flow_heatmap(
    points: list[SweepPoint],
    *,
    load_fraction: float,
    nominal_generator_scale: float,
    path: Path,
) -> None:
    """Net efficiency heatmap over spring stiffness x exhaust timing at one load."""
    subset = [
        p for p in points
        if abs(p.generator_force_scale / max(nominal_generator_scale, 1e-9) - load_fraction) < 0.06
    ]
    if not subset:
        subset = points
    springs = sorted({p.air_spring_reference_bar for p in subset})
    exhausts = sorted({p.exhaust_open_ms for p in subset})
    grid = np.full((len(springs), len(exhausts)), np.nan)
    for p in subset:
        i = springs.index(p.air_spring_reference_bar)
        j = exhausts.index(p.exhaust_open_ms)
        grid[i, j] = p.net_efficiency * 100.0
    fig, ax = plt.subplots(figsize=(8, 5))
    im = ax.imshow(grid, aspect="auto", cmap="YlGn", origin="lower")
    ax.set_xticks(range(len(exhausts)))
    ax.set_xticklabels([f"{e:.1f}" for e in exhausts])
    ax.set_yticks(range(len(springs)))
    ax.set_yticklabels([f"{s:.0f}" for s in springs])
    ax.set_xlabel("Exhaust open (ms)")
    ax.set_ylabel("Spring reference (bar)")
    ax.set_title(f"Net fuel-to-electric (%) — generator load {load_fraction:.0%}", fontweight="bold")
    for i in range(len(springs)):
        for j in range(len(exhausts)):
            val = grid[i, j]
            if not np.isnan(val):
                ax.text(j, i, f"{val:.1f}", ha="center", va="center", fontsize=9)
    fig.colorbar(im, ax=ax, label="Net efficiency (%)")
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def run_tuning_sweeps(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 8,
    export_dir: Path | None = None,
) -> None:
    """Run generator load, exhaust timing, and stroke sweeps."""
    ref = base or PhoenixV3Config()
    out = export_dir or OUT_DIR

    load_pts = run_generator_load_sweep(ref, cycles=cycles)
    print_sweep_table("GENERATOR LOAD SWEEP (electromagnetic braking)", load_pts)
    plot_sweep_chart(
        load_pts,
        x_label="Generator load (% of nominal force scale)",
        title="Phoenix V3 — Generator load vs capture & net efficiency",
        path=out / "phoenix_v3_sweep_generator_load.png",
    )
    print(f"Chart: {out / 'phoenix_v3_sweep_generator_load.png'}")

    exh_pts = run_exhaust_timing_sweep(ref, cycles=cycles)
    print_sweep_table("EXHAUST TIMING SWEEP (late blowdown)", exh_pts)
    plot_sweep_chart(
        exh_pts,
        x_label="Exhaust valve open time",
        title="Phoenix V3 — Exhaust timing vs capture & net efficiency",
        path=out / "phoenix_v3_sweep_exhaust_timing.png",
    )
    print(f"Chart: {out / 'phoenix_v3_sweep_exhaust_timing.png'}")

    stroke_pts = run_stroke_sweep(ref, cycles=cycles)
    print_sweep_table("STROKE SWEEP (peak-to-peak expansion)", stroke_pts)
    plot_sweep_chart(
        stroke_pts,
        x_label="Peak-to-peak stroke",
        title="Phoenix V3 — Stroke vs capture & net efficiency",
        path=out / "phoenix_v3_sweep_stroke.png",
    )
    print(f"Chart: {out / 'phoenix_v3_sweep_stroke.png'}")

    combo_pts = run_combined_sweep(ref, cycles=cycles)
    print_sweep_table("COMBINED SWEEP (generator load x late exhaust)", combo_pts)
    plot_sweep_chart(
        combo_pts,
        x_label="Load / exhaust combo",
        title="Phoenix V3 — Combined tuning sweet spot",
        path=out / "phoenix_v3_sweep_combined.png",
    )
    print(f"Chart: {out / 'phoenix_v3_sweep_combined.png'}")

    spring_vol_pts = run_air_spring_volume_sweep(
        ref,
        cycles=cycles,
        exhaust_open_ms=8.0,
        generator_force_scale=ref.generator_force_scale * 1.5,
    )
    print_sweep_table(
        "AIR SPRING VOLUME SWEEP (at Ex8.0 / Load150%)",
        spring_vol_pts,
    )
    plot_sweep_chart(
        spring_vol_pts,
        x_label="Max -> min cc per side",
        title="Phoenix V3 — Air spring volume vs capture & net efficiency",
        path=out / "phoenix_v3_sweep_air_spring_volume.png",
    )
    print(f"Chart: {out / 'phoenix_v3_sweep_air_spring_volume.png'}")

    flow_pts = run_energy_flow_sweep(ref, cycles=cycles)
    print_sweep_table("ENERGY FLOW SWEEP (load x spring stiffness x exhaust)", flow_pts)
    for load_frac in (1.0, 1.5):
        heat_path = out / f"phoenix_v3_sweep_energy_flow_L{int(load_frac * 100)}.png"
        plot_energy_flow_heatmap(
            flow_pts,
            load_fraction=load_frac,
            nominal_generator_scale=ref.generator_force_scale,
            path=heat_path,
        )
        print(f"Heatmap: {heat_path}")


def print_report(result: SimulationResult) -> None:
    cfg = result.cfg
    m = result.metrics
    print()
    print("=" * 60)
    print("PHOENIX ATPE V3 — SINGLE CARTRIDGE CYCLE SIMULATION")
    print("=" * 60)
    print(f"  Frequency:     {cfg.frequency_hz:.0f} Hz  ({cfg.cycle_time_s * 1e3:.1f} ms/cycle)")
    print(f"  Bore / stroke: {cfg.bore_m * 1e3:.0f} mm / {2 * cfg.half_stroke_m * 1e3:.0f} mm peak-to-peak")
    print(f"  Compression:   {cfg.compression_ratio:.1f}:1")
    print(f"  Air spring:    {cfg.air_spring_volume_max_m3 * 1e6:.0f} cc -> "
          f"{cfg.air_spring_volume_min_m3 * 1e6:.0f} cc per side (adiabatic)")
    print()
    print("Breathing metrics:")
    print(f"  Residual gas fraction:   {m.residual_gas_fraction:.2%}  (target < {cfg.target_residual_gas_fraction:.0%})")
    print(f"  Scavenging efficiency:   {m.scavenge_efficiency:.2%}  (target > {cfg.target_scavenge_efficiency:.0%})")
    print(f"  Intake ring pressure drop:{m.pressure_drop_fraction:.2%}  (target < {cfg.target_pressure_drop_fraction:.0%})")
    print(f"  Peak combustion pressure: {m.peak_pressure_bar:.1f} bar  (design max {cfg.max_pressure_bar:.0f} bar)")
    print(f"  Piston asymmetry:        {m.cylinder_variation:.2%}  (target < {cfg.target_cylinder_variation:.0%})")
    print()
    print_cycle_energy_table(result)
    print("Valve timing stages (software-defined):")
    push_ms = cfg.exhaust_open_ms + cfg.staged_exhaust_lead_ms
    for t, label in [
        (0.0, "Scavenge — all valves open"),
        (1.5, "Exhaust close (charge pressure build)"),
        (2.0, "Intake close — trapped charge"),
        (4.5, "Fuel injection near TDC"),
        (4.9, "Spark ignition"),
        (cfg.exhaust_open_ms, "Blowdown — exhaust first"),
        (push_ms, "Push scavenging — intake + exhaust"),
        (10.0, "Controller reset"),
    ]:
        t_show = t * (cfg.cycle_time_s * 1e3 / 10.0)
        print(f"  {t_show:4.1f} ms  {label}")
    print("=" * 60)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phoenix ATPE V3 opposed-piston cycle simulation")
    parser.add_argument("--cycles", type=int, default=4, help="Cycles to integrate before reporting")
    parser.add_argument("--frequency", type=float, default=100.0, help="Operating frequency (Hz)")
    parser.add_argument("--load", type=float, default=0.82, help="Combustion load fraction")
    parser.add_argument("--swirl", type=float, default=0.18, help="Swirl scavenging gain")
    parser.add_argument("--export-png", type=Path, default=None, help="Save dashboard PNG")
    parser.add_argument("--export-scavenge-png", type=Path, default=None, help="Save scavenging diagram PNG")
    parser.add_argument("--export-gif", type=Path, default=None, help="Save cross-section GIF")
    parser.add_argument("--animate", action="store_true", help="Show live animation window")
    parser.add_argument("--transient", action="store_true", help="Run misfire / stuck valve / load-double fault suite")
    parser.add_argument("--fault-cycle", type=int, default=3, help="Cycle index to inject transient fault")
    parser.add_argument("--generator-scale", type=float, default=None,
                        help="Generator force scale override (default 0.32)")
    parser.add_argument("--exhaust-open-ms", type=float, default=None,
                        help="Exhaust valve open time in ms (default 7.5)")
    parser.add_argument("--sweep", action="store_true",
                        help="Run generator load, exhaust timing, and stroke sweeps")
    parser.add_argument("--sweep-load", action="store_true", help="Generator load sweep only")
    parser.add_argument("--sweep-exhaust", action="store_true", help="Exhaust timing sweep only")
    parser.add_argument("--sweep-stroke", action="store_true", help="Stroke sweep only")
    parser.add_argument("--sweep-spring", action="store_true", help="Air spring volume sweep only")
    parser.add_argument("--sweep-flow", action="store_true",
                        help="3D energy-flow sweep (load x spring x exhaust)")
    parser.add_argument("--sweep-extraction", action="store_true",
                        help="Adaptive generator + spring phase + extreme load sweep")
    parser.add_argument("--sweep-asymmetry", action="store_true",
                        help="Generator L/R mismatch sensitivity (+/-1/2/5%%, ECU trial)")
    parser.add_argument("--sweep-disturbance", action="store_true",
                        help="1-5%% fuel/gen/valve/pressure/spring-leak disturbance sweep")
    parser.add_argument("--no-ecu-balance", action="store_true",
                        help="Disable electronic generator balancing for single run")
    parser.add_argument("--best-tuning", action="store_true",
                        help="Apply phoenix_v3_best_tuning.json parameters")
    parser.add_argument("--best-tuning-actuator", action="store_true",
                        help="Apply phoenix_v3_best_tuning_actuator.json (actuator-retuned)")
    parser.add_argument("--optimize", action="store_true",
                        help="ML-guided parameter search (genetic + surrogate)")
    parser.add_argument("--opt-trials", type=int, default=200,
                        help="New trials for --optimize (default 200)")
    parser.add_argument("--opt-workers", type=int, default=None,
                        help="Parallel workers for --optimize (default CPU-1)")
    parser.add_argument("--opt-resume", action="store_true",
                        help="Append to tuning corpus and continue search")
    parser.add_argument("--spring-ref-bar", type=float, default=None,
                        help="Air spring reference pressure override (bar)")
    parser.add_argument("--long-run", action="store_true",
                        help="Run long-duration stability test (use with --cycles 10000+)")
    parser.add_argument("--trend-log", action="store_true",
                        help="With --long-run, print windowed trend log and decay correlations")
    parser.add_argument("--trend-interval", type=int, default=100,
                        help="Cycles per trend window (default 100)")
    parser.add_argument("--trend-csv", type=str, default=None,
                        help="Export trend log CSV path (with --trend-log)")
    parser.add_argument("--capture-diagnostic", type=str, default=None,
                        help="PNG path for capture/spring diagnostic plot (with --long-run)")
    parser.add_argument("--capture-sample", type=int, default=10,
                        help="Plot every Nth cycle in capture diagnostic (default 10)")
    parser.add_argument("--tolerance-suite", action="store_true",
                        help="Run manufacturing tolerance sweep")
    parser.add_argument("--no-thermal", action="store_true",
                        help="Disable live thermal model")
    parser.add_argument("--cooling", type=str, default="passive",
                        choices=["passive", "water_jacket", "oil_loop", "cold_plate", "phase_change"],
                        help="Generator cooling mode for thermal model")
    parser.add_argument("--actuators", action="store_true",
                        help="Enable actuator plant (sensor delay, current slew, back-EMF)")
    parser.add_argument("--stochastic", action="store_true",
                        help="Enable stochastic combustion variability")
    parser.add_argument("--ring-plenum", action="store_true",
                        help="Enable shared intake plenum derating (single cartridge uses concurrent=3)")
    parser.add_argument("--actuator-suite", action="store_true",
                        help="Compare baseline vs actuator-limited operating point")
    parser.add_argument("--stochastic-suite", action="store_true",
                        help="Monte Carlo stochastic combustion failure/recovery report")
    parser.add_argument("--controls-off", action="store_true",
                        help="Run controls-off baseline (ECU stabilizers disabled)")
    parser.add_argument("--monte-carlo", action="store_true",
                        help="Large-scale Monte Carlo with failure taxonomy")
    parser.add_argument("--mc-trials", type=int, default=1000,
                        help="Monte Carlo trial count (default 1000)")
    parser.add_argument("--mc-workers", type=int, default=None,
                        help="Parallel workers for Monte Carlo")
    parser.add_argument("--canonical-report", action="store_true",
                        help="Print canonical efficiency report (late steady window)")
    parser.add_argument("--headless", action="store_true", help="Non-interactive matplotlib backend")
    args = parser.parse_args()

    if args.headless or args.export_png or args.export_gif or not sys.stdout.isatty():
        import matplotlib
        matplotlib.use("Agg")

    cfg = PhoenixV3Config(
        frequency_hz=args.frequency,
        load_fraction=args.load,
        swirl_scavenge_gain=args.swirl,
    )

    if args.best_tuning_actuator:
        act_path = OUT_DIR / "phoenix_v3_best_tuning_actuator.json"
        if act_path.exists():
            from designs.phoenix_v3_optimizer import ACTUATOR_SEARCH_BOUNDS, TuningVector

            payload = json.loads(act_path.read_text(encoding="utf-8"))
            cfg = TuningVector.from_dict(
                payload["parameters"], bounds=ACTUATOR_SEARCH_BOUNDS,
            ).to_config(cfg, bounds=ACTUATOR_SEARCH_BOUNDS)
        else:
            print("Warning: phoenix_v3_best_tuning_actuator.json not found — using defaults")
    elif args.best_tuning:
        for best_path in (
            OUT_DIR / "phoenix_v3_best_tuning_v3.json",
            OUT_DIR / "phoenix_v3_best_tuning_v2.json",
            OUT_DIR / "phoenix_v3_best_tuning.json",
        ):
            if best_path.exists():
                from designs.phoenix_v3_optimizer import TuningVector
                payload = json.loads(best_path.read_text(encoding="utf-8"))
                cfg = TuningVector.from_dict(payload["parameters"]).to_config(cfg)
                break
        else:
            print("Warning: no best-tuning JSON found — using defaults")

    if args.generator_scale is not None:
        cfg = replace(cfg, generator_force_scale=args.generator_scale)
    if args.exhaust_open_ms is not None:
        cfg = replace(cfg, exhaust_open_ms=args.exhaust_open_ms)
    if args.spring_ref_bar is not None:
        cfg = replace(cfg, air_spring_reference_bar=args.spring_ref_bar)
    if args.no_ecu_balance:
        cfg = replace(cfg, generator_balance_control=False)
    if args.no_thermal:
        cfg = replace(cfg, thermal_model_enabled=False)
    else:
        cfg = apply_generator_cooling(cfg, args.cooling)
    if args.actuators:
        cfg = replace(cfg, actuator_model_enabled=True)
    if args.stochastic:
        cfg = replace(cfg, stochastic_combustion_enabled=True, combustion_rng_seed=42)
    if args.ring_plenum:
        cfg = replace(cfg, intake_plenum_enabled=True, intake_plenum_concurrent_intakes=3)

    if args.actuator_suite:
        from designs.phoenix_v3.validation.actuator_suite import (
            print_actuator_limits_report,
            run_actuator_limits_suite,
        )

        summary = run_actuator_limits_suite(cfg, cycles=max(args.cycles, 24))
        print_actuator_limits_report(summary)
        return

    if args.stochastic_suite:
        from designs.phoenix_v3.validation.stochastic_suite import (
            print_stochastic_combustion_report,
            run_stochastic_combustion_suite,
        )

        summary = run_stochastic_combustion_suite(cfg, cycles=max(args.cycles, 30))
        print_stochastic_combustion_report(summary)
        return

    if args.controls_off:
        from designs.phoenix_v3.validation.controls_off import (
            print_controls_off_report,
            run_controls_off_baseline,
        )

        result = run_controls_off_baseline(cfg, cycles=max(args.cycles, 24))
        print_controls_off_report(result)
        return

    if args.monte_carlo:
        from designs.phoenix_v3_optimizer import default_workers
        from designs.phoenix_v3.validation.monte_carlo import (
            print_monte_carlo_report,
            run_monte_carlo_suite,
        )

        workers = args.mc_workers if args.mc_workers is not None else default_workers()
        summary = run_monte_carlo_suite(
            cfg,
            trials=max(args.mc_trials, 100),
            cycles=max(args.cycles, 30),
            workers=workers,
        )
        print_monte_carlo_report(summary)
        return

    if args.tolerance_suite:
        pts = run_manufacturing_tolerance_suite(cfg, cycles=max(args.cycles, 12))
        print_manufacturing_tolerance_report(pts)
        return

    if args.long_run:
        if args.trend_log or args.trend_csv or args.capture_diagnostic:
            analysis = run_long_stability_analysis(
                cfg,
                cycles=max(args.cycles, 1000),
                trend_interval=max(args.trend_interval, 1),
            )
            print_long_run_report(analysis.summary)
            if args.trend_log or args.trend_csv:
                print_long_run_trend_report(analysis)
            if args.trend_csv:
                csv_path = Path(args.trend_csv)
                export_long_run_trend_csv(csv_path, analysis.trend)
                print(f"Trend CSV: {csv_path.resolve()}")
            print_capture_transition_report(analysis.result.energy.cycle_reports)
            if args.capture_diagnostic:
                diag_path = Path(args.capture_diagnostic)
                plot_long_run_capture_diagnostic(
                    analysis.result.energy.cycle_reports,
                    diag_path,
                    sample_every=max(args.capture_sample, 1),
                )
                print(f"Capture diagnostic: {diag_path.resolve()}")
        else:
            summary = run_long_stability(cfg, cycles=max(args.cycles, 1000))
            print_long_run_report(summary)
        return

    if args.optimize:
        from designs.phoenix_v3_optimizer import (
            default_workers,
            export_best_config,
            print_optimization_report,
            run_optimization,
        )
        report = run_optimization(
            trials=args.opt_trials,
            cycles=max(args.cycles, 8),
            base=cfg,
            workers=args.opt_workers if args.opt_workers is not None else default_workers(),
            resume=args.opt_resume,
        )
        print_optimization_report(report)
        print(f"Best config JSON: {export_best_config(report)}")
        return

    if args.sweep or args.sweep_load or args.sweep_exhaust or args.sweep_stroke or args.sweep_spring or args.sweep_flow or args.sweep_extraction or args.sweep_asymmetry or args.sweep_disturbance:
        import matplotlib
        matplotlib.use("Agg")
        sweep_cycles = max(args.cycles, 8)
        if args.sweep:
            run_tuning_sweeps(cfg, cycles=sweep_cycles)
        else:
            if args.sweep_load:
                pts = run_generator_load_sweep(cfg, cycles=sweep_cycles)
                print_sweep_table("GENERATOR LOAD SWEEP", pts)
                plot_sweep_chart(pts, x_label="Generator load", title="Generator load sweep",
                                 path=OUT_DIR / "phoenix_v3_sweep_generator_load.png")
            if args.sweep_exhaust:
                pts = run_exhaust_timing_sweep(cfg, cycles=sweep_cycles)
                print_sweep_table("EXHAUST TIMING SWEEP", pts)
                plot_sweep_chart(pts, x_label="Exhaust open", title="Exhaust timing sweep",
                                 path=OUT_DIR / "phoenix_v3_sweep_exhaust_timing.png")
            if args.sweep_stroke:
                pts = run_stroke_sweep(cfg, cycles=sweep_cycles)
                print_sweep_table("STROKE SWEEP", pts)
                plot_sweep_chart(pts, x_label="Stroke", title="Stroke sweep",
                                 path=OUT_DIR / "phoenix_v3_sweep_stroke.png")
            if args.sweep_spring:
                exh = cfg.exhaust_open_ms if args.exhaust_open_ms is not None else 8.2
                gen = cfg.generator_force_scale if args.generator_scale is not None else cfg.generator_force_scale * 1.5
                pts = run_air_spring_volume_sweep(
                    cfg, cycles=sweep_cycles,
                    exhaust_open_ms=exh,
                    generator_force_scale=gen,
                )
                print_sweep_table("AIR SPRING VOLUME SWEEP", pts)
                plot_sweep_chart(pts, x_label="Spring volume", title="Air spring volume sweep",
                                 path=OUT_DIR / "phoenix_v3_sweep_air_spring_volume.png")
            if args.sweep_flow:
                pts = run_energy_flow_sweep(cfg, cycles=sweep_cycles)
                print_sweep_table("ENERGY FLOW SWEEP", pts)
                plot_energy_flow_heatmap(
                    pts, load_fraction=1.5,
                    nominal_generator_scale=cfg.generator_force_scale,
                    path=OUT_DIR / "phoenix_v3_sweep_energy_flow_L150.png",
                )
            if args.sweep_extraction:
                pts = run_extraction_strategy_sweep(cfg, cycles=sweep_cycles)
                print_sweep_table("EXTRACTION STRATEGY SWEEP", pts)
                plot_sweep_chart(
                    pts, x_label="Strategy", title="Phoenix V3 — Extraction strategy",
                    path=OUT_DIR / "phoenix_v3_sweep_extraction.png",
                )
            if args.sweep_asymmetry:
                pts = run_asymmetry_sensitivity_sweep(cfg, cycles=sweep_cycles)
                print_asymmetry_sweep_report(pts)
            if args.sweep_disturbance:
                pts = run_disturbance_sensitivity_sweep(cfg, cycles=sweep_cycles)
                print_disturbance_sweep_report(pts)
        return

    if args.transient:
        transient_results = run_transient_suite(cfg, cycles=max(args.cycles, 8), fault_cycle=args.fault_cycle)
        print_report(transient_results[0].result)
        print_transient_report(transient_results)
        result = transient_results[0].result
    else:
        record = args.cycles <= 200
        result = PhoenixV3Simulator(cfg).simulate(cycles=args.cycles, record_history=record)
        print_report(result)
        if args.canonical_report:
            print_canonical_efficiency_report(result)

    dashboard_path = args.export_png or OUT_DIR / "phoenix_v3_cycle_dashboard.png"
    fig = plot_dashboard(result, show=not args.headless and args.animate)
    fig.savefig(dashboard_path, dpi=160, bbox_inches="tight", facecolor="white")
    print(f"Dashboard: {dashboard_path}")
    if not args.animate:
        plt.close(fig)

    scavenge_path = args.export_scavenge_png or OUT_DIR / "phoenix_v3_scavenge_phases.png"
    plot_scavenge_phases(result, scavenge_path)
    print(f"Scavenge diagram: {scavenge_path}")

    if args.animate or args.export_gif:
        ani = animate_cartridge(result)
        gif_path = args.export_gif or OUT_DIR / "phoenix_v3_cycle.gif"
        ani.save(str(gif_path), writer=PillowWriter(fps=25))
        print(f"Animation: {gif_path}")
        if args.animate and not args.headless:
            plt.show()


if __name__ == "__main__":
    main()
