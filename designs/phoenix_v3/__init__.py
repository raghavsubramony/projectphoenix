"""Phoenix V3 simulation package — physics, controls, validation."""

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
from designs.phoenix_v3.efficiency import (
    CANONICAL_EFFICIENCY_DEFINITION,
    CanonicalEfficiencyReport,
    canonical_net_efficiency,
    compute_canonical_efficiency,
    late_cycle_reports,
    print_canonical_efficiency_report,
    steady_cycle_reports,
)

__all__ = [
    "CANONICAL_EFFICIENCY_DEFINITION",
    "CanonicalEfficiencyReport",
    "CycleEnergyReport",
    "CycleState",
    "EnergyMetrics",
    "PhoenixV3Config",
    "ScavengeMetrics",
    "SimulationResult",
    "TransientFault",
    "TransientResult",
    "ValveState",
    "canonical_net_efficiency",
    "compute_canonical_efficiency",
    "late_cycle_reports",
    "print_canonical_efficiency_report",
    "steady_cycle_reports",
]
