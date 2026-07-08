"""Validation suites for actuator limits and stochastic combustion."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.efficiency import compute_canonical_efficiency


@dataclass(frozen=True)
class ActuatorLimitsPoint:
    label: str
    net_efficiency: float
    capture_fraction: float
    max_bdc_mm: float
    collision: bool
    stable: bool


@dataclass(frozen=True)
class ActuatorLimitsSummary:
    baseline: ActuatorLimitsPoint
    with_actuators: ActuatorLimitsPoint
    efficiency_delta_pp: float
    collapsed: bool


def _point(label: str, result) -> ActuatorLimitsPoint:
    canon = compute_canonical_efficiency(result)
    reports = [r for r in result.energy.cycle_reports if r.fuel_energy_j > 50.0]
    late = reports[-max(1, len(reports) // 5):] if reports else []
    collision = any(r.piston_collision for r in late) if late else False
    stable = (
        canon.net_fuel_to_electric >= 0.20
        and canon.mean_bdc_mm >= 10.0
        and not collision
        and result.energy.raw_boundary_valid
    )
    return ActuatorLimitsPoint(
        label=label,
        net_efficiency=canon.net_fuel_to_electric,
        capture_fraction=canon.capture_fraction,
        max_bdc_mm=canon.mean_bdc_mm,
        collision=collision,
        stable=stable,
    )


def run_actuator_limits_suite(
    base: PhoenixV3Config | None = None,
    *,
    cycles: int = 24,
) -> ActuatorLimitsSummary:
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    ref = base or PhoenixV3Config()
    baseline_result = PhoenixV3Simulator(ref).simulate(cycles=cycles, record_history=False)
    actuator_cfg = replace(
        ref,
        actuator_model_enabled=True,
        sensor_delay_s=0.0015,
        back_emf_coeff_n_s_m=38.0,
        max_generator_current_a=110.0,
        current_slew_a_per_s=180_000.0,
    )
    actuator_result = PhoenixV3Simulator(actuator_cfg).simulate(
        cycles=cycles, record_history=False,
    )
    baseline = _point("baseline (ideal actuators)", baseline_result)
    with_actuators = _point("with actuators", actuator_result)
    delta = (with_actuators.net_efficiency - baseline.net_efficiency) * 100.0
    collapsed = (
        with_actuators.net_efficiency < 0.15
        or with_actuators.collision
        or (
            not with_actuators.stable
            and with_actuators.net_efficiency < 0.45
        )
    )
    return ActuatorLimitsSummary(
        baseline=baseline,
        with_actuators=with_actuators,
        efficiency_delta_pp=delta,
        collapsed=collapsed,
    )


def print_actuator_limits_report(summary: ActuatorLimitsSummary) -> None:
    print()
    print("=" * 72)
    print("ACTUATOR LIMITS — sensor delay, current slew, back-EMF")
    print("=" * 72)
    for pt in (summary.baseline, summary.with_actuators):
        print(
            f"  {pt.label:28s}  Enet={pt.net_efficiency:5.1%}  "
            f"Cap={pt.capture_fraction:5.1%}  BDC={pt.max_bdc_mm:4.1f}mm  "
            f"{'COLL' if pt.collision else 'ok':>4}  "
            f"{'STABLE' if pt.stable else 'FAIL':>6}"
        )
    print(f"  Efficiency delta:          {summary.efficiency_delta_pp:+5.1f} pp")
    print(f"  Operating point collapsed: {'YES' if summary.collapsed else 'no'}")
    print("=" * 72)
    print()
