"""Configuration and result datatypes for Phoenix V3 simulation."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class ValveState:
    intake_a: bool
    intake_b: bool
    exhaust_a: bool
    exhaust_b: bool

    @property
    def any_intake(self) -> bool:
        return self.intake_a or self.intake_b

    @property
    def any_exhaust(self) -> bool:
        return self.exhaust_a or self.exhaust_b

    @property
    def open_count(self) -> int:
        return sum((self.intake_a, self.intake_b, self.exhaust_a, self.exhaust_b))

    @property
    def intake_port_count(self) -> int:
        return int(self.intake_a) + int(self.intake_b)


@dataclass(frozen=True)
class PhoenixV3Config:
    """V2 cartridge geometry + V3 breathing / control parameters."""

    # Geometry (per cartridge, V2 storyboard)
    bore_m: float = 0.096
    half_stroke_m: float = 0.025
    displacement_per_side_m3: float = 150e-6
    compression_ratio: float = 12.5
    max_pressure_bar: float = 200.0

    # Operating point
    frequency_hz: float = 100.0
    intake_ring_pressure_bar: float = 1.50
    exhaust_back_pressure_bar: float = 1.05

    # Mechanics
    piston_mass_kg: float = 1.15
    damping_n_s_m: float = 28.0
    generator_force_scale: float = 0.32
    generator_rated_force_n: float = 4200.0
    generator_spring_decouple: float = 0.20
    generator_adaptive_profile: bool = False
    generator_profile_tdc_fraction: float = 0.20
    generator_profile_peak_fraction: float = 1.0
    generator_efficiency: float = 0.94
    generator_force_scale_b: float | None = None
    generator_balance_control: bool = True
    generator_balance_gain: float = 0.85

    # Virtual end stops (ECU motion protection)
    virtual_end_stop_enabled: bool = True
    virtual_end_stop_start_fraction: float = 0.85
    virtual_end_stop_hard_fraction: float = 0.98
    spring_relief_at_end_stop: bool = True
    spring_relief_min_fraction: float = 0.55

    # Load spike limiter
    load_spike_limiter_enabled: bool = True
    load_spike_slew_per_s: float = 18.0
    max_generator_load_scale: float = 2.5
    travel_limit_restitution: float = 0.0
    collision_speed_threshold_ms: float = 12.0

    # Air springs
    air_spring_volume_max_m3: float = 55e-6
    air_spring_volume_min_m3: float = 25e-6
    air_spring_reference_bar: float = 22.0
    air_spring_control_gain: float = 1.0
    spring_balance_control: bool = True
    air_spring_expansion_gain: float = 0.72
    air_spring_compression_gain: float = 1.18
    air_spring_phase_control: bool = True
    air_spring_phase_slew_per_s: float = 0.0
    air_spring_recovery_cap: float = 0.95
    capture_startup_cycles: int = 3
    capture_startup_scale: float = 0.95
    capture_min_bdc_mm: float = 13.0
    generator_midstroke_min_profile: float = 0.35

    # Actuator / plant realism (opt-in — disabled preserves legacy behaviour)
    actuator_model_enabled: bool = False
    sensor_delay_s: float = 0.0015
    back_emf_coeff_n_s_m: float = 38.0
    generator_force_per_amp_n: float = 38.0
    max_generator_current_a: float = 110.0
    current_slew_a_per_s: float = 6000.0

    # Stochastic combustion (opt-in)
    stochastic_combustion_enabled: bool = False
    fuel_energy_jitter_frac: float = 0.15
    ignition_jitter_ms: float = 0.5
    partial_burn_probability: float = 0.10
    partial_burn_fraction: float = 0.40
    combustion_rng_seed: int | None = None

    # Shared intake plenum (ring coupling — opt-in)
    intake_plenum_enabled: bool = False
    intake_plenum_volume_m3: float = 0.015
    intake_plenum_loss_per_open_port: float = 0.006
    intake_plenum_concurrent_intakes: int = 1

    # Breathing
    valve_flow_coeff: float = 3.8e-4
    blowdown_flow_boost: float = 2.4
    swirl_scavenge_gain: float = 0.18
    staged_exhaust_lead_ms: float = 0.5

    # Combustion
    fuel_energy_j: float = 920.0
    injection_start_ms: float = 4.5
    ignition_ms: float = 4.9
    burn_duration_ms: float = 1.8
    load_fraction: float = 0.82
    combustion_pressure_gain: float = 1.48
    power_stroke_end_ms: float = 8.0
    exhaust_open_ms: float = 7.5
    min_thermal_loss_fraction: float = 0.22

    # Thermal model
    thermal_model_enabled: bool = True
    ambient_temp_k: float = 293.15
    wall_temp_k_initial: float = 380.0
    generator_temp_k_initial: float = 310.0
    valve_temp_k_initial: float = 350.0
    wall_temp_rated_k: float = 420.0
    generator_temp_rated_k: float = 380.0
    generator_temp_trip_k: float = 450.0
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
    generator_temp_max_k: float = 650.0
    shared_coolant_enabled: bool = False
    coolant_temp_k: float = 293.15
    coolant_thermal_mass_j_per_k: float = 85000.0
    coolant_radiator_w_per_k: float = 12.0
    valve_thermal_mass_j_per_k: float = 45.0
    valve_heating_coeff_w_kg: float = 2.2e4
    valve_cooling_w_per_k: float = 3.5

    # Manufacturing tolerances
    tolerance_generator_mismatch_frac: float = 0.0
    tolerance_valve_lag_ms: float = 0.0
    tolerance_spring_leak_frac: float = 0.0
    tolerance_pressure_sensor_frac: float = 0.0

    phase_offset_ms: float = 0.0

    target_residual_gas_fraction: float = 0.05
    target_scavenge_efficiency: float = 0.95
    target_pressure_drop_fraction: float = 0.03
    target_cylinder_variation: float = 0.02
    target_air_spring_recovery: float = 0.90
    target_generator_efficiency: float = 0.90
    target_net_cartridge_efficiency: float = 0.40

    @property
    def cycle_time_s(self) -> float:
        return 1.0 / self.frequency_hz

    @property
    def piston_area_m2(self) -> float:
        return math.pi * (0.5 * self.bore_m) ** 2

    @property
    def clearance_volume_m3(self) -> float:
        total_swept = 2.0 * self.displacement_per_side_m3
        return total_swept / (self.compression_ratio - 1.0)

    @property
    def intake_pressure_pa(self) -> float:
        return self.effective_intake_pressure_bar * 1e5

    @property
    def effective_intake_pressure_bar(self) -> float:
        if not self.intake_plenum_enabled or self.intake_plenum_concurrent_intakes <= 1:
            return self.intake_ring_pressure_bar
        from designs.phoenix_v3.ring_plenum import plenum_intake_pressure_bar

        return plenum_intake_pressure_bar(
            self.intake_ring_pressure_bar,
            self.intake_plenum_concurrent_intakes,
            self.intake_plenum_volume_m3,
            self.intake_plenum_loss_per_open_port,
        )

    @property
    def exhaust_pressure_pa(self) -> float:
        return self.exhaust_back_pressure_bar * 1e5


@dataclass
class CycleState:
    t_s: float
    x_a_m: float
    v_a_ms: float
    x_b_m: float
    v_b_ms: float
    pressure_pa: float
    temperature_k: float
    mass_kg: float
    residual_mass_kg: float
    fresh_mass_kg: float
    fuel_burned_frac: float
    valves: ValveState
    stage: str
    p_spring_a_pa: float
    p_spring_b_pa: float
    spring_energy_j: float = 0.0
    mech_power_w: float = 0.0
    elec_power_w: float = 0.0
    wall_temp_k: float = 0.0
    generator_temp_k: float = 0.0
    valve_temp_k: float = 0.0


@dataclass(frozen=True)
class CycleEnergyReport:
    cycle_index: int
    spring_p_min_bar: float
    spring_p_max_bar: float
    spring_pressure_ratio: float
    spring_energy_stored_j: float
    spring_energy_recovered_j: float
    spring_damping_loss_j: float
    spring_recovery_efficiency: float
    combustion_work_j: float
    indicated_work_raw_j: float
    power_stroke_expansion_j: float
    gross_expansion_work_j: float
    pumping_loss_j: float
    heat_residual_j: float
    unharvested_work_j: float
    mech_energy_j: float
    elec_energy_j: float
    generator_efficiency: float
    fuel_energy_j: float
    net_cartridge_efficiency: float
    capture_fraction: float
    energy_balance_valid: bool
    min_piston_clearance_mm: float
    max_piston_travel_mm: float
    piston_collision: bool
    mech_energy_raw_j: float = 0.0
    mech_from_gas_j: float = 0.0
    mech_from_storage_j: float = 0.0
    spring_hysteresis_j: float = 0.0
    spring_state_delta_j: float = 0.0
    kinetic_state_delta_j: float = 0.0
    energy_state_valid: bool = True
    hierarchy_valid: bool = True
    raw_boundary_valid: bool = True
    power_spring_work_j: float = 0.0
    power_ke_delta_j: float = 0.0
    power_damping_j: float = 0.0
    power_unharvested_j: float = 0.0
    power_closure_valid: bool = True
    wall_temp_k: float = 0.0
    generator_temp_k: float = 0.0
    valve_temp_k: float = 0.0
    generator_derate: float = 1.0
    mean_generator_force_n: float = 0.0
    raw_spring_recovery: float = 0.0


@dataclass
class EnergyMetrics:
    air_spring_recovery: float
    air_spring_stored_j: float
    air_spring_recovered_j: float
    generator_efficiency: float
    mech_energy_j: float
    elec_energy_j: float
    net_cartridge_efficiency: float
    fuel_energy_j: float
    combustion_work_j: float
    unharvested_work_j: float
    heat_residual_j: float
    energy_balance_valid: bool
    cycle_reports: tuple[CycleEnergyReport, ...]
    hierarchy_valid: bool = True
    raw_boundary_valid: bool = True


@dataclass(frozen=True)
class TransientFault:
    kind: str = "none"
    trigger_cycle: int = 3
    trigger_time_s: float | None = None


@dataclass
class ScavengeMetrics:
    residual_gas_fraction: float
    scavenge_efficiency: float
    pressure_drop_fraction: float
    cylinder_variation: float
    post_scavenge_temp_uniformity: float
    peak_pressure_bar: float


@dataclass
class SimulationResult:
    cfg: PhoenixV3Config
    history: list[CycleState]
    metrics: ScavengeMetrics
    energy: EnergyMetrics
    time_s: np.ndarray
    stage_labels: list[str]
    fault: TransientFault | None = None


@dataclass(frozen=True)
class TransientResult:
    fault: TransientFault
    result: SimulationResult
    collision_cycle: int | None
    recovered: bool
    recovery_cycles: int | None
    notes: str
