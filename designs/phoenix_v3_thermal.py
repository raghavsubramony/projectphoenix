"""Lumped thermal model for Phoenix V3 single-cartridge simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

CoolingMode = Literal["passive", "water_jacket", "oil_loop", "cold_plate", "phase_change"]

COOLING_PRESETS: dict[str, dict[str, float]] = {
    "passive": {
        "generator_cooling_base_w": 0.0,
        "generator_cooling_w_per_k": 8.5,
        "phase_change_boost_w_per_k": 0.0,
    },
    "water_jacket": {
        "generator_cooling_base_w": 180.0,
        "generator_cooling_w_per_k": 42.0,
        "phase_change_boost_w_per_k": 0.0,
    },
    "oil_loop": {
        "generator_cooling_base_w": 90.0,
        "generator_cooling_w_per_k": 28.0,
        "phase_change_boost_w_per_k": 0.0,
    },
    "cold_plate": {
        "generator_cooling_base_w": 250.0,
        "generator_cooling_w_per_k": 65.0,
        "phase_change_boost_w_per_k": 0.0,
    },
    "phase_change": {
        "generator_cooling_base_w": 320.0,
        "generator_cooling_w_per_k": 55.0,
        "phase_change_boost_w_per_k": 120.0,
    },
}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class ThermalConfig:
    """Thermal parameters (mirrors PhoenixV3Config thermal fields)."""

    thermal_model_enabled: bool = True
    ambient_temp_k: float = 293.15
    wall_temp_k_initial: float = 380.0
    generator_temp_k_initial: float = 310.0
    valve_temp_k_initial: float = 350.0
    wall_temp_rated_k: float = 420.0
    generator_temp_rated_k: float = 380.0
    generator_temp_trip_k: float = 450.0
    generator_temp_max_k: float = 650.0
    wall_heat_transfer_w_per_k: float = 18.0
    wall_thermal_mass_j_per_k: float = 8500.0
    wall_combustion_heat_fraction: float = 0.08
    generator_coil_resistance_ohm: float = 0.42
    generator_coil_resistance_ref_k: float = 293.15
    generator_coil_temp_coeff: float = 0.0039
    generator_thermal_mass_j_per_k: float = 4500.0
    generator_cooling_mode: str = "passive"
    generator_cooling_base_w: float = 0.0
    generator_cooling_w_per_k: float = 8.5
    phase_change_temp_k: float = 358.0
    phase_change_boost_w_per_k: float = 0.0
    generator_core_loss_fraction: float = 0.015
    generator_base_efficiency: float = 0.94
    coolant_temp_k: float = 293.15
    shared_coolant_enabled: bool = False
    valve_thermal_mass_j_per_k: float = 45.0
    valve_heating_coeff_w_kg: float = 2.2e4
    valve_cooling_w_per_k: float = 3.5
    min_thermal_loss_fraction: float = 0.22


@dataclass
class ThermalState:
    """Lumped temperatures for chamber wall, linear generators, and valves."""

    wall_temp_k: float
    generator_temp_k: float
    valve_temp_k: float
    wall_heat_j: float = 0.0
    generator_heat_j: float = 0.0
    valve_heat_j: float = 0.0
    generator_derate: float = 1.0
    generator_efficiency: float = 0.94


@dataclass(frozen=True)
class ThermalStepResult:
    """Per-timestep thermal update."""

    state: ThermalState
    wall_loss_w: float
    generator_heat_loss_w: float
    generator_cooling_w: float
    valve_heating_w: float
    generator_derate: float
    generator_efficiency: float


def apply_cooling_preset(mode: str, cfg: ThermalConfig) -> ThermalConfig:
    """Return a config with cooling coefficients for the selected mode."""
    preset = COOLING_PRESETS.get(mode, COOLING_PRESETS["passive"])
    return ThermalConfig(
        **{
            **cfg.__dict__,
            "generator_cooling_mode": mode,
            "generator_cooling_base_w": preset["generator_cooling_base_w"],
            "generator_cooling_w_per_k": preset["generator_cooling_w_per_k"],
            "phase_change_boost_w_per_k": preset["phase_change_boost_w_per_k"],
        }
    )


def initial_thermal_state(cfg: ThermalConfig) -> ThermalState:
    eff = generator_effective_efficiency(cfg.generator_temp_k_initial, cfg)
    return ThermalState(
        wall_temp_k=cfg.wall_temp_k_initial,
        generator_temp_k=cfg.generator_temp_k_initial,
        valve_temp_k=cfg.valve_temp_k_initial,
        generator_derate=generator_magnet_derate(cfg.generator_temp_k_initial, cfg),
        generator_efficiency=eff,
    )


def coil_resistance_ohm(temp_k: float, cfg: ThermalConfig) -> float:
    delta = temp_k - cfg.generator_coil_resistance_ref_k
    return cfg.generator_coil_resistance_ohm * (1.0 + cfg.generator_coil_temp_coeff * delta)


def generator_magnet_derate(temp_k: float, cfg: ThermalConfig) -> float:
    """Permanent-magnet weakening above rated temperature."""
    t_rated = cfg.generator_temp_rated_k
    if temp_k <= t_rated:
        return 1.0
    span = max(cfg.generator_temp_trip_k - t_rated, 1.0)
    return _clamp(1.0 - 0.45 * (temp_k - t_rated) / span, 0.55, 1.0)


def generator_effective_efficiency(temp_k: float, cfg: ThermalConfig) -> float:
    """Temperature-dependent electrical efficiency (copper + magnet + core)."""
    magnet = generator_magnet_derate(temp_k, cfg)
    r_ratio = coil_resistance_ohm(temp_k, cfg) / max(
        coil_resistance_ohm(cfg.generator_coil_resistance_ref_k, cfg), 1e-9,
    )
    copper_penalty = _clamp(1.0 - 0.08 * (r_ratio - 1.0), 0.88, 1.0)
    core = 1.0 - cfg.generator_core_loss_fraction
    return _clamp(cfg.generator_base_efficiency * magnet * copper_penalty * core, 0.55, 0.96)


def generator_heat_loss_w(mech_power_w: float, cfg: ThermalConfig, eff: float) -> float:
    """Electrical + core losses dissipated as heat in the generator."""
    if mech_power_w <= 0.0:
        return 0.0
    elec = mech_power_w * eff
    core = mech_power_w * cfg.generator_core_loss_fraction
    return max(mech_power_w - elec + core, 0.0)


def generator_cooling_w(temp_k: float, cfg: ThermalConfig) -> float:
    """Active or passive heat rejection from the linear generator."""
    sink_k = cfg.coolant_temp_k if cfg.shared_coolant_enabled else cfg.ambient_temp_k
    rise = max(temp_k - sink_k, 0.0)
    cool = cfg.generator_cooling_base_w + cfg.generator_cooling_w_per_k * rise
    if cfg.generator_cooling_mode == "phase_change" and temp_k > cfg.phase_change_temp_k:
        cool += cfg.phase_change_boost_w_per_k * (temp_k - cfg.phase_change_temp_k)
    return cool


def valve_heating_w(
    mass_flow_kg_s: float,
    valve_temp_k: float,
    gas_temp_k: float,
    cfg: ThermalConfig,
) -> float:
    if mass_flow_kg_s <= 0.0:
        return 0.0
    delta_t = max(gas_temp_k - valve_temp_k, 0.0)
    return cfg.valve_heating_coeff_w_kg * mass_flow_kg_s * delta_t / max(cfg.ambient_temp_k, 1.0)


def wall_heat_loss_w(wall_temp_k: float, cfg: ThermalConfig) -> float:
    return cfg.wall_heat_transfer_w_per_k * (wall_temp_k - cfg.ambient_temp_k)


def update_thermal_state(
    state: ThermalState,
    *,
    cfg: ThermalConfig,
    dt: float,
    combustion_heat_w: float,
    mech_power_w: float,
    valve_mass_flow_kg_s: float,
    gas_temp_k: float,
    valves_open: bool,
) -> ThermalStepResult:
    """Advance lumped wall / generator / valve temperatures one timestep."""
    if not cfg.thermal_model_enabled:
        return ThermalStepResult(
            state=state,
            wall_loss_w=0.0,
            generator_heat_loss_w=0.0,
            generator_cooling_w=0.0,
            valve_heating_w=0.0,
            generator_derate=1.0,
            generator_efficiency=cfg.generator_base_efficiency,
        )

    wall_in = combustion_heat_w * cfg.wall_combustion_heat_fraction
    wall_out = wall_heat_loss_w(state.wall_temp_k, cfg)
    wall_net = wall_in - wall_out
    wall_temp = state.wall_temp_k + wall_net * dt / max(cfg.wall_thermal_mass_j_per_k, 1.0)

    eff = generator_effective_efficiency(state.generator_temp_k, cfg)
    heat_in = generator_heat_loss_w(mech_power_w, cfg, eff)
    cool = generator_cooling_w(state.generator_temp_k, cfg)
    gen_net = heat_in - cool
    gen_temp = state.generator_temp_k + gen_net * dt / max(cfg.generator_thermal_mass_j_per_k, 1.0)
    gen_temp = _clamp(gen_temp, cfg.ambient_temp_k, cfg.generator_temp_max_k)
    derate = generator_magnet_derate(gen_temp, cfg)
    eff_after = generator_effective_efficiency(gen_temp, cfg)

    valve_in = valve_heating_w(valve_mass_flow_kg_s, state.valve_temp_k, gas_temp_k, cfg) if valves_open else 0.0
    valve_cool = cfg.valve_cooling_w_per_k * (state.valve_temp_k - cfg.ambient_temp_k)
    valve_net = valve_in - valve_cool
    valve_temp = state.valve_temp_k + valve_net * dt / max(cfg.valve_thermal_mass_j_per_k, 1.0)

    new_state = ThermalState(
        wall_temp_k=max(cfg.ambient_temp_k, wall_temp),
        generator_temp_k=gen_temp,
        valve_temp_k=max(cfg.ambient_temp_k, valve_temp),
        wall_heat_j=state.wall_heat_j + wall_out * dt,
        generator_heat_j=state.generator_heat_j + heat_in * dt,
        valve_heat_j=state.valve_heat_j + valve_in * dt,
        generator_derate=derate,
        generator_efficiency=eff_after,
    )
    return ThermalStepResult(
        state=new_state,
        wall_loss_w=wall_out,
        generator_heat_loss_w=heat_in,
        generator_cooling_w=cool,
        valve_heating_w=valve_in,
        generator_derate=derate,
        generator_efficiency=eff_after,
    )


def live_wall_heat_fraction(cfg: ThermalConfig, wall_temp_k: float) -> float:
    if not cfg.thermal_model_enabled:
        return cfg.min_thermal_loss_fraction
    span = max(cfg.wall_temp_rated_k - cfg.ambient_temp_k, 1.0)
    rise = _clamp((wall_temp_k - cfg.ambient_temp_k) / span, 0.0, 1.5)
    return _clamp(cfg.min_thermal_loss_fraction + 0.06 * rise, 0.18, 0.38)


def thermal_sustainable(
    generator_temp_k: float,
    generator_derate: float,
    cfg: ThermalConfig,
    *,
    min_derate: float = 0.90,
) -> bool:
    """True when generator temperature and derate are within long-run limits."""
    return (
        generator_temp_k <= cfg.generator_temp_trip_k
        and generator_derate >= min_derate
    )


@dataclass
class CoolantBus:
    """Shared coolant loop temperature for multi-cartridge ring."""

    temp_k: float = 293.15


def advance_coolant_bus(
    bus: CoolantBus,
    *,
    ambient_temp_k: float,
    coolant_thermal_mass_j_per_k: float,
    coolant_radiator_w_per_k: float,
    heat_in_w: float,
    duration_s: float,
) -> CoolantBus:
    """Advance ring coolant temperature after a cartridge run."""
    cool_out = coolant_radiator_w_per_k * (bus.temp_k - ambient_temp_k)
    net = heat_in_w - cool_out
    temp = bus.temp_k + net * duration_s / max(coolant_thermal_mass_j_per_k, 1.0)
    return CoolantBus(temp_k=max(ambient_temp_k, temp))


def estimate_cartridge_heat_w(
    reports: tuple,
    cycle_time_s: float,
) -> float:
    """Mean generator heat rejection rate from per-cycle energy reports."""
    valid = [r for r in reports if getattr(r, "fuel_energy_j", 0.0) > 50.0]
    if not valid or cycle_time_s <= 0.0:
        return 0.0
    mech = sum(r.mech_energy_j for r in valid) / len(valid)
    eff = sum(r.generator_efficiency for r in valid) / len(valid)
    return mech * max(1.0 - eff, 0.0) / cycle_time_s
