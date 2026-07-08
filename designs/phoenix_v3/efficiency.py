"""Canonical efficiency definition — single source of truth for reporting."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from designs.phoenix_v3.config import CycleEnergyReport, PhoenixV3Config, SimulationResult

CANONICAL_EFFICIENCY_DEFINITION = (
    "late_steady_window_mean: mean(Welec/Efuel) over the last 10% of steady "
    "cycles (fuel > 50 J, BDC >= 85% capture_min, cycle_index >= capture_startup)"
)


def steady_cycle_reports(
    reports: tuple[CycleEnergyReport, ...],
    cfg: PhoenixV3Config,
) -> tuple[CycleEnergyReport, ...]:
    steady = [
        c for c in reports
        if c.fuel_energy_j > 50.0
        and c.max_piston_travel_mm >= cfg.capture_min_bdc_mm * 0.85
        and c.cycle_index >= cfg.capture_startup_cycles
    ]
    return tuple(steady)


def late_cycle_reports(
    reports: tuple[CycleEnergyReport, ...],
    cfg: PhoenixV3Config,
) -> tuple[CycleEnergyReport, ...]:
    steady = steady_cycle_reports(reports, cfg)
    if not steady:
        return ()
    window = max(1, len(steady) // 10)
    return steady[-window:]


@dataclass(frozen=True)
class CanonicalEfficiencyReport:
    definition: str
    window_cycles: int
    steady_cycles: int
    net_fuel_to_electric: float
    capture_fraction: float
    mean_elec_energy_j: float
    mean_fuel_energy_j: float
    mean_bdc_mm: float
    energy_balance_valid: bool
    raw_boundary_valid: bool


def compute_canonical_efficiency(result: SimulationResult) -> CanonicalEfficiencyReport:
    """Single canonical net efficiency — use everywhere (audit, JSON, optimizer)."""
    cfg = result.cfg
    reports = result.energy.cycle_reports
    steady = steady_cycle_reports(reports, cfg)
    late = late_cycle_reports(reports, cfg)
    window = late if late else steady
    if not window:
        return CanonicalEfficiencyReport(
            definition=CANONICAL_EFFICIENCY_DEFINITION,
            window_cycles=0,
            steady_cycles=len(steady),
            net_fuel_to_electric=0.0,
            capture_fraction=0.0,
            mean_elec_energy_j=0.0,
            mean_fuel_energy_j=0.0,
            mean_bdc_mm=0.0,
            energy_balance_valid=result.energy.energy_balance_valid,
            raw_boundary_valid=result.energy.raw_boundary_valid,
        )

    net_eff = float(np.mean([r.net_cartridge_efficiency for r in window]))
    capture = float(np.mean([r.capture_fraction for r in window]))
    elec = float(np.mean([r.elec_energy_j for r in window]))
    fuel = float(np.mean([r.fuel_energy_j for r in window]))
    bdc = float(np.mean([r.max_piston_travel_mm for r in window]))
    return CanonicalEfficiencyReport(
        definition=CANONICAL_EFFICIENCY_DEFINITION,
        window_cycles=len(window),
        steady_cycles=len(steady),
        net_fuel_to_electric=net_eff,
        capture_fraction=capture,
        mean_elec_energy_j=elec,
        mean_fuel_energy_j=fuel,
        mean_bdc_mm=bdc,
        energy_balance_valid=all(r.hierarchy_valid for r in window),
        raw_boundary_valid=all(r.raw_boundary_valid for r in window),
    )


def canonical_net_efficiency(result: SimulationResult) -> float:
    return compute_canonical_efficiency(result).net_fuel_to_electric


def print_canonical_efficiency_report(result: SimulationResult) -> None:
    report = compute_canonical_efficiency(result)
    print()
    print("=" * 72)
    print("CANONICAL EFFICIENCY REPORT")
    print("=" * 72)
    print(f"  Definition:   {report.definition}")
    print(f"  Window:       {report.window_cycles} cycles (of {report.steady_cycles} steady)")
    print(f"  Net Enet:     {report.net_fuel_to_electric:.1%}  (Welec / Efuel, late steady)")
    print(f"  Capture:      {report.capture_fraction:.1%}")
    print(f"  Mean Welec:   {report.mean_elec_energy_j:.1f} J/cycle")
    print(f"  Mean Efuel:   {report.mean_fuel_energy_j:.1f} J/cycle")
    print(f"  Mean BDC:     {report.mean_bdc_mm:.1f} mm")
    print(f"  Energy bal:   {'PASS' if report.energy_balance_valid else 'FAIL'}")
    print(f"  Work bound:   {'PASS' if report.raw_boundary_valid else 'WARN'}")
    print("=" * 72)
    print()
