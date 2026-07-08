"""Controls-off baseline — does the plant run without ECU assistance?"""

from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.efficiency import compute_canonical_efficiency


def apply_controls_off(cfg: PhoenixV3Config) -> PhoenixV3Config:
    """Strip software-defined stabilizers — physics + fixed generator load only."""
    return replace(
        cfg,
        generator_balance_control=False,
        generator_adaptive_profile=False,
        virtual_end_stop_enabled=False,
        spring_relief_at_end_stop=False,
        capture_startup_scale=1.0,
        capture_startup_cycles=0,
        air_spring_phase_control=False,
        spring_balance_control=False,
        load_spike_limiter_enabled=False,
    )


@dataclass(frozen=True)
class ControlsOffResult:
    runs: bool
    net_efficiency: float
    capture_fraction: float
    mean_bdc_mm: float
    collision: bool
    energy_balance_valid: bool
    raw_boundary_valid: bool
    peak_pressure_bar: float
    asymmetry: float
    notes: str


def run_controls_off_baseline(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 24,
) -> ControlsOffResult:
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    ref = base or PhoenixV3Config()
    cfg = apply_controls_off(ref)
    result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
    canon = compute_canonical_efficiency(result)
    reports = [r for r in result.energy.cycle_reports if r.fuel_energy_j > 50.0]
    late = reports[-max(1, len(reports) // 5):] if reports else []
    collision = any(r.piston_collision for r in late) if late else False

    runs = (
        canon.net_fuel_to_electric >= 0.15
        and canon.mean_bdc_mm >= 8.0
        and not collision
        and result.energy.energy_balance_valid
    )
    if not reports:
        notes = "No valid fuel cycles — machine did not sustain combustion."
    elif collision:
        notes = "Piston collision with ECU disabled — plant unstable without software."
    elif canon.net_fuel_to_electric < 0.15:
        notes = f"Efficiency collapsed to {canon.net_fuel_to_electric:.1%} without ECU."
    elif canon.mean_bdc_mm < 8.0:
        notes = f"Stroke collapsed to {canon.mean_bdc_mm:.1f} mm BDC without ECU."
    elif not runs:
        notes = "Marginal operation — fails stability band without ECU."
    else:
        notes = (
            f"Self-sustaining at {canon.net_fuel_to_electric:.1%} Enet without ECU "
            f"(capture {canon.capture_fraction:.1%})."
        )

    return ControlsOffResult(
        runs=runs,
        net_efficiency=canon.net_fuel_to_electric,
        capture_fraction=canon.capture_fraction,
        mean_bdc_mm=canon.mean_bdc_mm,
        collision=collision,
        energy_balance_valid=result.energy.energy_balance_valid,
        raw_boundary_valid=result.energy.raw_boundary_valid,
        peak_pressure_bar=result.metrics.peak_pressure_bar,
        asymmetry=result.metrics.cylinder_variation,
        notes=notes,
    )


def print_controls_off_report(result: ControlsOffResult) -> None:
    print()
    print("=" * 72)
    print("CONTROLS-OFF BASELINE — ECU stabilizers disabled")
    print("=" * 72)
    print("  Disabled: balance trim, adaptive profile, virtual end stops,")
    print("            spring relief, capture startup ramp, spring phase control,")
    print("            load spike limiter, closed-loop spring balance")
    print()
    print(f"  Self-sustaining:     {'YES' if result.runs else 'NO'}")
    print(f"  Net Enet:            {result.net_efficiency:6.1%}")
    print(f"  Capture:             {result.capture_fraction:6.1%}")
    print(f"  Mean BDC:            {result.mean_bdc_mm:6.1f} mm")
    print(f"  Peak pressure:       {result.peak_pressure_bar:6.0f} bar")
    print(f"  Asymmetry:           {result.asymmetry:6.2%}")
    print(f"  Collision:           {'YES' if result.collision else 'no'}")
    print(f"  Energy balance:      {'PASS' if result.energy_balance_valid else 'FAIL'}")
    print(f"  Work boundary:       {'PASS' if result.raw_boundary_valid else 'WARN'}")
    print()
    print(f"  Verdict: {result.notes}")
    print("=" * 72)
    print()
