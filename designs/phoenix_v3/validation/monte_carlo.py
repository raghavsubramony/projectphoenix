"""Large-scale Monte Carlo with failure taxonomy."""

from __future__ import annotations

import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.efficiency import compute_canonical_efficiency


class FailureKind(str, Enum):
    OK = "ok"
    COLLISION = "collision"
    STROKE_COLLAPSE = "stroke_collapse"
    CAPTURE_LOSS = "capture_loss"
    EFFICIENCY_CLIFF = "efficiency_cliff"
    BOUNDARY_FAIL = "boundary_fail"
    NO_VALID_CYCLES = "no_valid_cycles"


@dataclass(frozen=True)
class MonteCarloOutcome:
    seed: int
    net_efficiency: float
    capture_fraction: float
    mean_bdc_mm: float
    failure: FailureKind
    efficiency_delta_pp: float


@dataclass(frozen=True)
class MonteCarloSummary:
    trials: int
    baseline_efficiency: float
    baseline_capture: float
    failure_rate: float
    collision_rate: float
    stroke_collapse_rate: float
    capture_loss_rate: float
    efficiency_cliff_rate: float
    boundary_fail_rate: float
    ok_rate: float
    mean_net_efficiency: float
    p01_net_efficiency: float
    p10_net_efficiency: float
    p50_net_efficiency: float
    p90_net_efficiency: float
    p99_net_efficiency: float
    outcomes: tuple[MonteCarloOutcome, ...]


def classify_failure(
    result,
    *,
    baseline_eff: float,
    baseline_capture: float,
    stroke_min_mm: float = 10.0,
    capture_min: float = 0.25,
    efficiency_cliff_pp: float = 10.0,
    absolute_eff_floor: float = 0.30,
) -> FailureKind:
    reports = [r for r in result.energy.cycle_reports if r.fuel_energy_j > 50.0]
    if not reports:
        return FailureKind.NO_VALID_CYCLES
    if any(r.piston_collision for r in reports):
        return FailureKind.COLLISION
    if not result.energy.raw_boundary_valid:
        return FailureKind.BOUNDARY_FAIL

    canon = compute_canonical_efficiency(result)
    if canon.mean_bdc_mm < stroke_min_mm:
        return FailureKind.STROKE_COLLAPSE
    if canon.capture_fraction < capture_min:
        return FailureKind.CAPTURE_LOSS
    cliff = max(absolute_eff_floor, baseline_eff - efficiency_cliff_pp / 100.0)
    if canon.net_fuel_to_electric < cliff:
        return FailureKind.EFFICIENCY_CLIFF
    return FailureKind.OK


def _mc_worker(args: tuple[int, int, dict, float, float]) -> MonteCarloOutcome:
    seed, cycles, cfg_kwargs, baseline_eff, baseline_capture = args
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    cfg = PhoenixV3Config(**cfg_kwargs)
    cfg = replace(
        cfg,
        stochastic_combustion_enabled=True,
        combustion_rng_seed=seed,
    )
    result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
    canon = compute_canonical_efficiency(result)
    failure = classify_failure(
        result,
        baseline_eff=baseline_eff,
        baseline_capture=baseline_capture,
    )
    return MonteCarloOutcome(
        seed=seed,
        net_efficiency=canon.net_fuel_to_electric,
        capture_fraction=canon.capture_fraction,
        mean_bdc_mm=canon.mean_bdc_mm,
        failure=failure,
        efficiency_delta_pp=(canon.net_fuel_to_electric - baseline_eff) * 100.0,
    )


def _config_kwargs(cfg: PhoenixV3Config) -> dict:
    from dataclasses import asdict

    d = asdict(cfg)
    d.pop("_baseline_eff", None)
    d.pop("_baseline_capture", None)
    return d


def run_monte_carlo_suite(
    base: PhoenixV3Config | None = None,
    *,
    trials: int = 1000,
    cycles: int = 30,
    base_seed: int = 5000,
    workers: int = 1,
) -> MonteCarloSummary:
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    ref = base or PhoenixV3Config()
    stochastic_cfg = replace(
        ref,
        stochastic_combustion_enabled=True,
        fuel_energy_jitter_frac=0.15,
        ignition_jitter_ms=0.5,
        partial_burn_probability=0.10,
        partial_burn_fraction=0.40,
    )
    baseline = PhoenixV3Simulator(ref).simulate(cycles=cycles, record_history=False)
    baseline_canon = compute_canonical_efficiency(baseline)
    baseline_eff = baseline_canon.net_fuel_to_electric
    baseline_capture = baseline_canon.capture_fraction

    kw = _config_kwargs(stochastic_cfg)

    outcomes: list[MonteCarloOutcome] = []
    jobs = [(base_seed + i, cycles, kw, baseline_eff, baseline_capture) for i in range(trials)]

    if workers <= 1:
        for job in jobs:
            outcomes.append(_mc_worker(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_mc_worker, job) for job in jobs]
            for fut in as_completed(futures):
                outcomes.append(fut.result())

    outcomes.sort(key=lambda o: o.seed)
    effs = np.array([o.net_efficiency for o in outcomes])
    n = max(len(outcomes), 1)

    def rate(kind: FailureKind) -> float:
        return sum(1 for o in outcomes if o.failure == kind) / n

    return MonteCarloSummary(
        trials=len(outcomes),
        baseline_efficiency=baseline_eff,
        baseline_capture=baseline_capture,
        failure_rate=1.0 - rate(FailureKind.OK),
        collision_rate=rate(FailureKind.COLLISION),
        stroke_collapse_rate=rate(FailureKind.STROKE_COLLAPSE),
        capture_loss_rate=rate(FailureKind.CAPTURE_LOSS),
        efficiency_cliff_rate=rate(FailureKind.EFFICIENCY_CLIFF),
        boundary_fail_rate=rate(FailureKind.BOUNDARY_FAIL),
        ok_rate=rate(FailureKind.OK),
        mean_net_efficiency=float(np.mean(effs)) if len(effs) else 0.0,
        p01_net_efficiency=float(np.percentile(effs, 1)) if len(effs) else 0.0,
        p10_net_efficiency=float(np.percentile(effs, 10)) if len(effs) else 0.0,
        p50_net_efficiency=float(np.percentile(effs, 50)) if len(effs) else 0.0,
        p90_net_efficiency=float(np.percentile(effs, 90)) if len(effs) else 0.0,
        p99_net_efficiency=float(np.percentile(effs, 99)) if len(effs) else 0.0,
        outcomes=tuple(outcomes),
    )


def print_monte_carlo_report(summary: MonteCarloSummary) -> None:
    print()
    print("=" * 72)
    print(f"MONTE CARLO — {summary.trials} stochastic trials")
    print("=" * 72)
    print(f"  Baseline Enet:         {summary.baseline_efficiency:6.1%}")
    print(f"  Baseline capture:      {summary.baseline_capture:6.1%}")
    print()
    print("  Failure taxonomy:")
    print(f"    OK:                  {summary.ok_rate:6.1%}")
    print(f"    Collision:           {summary.collision_rate:6.1%}")
    print(f"    Stroke collapse:     {summary.stroke_collapse_rate:6.1%}")
    print(f"    Capture loss:        {summary.capture_loss_rate:6.1%}")
    print(f"    Efficiency cliff:    {summary.efficiency_cliff_rate:6.1%}")
    print(f"    Boundary fail:       {summary.boundary_fail_rate:6.1%}")
    print(f"    Any failure:         {summary.failure_rate:6.1%}")
    print()
    print(f"  Enet distribution:")
    print(f"    Mean:                {summary.mean_net_efficiency:6.1%}")
    print(f"    P01 / P10 / P50:     {summary.p01_net_efficiency:5.1%} / "
          f"{summary.p10_net_efficiency:5.1%} / {summary.p50_net_efficiency:.1%}")
    print(f"    P90 / P99:           {summary.p90_net_efficiency:5.1%} / "
          f"{summary.p99_net_efficiency:.1%}")
    print("=" * 72)
    print()
