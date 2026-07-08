"""Monte Carlo stochastic combustion validation."""

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
class StochasticRunOutcome:
    seed: int
    net_efficiency: float
    capture_fraction: float
    max_bdc_mm: float
    collision: bool
    recovered: bool
    failure: bool


@dataclass(frozen=True)
class StochasticCombustionSummary:
    trials: int
    failure_rate: float
    collision_rate: float
    mean_net_efficiency: float
    p10_net_efficiency: float
    p90_net_efficiency: float
    mean_recovery_cycles: float
    outcomes: tuple[StochasticRunOutcome, ...]


def _assess_recovery(result, baseline_eff: float) -> tuple[bool, bool]:
    reports = [r for r in result.energy.cycle_reports if r.fuel_energy_j > 50.0]
    if not reports:
        return False, True
    collision = any(r.piston_collision for r in reports)
    late_eff = compute_canonical_efficiency(result).net_fuel_to_electric
    recovered = late_eff >= max(0.20, baseline_eff * 0.75)
    failure = collision or late_eff < 0.15 or not result.energy.raw_boundary_valid
    return recovered, failure


def run_stochastic_combustion_suite(
    base: PhoenixV3Config | None = None,
    *,
    trials: int = 20,
    cycles: int = 30,
    base_seed: int = 1000,
) -> StochasticCombustionSummary:
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    ref = base or PhoenixV3Config()
    baseline = PhoenixV3Simulator(ref).simulate(cycles=cycles, record_history=False)
    baseline_eff = compute_canonical_efficiency(baseline).net_fuel_to_electric

    stochastic_cfg = replace(
        ref,
        stochastic_combustion_enabled=True,
        fuel_energy_jitter_frac=0.15,
        ignition_jitter_ms=0.5,
        partial_burn_probability=0.10,
        partial_burn_fraction=0.40,
    )

    outcomes: list[StochasticRunOutcome] = []
    recovery_hits = 0
    for i in range(trials):
        seed = base_seed + i
        cfg = replace(stochastic_cfg, combustion_rng_seed=seed)
        result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
        canon = compute_canonical_efficiency(result)
        reports = [r for r in result.energy.cycle_reports if r.fuel_energy_j > 50.0]
        collision = any(r.piston_collision for r in reports) if reports else False
        recovered, failure = _assess_recovery(result, baseline_eff)
        if recovered:
            recovery_hits += 1
        outcomes.append(StochasticRunOutcome(
            seed=seed,
            net_efficiency=canon.net_fuel_to_electric,
            capture_fraction=canon.capture_fraction,
            max_bdc_mm=canon.mean_bdc_mm,
            collision=collision,
            recovered=recovered,
            failure=failure,
        ))

    effs = np.array([o.net_efficiency for o in outcomes])
    failures = sum(1 for o in outcomes if o.failure)
    collisions = sum(1 for o in outcomes if o.collision)
    return StochasticCombustionSummary(
        trials=trials,
        failure_rate=failures / max(trials, 1),
        collision_rate=collisions / max(trials, 1),
        mean_net_efficiency=float(np.mean(effs)),
        p10_net_efficiency=float(np.percentile(effs, 10)),
        p90_net_efficiency=float(np.percentile(effs, 90)),
        mean_recovery_cycles=float(recovery_hits) / max(trials, 1),
        outcomes=tuple(outcomes),
    )


def print_stochastic_combustion_report(summary: StochasticCombustionSummary) -> None:
    print()
    print("=" * 72)
    print(f"STOCHASTIC COMBUSTION — {summary.trials} Monte Carlo trials")
    print("=" * 72)
    print(f"  Failure rate:        {summary.failure_rate:6.1%}")
    print(f"  Collision rate:      {summary.collision_rate:6.1%}")
    print(f"  Mean Enet:           {summary.mean_net_efficiency:6.1%}")
    print(f"  P10 / P90 Enet:      {summary.p10_net_efficiency:6.1%} / {summary.p90_net_efficiency:.1%}")
    print(f"  Recovery rate:       {summary.mean_recovery_cycles:6.1%}")
    print()
    print(f"  {'Seed':>6}  {'Enet':>6}  {'Cap':>6}  {'BDC':>5}  {'Rec':>4}  {'Fail':>4}")
    for o in summary.outcomes[:12]:
        print(
            f"  {o.seed:6d}  {o.net_efficiency:5.1%}  {o.capture_fraction:5.1%}  "
            f"{o.max_bdc_mm:4.1f}  {'OK' if o.recovered else '--':>4}  "
            f"{'YES' if o.failure else 'no':>4}"
        )
    if len(summary.outcomes) > 12:
        print(f"  ... {len(summary.outcomes) - 12} more trials")
    print("=" * 72)
    print()
