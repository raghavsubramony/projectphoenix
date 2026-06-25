"""Digital twin of the integrated ATPE + PCMRITMS series-hybrid powertrain.

Pure standard-library implementation (no third-party dependencies).

Public API:
    build_default_twin()  -> Powertrain  (Phase-1 MUV/SUV configuration)
    build_performance_twin() -> Powertrain (Phase-2 performance configuration)
    run(twin, cycle)      -> Result
    DriveCycles           -> synthetic drive-cycle factory
"""

from typing import Callable

from .config import (
    TierSpec,
    ATPEConfig,
    BufferConfig,
    BatteryConfig,
    BatteryThermalConfig,
    VehicleConfig,
    ControlConfig,
    TractionConfig,
    TwinConfig,
    BodyStyle,
    PHASE1_BODIES,
    phase1_config,
    phase1_config_for,
    phase1_variants,
    with_battery_thermal,
    phase2_config,
)
from .powertrain import Powertrain
from .controller import UnifiedController, ClosedLoopRotorController, ControlState
from .drive_cycles import DriveCycles, DriveCycle
from .simulation import run, Result, StepRecord
from .pcmritms_rotor import (
    RotorSet,
    TorqueModulationResult,
    simulate_torque_augmentation,
)
from .pcmritms_coupling import (
    couple_buffer,
    rotor_peak_reaction_torque_nm,
    rotor_transient_power_w,
)
from .fleet import (
    FleetCell,
    standard_cycles,
    charge_sustaining_bodies,
    stress_bodies,
    closed_loop_rotor_bodies,
    stress_unmet_launch_kj,
    run_fleet,
    fleet_table,
    fleet_report,
    fleet_delta,
)
from .acceptance import (
    ERSTargets,
    Check,
    evaluate,
    report,
    report_bodies,
    compare_bodies,
    recommend_motor,
    recommend_motors,
    MotorRecommendation,
    SweepPoint,
    sweep_motor,
    sweep_grid,
    sweep_body_detail,
    phase1_targets,
    phase1_targets_for,
    phase1_body_targets,
)
from .validation import (
    Parameter,
    Elasticity,
    phase1_sensitivity_params,
    elasticity,
    sensitivity_table,
    sensitivity_report,
    capability_sweep,
    capability_report,
)
from .economics import (
    EconomicsConfig,
    TcoResult,
    DEFAULT_USAGE_MIX,
    tco_for_body,
    fleet_tco,
    tco_table,
)
from .sizing import (
    SizingConfig,
    SizingPoint,
    BodySizing,
    size_battery_power,
    fleet_battery_sizing,
    sizing_table,
)
from .montecarlo import (
    Distribution,
    TcoBands,
    DEFAULT_PARAM_SIGMA,
    DEFAULT_ECON_SIGMA,
    monte_carlo_fuel,
    monte_carlo_tco,
    fleet_uncertainty,
    uncertainty_table,
)
from .ambient import (
    AmbientConfig,
    AmbientPoint,
    AmbientSweep,
    DEFAULT_TEMPS_C,
    air_density_factor,
    hvac_load_w,
    simulate_at_temp,
    ambient_sweep,
    fleet_ambient,
    ambient_table,
)
from .regulatory_cycles import (
    CycleStats,
    CycleSpec,
    RegulatoryCycle,
    RegulatoryCycles,
    RegulatoryEconomy,
    cycle_stats,
    regulatory_fidelity_table,
    regulatory_economy,
    fleet_regulatory,
    regulatory_economy_table,
)
from .summary import (
    BodySummary,
    ExecutiveSummary,
    build_executive_summary,
)
from .coldstart import (
    ColdStartConfig,
    ColdStartResult,
    cold_start_for,
    fleet_cold_start,
    cold_start_table,
)
from .payload import (
    LoadConfig,
    LoadPoint,
    LoadSweep,
    OCCUPANT_KG,
    payload_sweep,
    fleet_payload,
    payload_table,
)
from .phev import (
    GridConfig,
    PhevResult,
    phev_for_body,
    fleet_phev,
    phev_table,
)
from .degradation import (
    DegradationConfig,
    DegradationPoint,
    DegradationCurve,
    degradation_for,
    fleet_degradation,
    degradation_table,
)

__all__ = [
    "TierSpec",
    "ATPEConfig",
    "BufferConfig",
    "BatteryConfig",
    "BatteryThermalConfig",
    "VehicleConfig",
    "ControlConfig",
    "TractionConfig",
    "TwinConfig",
    "BodyStyle",
    "PHASE1_BODIES",
    "phase1_config",
    "phase1_config_for",
    "phase1_variants",
    "with_battery_thermal",
    "phase2_config",
    "Powertrain",
    "UnifiedController",
    "ClosedLoopRotorController",
    "ControlState",
    "DriveCycles",
    "DriveCycle",
    "run",
    "Result",
    "StepRecord",
    "RotorSet",
    "TorqueModulationResult",
    "simulate_torque_augmentation",
    "couple_buffer",
    "rotor_peak_reaction_torque_nm",
    "rotor_transient_power_w",
    "FleetCell",
    "standard_cycles",
    "charge_sustaining_bodies",
    "stress_bodies",
    "closed_loop_rotor_bodies",
    "stress_unmet_launch_kj",
    "run_fleet",
    "fleet_table",
    "fleet_report",
    "fleet_delta",
    "ERSTargets",
    "Check",
    "evaluate",
    "report",
    "report_bodies",
    "compare_bodies",
    "recommend_motor",
    "recommend_motors",
    "MotorRecommendation",
    "SweepPoint",
    "sweep_motor",
    "sweep_grid",
    "sweep_body_detail",
    "phase1_targets",
    "phase1_targets_for",
    "phase1_body_targets",
    "Parameter",
    "Elasticity",
    "phase1_sensitivity_params",
    "elasticity",
    "sensitivity_table",
    "sensitivity_report",
    "capability_sweep",
    "capability_report",
    "EconomicsConfig",
    "TcoResult",
    "DEFAULT_USAGE_MIX",
    "tco_for_body",
    "fleet_tco",
    "tco_table",
    "SizingConfig",
    "SizingPoint",
    "BodySizing",
    "size_battery_power",
    "fleet_battery_sizing",
    "sizing_table",
    "Distribution",
    "TcoBands",
    "DEFAULT_PARAM_SIGMA",
    "DEFAULT_ECON_SIGMA",
    "monte_carlo_fuel",
    "monte_carlo_tco",
    "fleet_uncertainty",
    "uncertainty_table",
    "AmbientConfig",
    "AmbientPoint",
    "AmbientSweep",
    "DEFAULT_TEMPS_C",
    "air_density_factor",
    "hvac_load_w",
    "simulate_at_temp",
    "ambient_sweep",
    "fleet_ambient",
    "ambient_table",
    "CycleStats",
    "CycleSpec",
    "RegulatoryCycle",
    "RegulatoryCycles",
    "RegulatoryEconomy",
    "cycle_stats",
    "regulatory_fidelity_table",
    "regulatory_economy",
    "fleet_regulatory",
    "regulatory_economy_table",
    "BodySummary",
    "ExecutiveSummary",
    "build_executive_summary",
    "ColdStartConfig",
    "ColdStartResult",
    "cold_start_for",
    "fleet_cold_start",
    "cold_start_table",
    "LoadConfig",
    "LoadPoint",
    "LoadSweep",
    "OCCUPANT_KG",
    "payload_sweep",
    "fleet_payload",
    "payload_table",
    "GridConfig",
    "PhevResult",
    "phev_for_body",
    "fleet_phev",
    "phev_table",
    "DegradationConfig",
    "DegradationPoint",
    "DegradationCurve",
    "degradation_for",
    "fleet_degradation",
    "degradation_table",
    "build_default_twin",
    "build_performance_twin",
    "build_body_twins",
]


def build_default_twin(rotor_coupled: bool = False) -> Powertrain:
    """Return a Powertrain configured for the Phase-1 petrol MUV/SUV."""
    return Powertrain(phase1_config(rotor_coupled=rotor_coupled))


def build_performance_twin() -> Powertrain:
    """Return a Powertrain configured for the Phase-2 performance vehicle."""
    return Powertrain(phase2_config())


def build_body_twins(
    rotor_coupled: bool = False,
) -> dict[str, Callable[[], Powertrain]]:
    """Return per-body Powertrain *builders* (callables) for every Phase-1 body.

    Each value is a zero-arg builder so capability sims can spin up fresh,
    independent twins on demand (their internal energy state is consumed).
    When `rotor_coupled` is True, every body's inertial buffer gains the
    PCMRITMS rotor-derived brief-burst discharge rating.
    """
    return {
        name: (lambda c=cfg: Powertrain(c))
        for name, cfg in phase1_variants(rotor_coupled=rotor_coupled).items()
    }
