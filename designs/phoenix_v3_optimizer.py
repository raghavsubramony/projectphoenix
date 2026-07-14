"""Automated tuning for Phoenix V3 — learn optimal parameters without manual sweeps.

Three-phase search with optional **parallel workers** and **corpus resume**.

The harvest objective maximizes net electrical output, generator capture, and spring
recovery while penalizing excess BDC travel (>21 mm), power-stroke kinetic storage,
and unharvested expansion work. Do **not** reward raw stroke — that steers toward
high-velocity oscillation modes that score well but do not generate electricity.

Scale up with::

    py -3 designs/phoenix_v3_optimizer.py --trials 500 --workers 8 --cycles 10
    py -3 designs/phoenix_v3_optimizer.py --tier micro --trials 400 --search-version v3
    py -3 designs/phoenix_v3_optimizer.py --tier large --trials 400 --search-version v3

Writes to ``phoenix_v3_tuning_corpus_v2.csv`` (15 parameters including spring
phase gains and load up to 3.0×).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

_DESIGNS = Path(__file__).resolve().parent
_REPO = _DESIGNS.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.tier_presets import (
    native_tier_config,
    native_tuning_seed_dict,
    tier_search_bound_overrides,
)
from designs.phoenix_v3.tier_profiles import (
    TIER_MEDIUM,
    TIER_MICRO,
    TIER_LARGE,
    TierCartridgeProfile,
    profile_for_key,
)
from designs.phoenix_v3_simulation import (  # noqa: E402
    PhoenixV3Config,
    PhoenixV3Simulator,
    SimulationResult,
    _late_cycle_reports,
    _summarize_sweep,
)
from designs.phoenix_v3.efficiency import canonical_net_efficiency

# Harvest-aligned objective — validated ~20 mm BDC / ~50% capture / low piston KE.
BDC_SOFT_CAP_MM = 21.0
ELEC_NORM_J = 550.0

OUT_DIR = _DESIGNS
DEFAULT_CORPUS = OUT_DIR / "phoenix_v3_tuning_corpus.csv"
HARVEST_CORPUS = OUT_DIR / "phoenix_v3_tuning_corpus_harvest.csv"
CORPUS_V2 = OUT_DIR / "phoenix_v3_tuning_corpus_v2.csv"
CORPUS_V3 = OUT_DIR / "phoenix_v3_tuning_corpus_v3.csv"
CORPUS_ACTUATOR = OUT_DIR / "phoenix_v3_tuning_corpus_actuator.csv"
BEST_TUNING_V3 = OUT_DIR / "phoenix_v3_best_tuning_v3.json"
BEST_TUNING_MICRO = OUT_DIR / TIER_MICRO.best_tuning_filename
BEST_TUNING_LARGE = OUT_DIR / TIER_LARGE.best_tuning_filename
BEST_TUNING_ACTUATOR = OUT_DIR / "phoenix_v3_best_tuning_actuator.json"

ACTUATOR_PLANT_KWARGS = {
    "actuator_model_enabled": True,
    "sensor_delay_s": 0.0015,
    # Ke ≈ Kt (SI): back-EMF force ≈ generator_force_per_amp_n × v [N/(m/s)]
    "back_emf_coeff_n_s_m": 38.0,
    "max_generator_current_a": 110.0,
    "current_slew_a_per_s": 180_000.0,
}

CORPUS_FIELDS = (
    [
        "generator_force_scale",
        "generator_rated_force_n",
        "generator_spring_decouple",
        "air_spring_reference_bar",
        "air_spring_max_cc",
        "air_spring_min_cc",
        "air_spring_expansion_gain",
        "air_spring_compression_gain",
        "generator_adaptive_profile",
        "exhaust_open_ms",
        "load_fraction",
        "frequency_hz",
        "combustion_pressure_gain",
        "burn_duration_ms",
        "damping_n_s_m",
        "ignition_ms",
        "compression_ratio",
    ]
    + [
        "score",
        "net_efficiency",
        "capture_fraction",
        "max_bdc_mm",
        "elec_per_spring",
        "expansion_work_j",
        "elec_energy_j",
        "peak_pressure_bar",
        "rgf",
        "scavenge_efficiency",
        "stable",
        "energy_balance_valid",
        "spring_recovery",
        "ke_power_fraction",
        "unharvested_fraction",
    ]
)


@dataclass(frozen=True)
class ParamBounds:
    """One searchable dimension."""

    name: str
    lo: float
    hi: float


SEARCH_BOUNDS: tuple[ParamBounds, ...] = (
    ParamBounds("generator_force_scale", 0.18, 3.0),
    ParamBounds("generator_rated_force_n", 2200.0, 7800.0),
    ParamBounds("generator_spring_decouple", 0.08, 0.45),
    ParamBounds("air_spring_reference_bar", 10.0, 24.0),
    ParamBounds("air_spring_max_cc", 50.0, 82.0),
    ParamBounds("air_spring_min_cc", 18.0, 48.0),
    ParamBounds("air_spring_expansion_gain", 0.55, 0.95),
    ParamBounds("air_spring_compression_gain", 1.0, 1.45),
    ParamBounds("generator_adaptive_profile", 0.0, 1.0),
    ParamBounds("exhaust_open_ms", 7.35, 8.8),
    ParamBounds("load_fraction", 0.62, 0.95),
    ParamBounds("frequency_hz", 42.0, 110.0),
    ParamBounds("combustion_pressure_gain", 1.25, 1.75),
    ParamBounds("burn_duration_ms", 1.3, 2.4),
    ParamBounds("damping_n_s_m", 18.0, 42.0),
    ParamBounds("ignition_ms", 3.6, 5.4),
    ParamBounds("compression_ratio", 10.5, 14.5),
)

ACTUATOR_SEARCH_BOUNDS: tuple[ParamBounds, ...] = tuple(
    ParamBounds(
        b.name,
        22.0 if b.name == "frequency_hz" else (
            0.22 if b.name == "load_fraction" else b.lo
        ),
        1.35 if b.name == "generator_force_scale" else (
            5200.0 if b.name == "generator_rated_force_n" else (
                0.88 if b.name == "load_fraction" else b.hi
            )
        ),
    )
    for b in SEARCH_BOUNDS
)

PARAM_NAMES: tuple[str, ...] = tuple(b.name for b in SEARCH_BOUNDS)


def _apply_bound_overrides(
    base: tuple[ParamBounds, ...],
    overrides: dict[str, tuple[float, float]],
) -> tuple[ParamBounds, ...]:
    if not overrides:
        return base
    return tuple(
        ParamBounds(b.name, overrides[b.name][0], overrides[b.name][1])
        if b.name in overrides
        else b
        for b in base
    )


def _actuator_bounds_from(base: tuple[ParamBounds, ...]) -> tuple[ParamBounds, ...]:
    """Narrow search ranges for actuator-plant tuning on top of ``base``."""
    out: list[ParamBounds] = []
    for b in base:
        if b.name == "frequency_hz":
            out.append(ParamBounds(b.name, min(22.0, b.lo), b.hi))
        elif b.name == "load_fraction":
            out.append(ParamBounds(b.name, 0.22, 0.88))
        elif b.name == "generator_force_scale":
            out.append(ParamBounds(b.name, b.lo, min(b.hi, 1.35)))
        elif b.name == "generator_rated_force_n":
            out.append(ParamBounds(b.name, b.lo, min(b.hi, 5200.0)))
        else:
            out.append(b)
    return tuple(out)


def search_bounds_for_tier(
    tier_index: int | None,
    *,
    actuator_mode: bool = False,
) -> tuple[ParamBounds, ...]:
    """Resolve optimizer bounds for a cartridge tier (or global medium defaults)."""
    base = SEARCH_BOUNDS
    if tier_index is not None:
        base = _apply_bound_overrides(base, tier_search_bound_overrides(tier_index))
    if actuator_mode:
        return _actuator_bounds_from(base)
    return base


def _bounds_for(
    actuator_mode: bool,
    tier_index: int | None = None,
) -> tuple[ParamBounds, ...]:
    return search_bounds_for_tier(tier_index, actuator_mode=actuator_mode)


def _vector_to_dict(vector: TuningVector, bounds: tuple[ParamBounds, ...]) -> dict[str, float]:
    return {
        b.name: b.lo + _clamp01(u) * (b.hi - b.lo)
        for b, u in zip(bounds, vector.values)
    }


@dataclass(frozen=True)
class TuningVector:
    """Normalised search vector in [0, 1]^N — maps to physical parameters."""

    values: tuple[float, ...]

    @staticmethod
    def random(
        rng: random.Random,
        bounds: tuple[ParamBounds, ...] | None = None,
    ) -> TuningVector:
        dim = len(bounds) if bounds is not None else len(SEARCH_BOUNDS)
        return TuningVector(tuple(rng.random() for _ in range(dim)))

    @staticmethod
    def from_dict(
        d: dict[str, float],
        bounds: tuple[ParamBounds, ...] | None = None,
    ) -> TuningVector:
        bnds = bounds or SEARCH_BOUNDS
        vals: list[float] = []
        for b in bnds:
            if b.name in d:
                vals.append(_clamp01((float(d[b.name]) - b.lo) / max(b.hi - b.lo, 1e-12)))
            else:
                vals.append(0.5)
        return TuningVector(tuple(vals))

    def to_dict(
        self,
        bounds: tuple[ParamBounds, ...] | None = None,
    ) -> dict[str, float]:
        return _vector_to_dict(self, bounds or SEARCH_BOUNDS)

    def to_config(
        self,
        base: PhoenixV3Config | None = None,
        *,
        bounds: tuple[ParamBounds, ...] | None = None,
    ) -> PhoenixV3Config:
        d = _vector_to_dict(self, bounds or SEARCH_BOUNDS)
        cfg = base or PhoenixV3Config()
        max_cc = d["air_spring_max_cc"]
        min_cc = min(d["air_spring_min_cc"], max_cc - 8.0)
        return replace(
            cfg,
            generator_force_scale=d["generator_force_scale"],
            generator_rated_force_n=d["generator_rated_force_n"],
            generator_spring_decouple=d["generator_spring_decouple"],
            generator_adaptive_profile=d["generator_adaptive_profile"] >= 0.5,
            air_spring_reference_bar=d["air_spring_reference_bar"],
            air_spring_volume_max_m3=max_cc * 1e-6,
            air_spring_volume_min_m3=max(min_cc, 12.0) * 1e-6,
            air_spring_expansion_gain=d["air_spring_expansion_gain"],
            air_spring_compression_gain=d["air_spring_compression_gain"],
            air_spring_phase_control=True,
            exhaust_open_ms=d["exhaust_open_ms"],
            load_fraction=d["load_fraction"],
            frequency_hz=d["frequency_hz"],
            combustion_pressure_gain=d["combustion_pressure_gain"],
            burn_duration_ms=d["burn_duration_ms"],
            damping_n_s_m=d["damping_n_s_m"],
            ignition_ms=d["ignition_ms"],
            injection_start_ms=max(0.5, d["ignition_ms"] - 0.4),
            compression_ratio=d["compression_ratio"],
        )

    def mutate(self, rng: random.Random, sigma: float = 0.12) -> TuningVector:
        return TuningVector(tuple(
            _clamp01(u + rng.gauss(0.0, sigma)) for u in self.values
        ))

    def crossover(self, other: TuningVector, rng: random.Random) -> TuningVector:
        return TuningVector(tuple(
            a if rng.random() < 0.5 else b
            for a, b in zip(self.values, other.values)
        ))


@dataclass(frozen=True)
class TrialResult:
    """One simulation evaluation."""

    vector: TuningVector
    score: float
    net_efficiency: float
    capture_fraction: float
    max_bdc_mm: float
    elec_per_spring: float
    expansion_work_j: float
    elec_energy_j: float
    peak_pressure_bar: float
    rgf: float
    scavenge_efficiency: float
    stable: bool
    energy_balance_valid: bool
    spring_recovery: float = 0.0
    ke_power_fraction: float = 0.0
    unharvested_fraction: float = 0.0


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, v))


def _latin_hypercube(n: int, dims: int, rng: random.Random) -> list[TuningVector]:
    if n <= 0:
        return []
    columns: list[list[float]] = []
    for _ in range(dims):
        strata = [(i + rng.random()) / n for i in range(n)]
        rng.shuffle(strata)
        columns.append(strata)
    return [TuningVector(tuple(columns[d][i] for d in range(dims))) for i in range(n)]


def _harvest_metrics(
    result: SimulationResult,
    reports: list,
) -> tuple[float, float, float, float, float, float, float]:
    """Late-window harvest KPIs used by the objective (not raw stroke)."""
    late = _late_cycle_reports(result.energy.cycle_reports, result.cfg)
    window = late if late else reports
    if not window:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
    pwr_exp = float(np.mean([r.power_stroke_expansion_j for r in window]))
    ke_power = float(np.mean([abs(r.power_ke_delta_j) for r in window]))
    unharv = float(np.mean([r.power_unharvested_j for r in window]))
    return (
        float(np.mean([r.net_cartridge_efficiency for r in window])),
        float(np.mean([r.capture_fraction for r in window])),
        float(np.mean([r.spring_recovery_efficiency for r in window])),
        float(np.mean([r.elec_energy_j for r in window])),
        float(np.mean([r.max_piston_travel_mm for r in window])),
        ke_power / max(pwr_exp, 1e-9),
        unharv / max(pwr_exp, 1e-9),
    )


def _score_result(
    vector: TuningVector,
    result: SimulationResult,
    *,
    actuator_mode: bool = False,
) -> TrialResult:
    e = result.energy
    m = result.metrics
    reports = [r for r in e.cycle_reports if r.fuel_energy_j > 50.0]
    sweep = _summarize_sweep(
        result,
        label="trial",
        generator_force_scale=result.cfg.generator_force_scale,
        exhaust_open_ms=result.cfg.exhaust_open_ms,
        peak_stroke_mm=2.0 * result.cfg.half_stroke_m * 1e3,
        air_spring_max_cc=result.cfg.air_spring_volume_max_m3 * 1e6,
        air_spring_min_cc=result.cfg.air_spring_volume_min_m3 * 1e6,
        air_spring_reference_bar=result.cfg.air_spring_reference_bar,
    )

    (
        net_eff,
        capture,
        spring_rec,
        elec_j,
        bdc_mm,
        ke_frac,
        unharv_frac,
    ) = _harvest_metrics(result, reports)
    if actuator_mode:
        net_eff = canonical_net_efficiency(result)
    dead_cycle = not reports or sweep.expansion_work_j < 20.0

    # Harvest objective: maximize electrical extraction, not piston travel.
    score = net_eff
    score += 0.12 * capture
    score += 0.08 * spring_rec
    score += 0.05 * min(elec_j / ELEC_NORM_J, 1.0)

    if bdc_mm > BDC_SOFT_CAP_MM:
        over = min((bdc_mm - BDC_SOFT_CAP_MM) / 4.0, 1.0)
        if elec_j < 0.82 * ELEC_NORM_J:
            score -= 0.15 * over
    score -= 0.20 * min(ke_frac, 0.35)
    score -= 0.10 * min(unharv_frac, 0.30)

    if not sweep.stable:
        score *= 0.35
    if not e.energy_balance_valid:
        score *= 0.25
    if not e.raw_boundary_valid:
        score *= 0.85
    if m.residual_gas_fraction > 0.05:
        score *= 0.70
    if m.scavenge_efficiency < 0.93:
        score *= 0.75
    if dead_cycle:
        score = -0.05

    if actuator_mode:
        collision = any(r.piston_collision for r in reports)
        if collision:
            score = -0.15
        elif net_eff < 0.35:
            score = -0.05 + net_eff * 0.15
        elif net_eff >= 0.48:
            score += 0.04
        if net_eff < 0.40:
            score *= 0.35

    return TrialResult(
        vector=vector,
        score=score,
        net_efficiency=net_eff if reports else sweep.net_efficiency,
        capture_fraction=capture if reports else sweep.capture_fraction,
        max_bdc_mm=bdc_mm if reports else sweep.max_bdc_mm,
        elec_per_spring=elec_j / max(sweep.spring_stored_j, 1e-9) if reports else sweep.elec_per_spring_j,
        expansion_work_j=sweep.expansion_work_j,
        elec_energy_j=elec_j if reports else sweep.elec_energy_j,
        peak_pressure_bar=sweep.peak_pressure_bar,
        rgf=sweep.rgf,
        scavenge_efficiency=m.scavenge_efficiency,
        stable=sweep.stable,
        energy_balance_valid=e.energy_balance_valid,
        spring_recovery=spring_rec,
        ke_power_fraction=ke_frac,
        unharvested_fraction=unharv_frac,
    )


def _rank_trials(trials: list[TrialResult]) -> list[TrialResult]:
    """Prefer stable configs when ranking by harvest score."""
    stable = [t for t in trials if t.stable]
    pool = stable if stable else trials
    return sorted(pool, key=lambda t: t.score, reverse=True)


def _select_best_actuator(trials: list[TrialResult]) -> TrialResult:
    """Prefer stable configs with actuator Enet 48–52% and modest ideal→actuator drop."""
    stable = [t for t in trials if t.stable]
    pool = stable if stable else trials
    ranked = sorted(pool, key=lambda t: t.net_efficiency, reverse=True)
    top = ranked[: min(24, len(ranked))]
    best: TrialResult | None = None
    best_key = (-1.0, -1.0)
    for trial in top:
        bounds = ACTUATOR_SEARCH_BOUNDS
        cfg = trial.vector.to_config(bounds=bounds)
        ideal_cfg = replace(cfg, thermal_model_enabled=True)
        act_cfg = replace(cfg, **ACTUATOR_PLANT_KWARGS, thermal_model_enabled=True)
        from designs.phoenix_v3_simulation import PhoenixV3Simulator, apply_generator_cooling

        ideal_cfg = apply_generator_cooling(ideal_cfg, "water_jacket")
        act_cfg = apply_generator_cooling(act_cfg, "water_jacket")
        ideal_r = PhoenixV3Simulator(ideal_cfg).simulate(cycles=20, record_history=False)
        act_r = PhoenixV3Simulator(act_cfg).simulate(cycles=20, record_history=False)
        ideal_eff = canonical_net_efficiency(ideal_r)
        act_eff = canonical_net_efficiency(act_r)
        delta_pp = (act_eff - ideal_eff) * 100.0
        if act_eff < 0.45 or ideal_eff < 0.48:
            continue
        if act_eff > ideal_eff + 0.02:
            continue
        in_band = 0.48 <= act_eff <= 0.52
        key = (
            1.0 if in_band else 0.0,
            act_eff - 0.01 * abs(delta_pp + 3.0),
        )
        if key > best_key:
            best_key = key
            best = replace(trial, net_efficiency=act_eff, capture_fraction=trial.capture_fraction)
    return best if best is not None else ranked[0]


def _select_best_stable(trials: list[TrialResult], *, actuator_mode: bool = False) -> TrialResult:
    if actuator_mode:
        return _select_best_actuator(trials)
    ranked = _rank_trials(trials)
    return ranked[0] if ranked else trials[0]


def _select_best_v3_export(
    trials: list[TrialResult],
    *,
    tier_index: int | None,
    actuator_mode: bool = False,
    require_ring_valid: bool = False,
) -> tuple[TrialResult, TrialResult, dict[str, float | bool | str]]:
    """Pick export candidate; prefer ring-valid configs when ``require_ring_valid``."""
    ranked = _rank_trials(trials)
    peak = max(trials, key=lambda t: t.net_efficiency)
    if not ranked:
        raise ValueError("empty trial list")

    best: TrialResult | None = None
    best_extras: dict[str, float | bool | str] = {}
    pool = ranked[: min(72, len(ranked))]

    for trial in pool:
        validated, extras = validate_best_for_export(
            trial.vector,
            actuator_mode=actuator_mode,
            tier_index=tier_index,
        )
        ring_ok = bool(extras.get("ring_valid", False))
        if require_ring_valid and not ring_ok:
            continue
        if best is None:
            best = validated
            best_extras = extras
            continue
        if validated.net_efficiency > best.net_efficiency:
            if not require_ring_valid or ring_ok:
                best = validated
                best_extras = extras

    if best is None:
        best, best_extras = validate_best_for_export(
            ranked[0].vector,
            actuator_mode=actuator_mode,
            tier_index=tier_index,
        )
    return best, peak, best_extras


def evaluate_vector(
    vector: TuningVector,
    *,
    base: PhoenixV3Config | None = None,
    cycles: int = 8,
    actuator_mode: bool = False,
    tier_index: int | None = None,
) -> TrialResult:
    bounds = _bounds_for(actuator_mode, tier_index)
    cfg = vector.to_config(base, bounds=bounds)
    if actuator_mode:
        cfg = replace(cfg, **ACTUATOR_PLANT_KWARGS)
    result = PhoenixV3Simulator(cfg).simulate(cycles=cycles)
    trial = _score_result(vector, result, actuator_mode=actuator_mode)
    if actuator_mode:
        trial = replace(trial, net_efficiency=canonical_net_efficiency(result))
    return trial


def _worker_payload(
    values: tuple[float, ...],
    cycles: int,
    tier_index: int | None,
    actuator_mode: bool,
) -> TrialResult:
    """Process-pool entry point (must be module-level for Windows spawn)."""
    base = native_tier_config(tier_index) if tier_index is not None else None
    return evaluate_vector(
        TuningVector(values),
        base=base,
        cycles=cycles,
        actuator_mode=actuator_mode,
        tier_index=tier_index,
    )


def _config_to_kwargs(cfg: PhoenixV3Config | None) -> dict[str, float] | None:
    if cfg is None:
        return None
    return {
        "frequency_hz": cfg.frequency_hz,
        "load_fraction": cfg.load_fraction,
        "swirl_scavenge_gain": cfg.swirl_scavenge_gain,
    }


def evaluate_batch(
    vectors: list[TuningVector],
    *,
    base: PhoenixV3Config | None = None,
    cycles: int = 8,
    workers: int = 1,
    actuator_mode: bool = False,
    tier_index: int | None = None,
) -> list[TrialResult]:
    """Evaluate many parameter vectors, optionally in parallel."""
    if not vectors:
        return []
    if workers <= 1:
        return [
            evaluate_vector(
                v,
                base=base,
                cycles=cycles,
                actuator_mode=actuator_mode,
                tier_index=tier_index,
            )
            for v in vectors
        ]

    results: list[TrialResult | None] = [None] * len(vectors)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_worker_payload, v.values, cycles, tier_index, actuator_mode): i
            for i, v in enumerate(vectors)
        }
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return [r for r in results if r is not None]


class KnnSurrogate:
    """Inverse-distance weighted k-NN regressor."""

    def __init__(self, k: int = 12) -> None:
        self.k = k
        self._x: list[np.ndarray] = []
        self._y: list[float] = []

    def fit(self, trials: list[TrialResult]) -> None:
        self._x = [np.array(t.vector.values, dtype=float) for t in trials]
        self._y = [t.score for t in trials]

    def predict(self, vector: TuningVector) -> float:
        if not self._x:
            return 0.0
        x = np.array(vector.values, dtype=float)
        dists = [float(np.linalg.norm(x - xi)) for xi in self._x]
        order = sorted(range(len(dists)), key=lambda i: dists[i])[: self.k]
        weights, values = [], []
        for i in order:
            w = 1.0 / (dists[i] + 1e-6)
            weights.append(w)
            values.append(self._y[i])
        return sum(w * v for w, v in zip(weights, values)) / max(sum(weights), 1e-12)

    def suggest_batch(
        self,
        rng: random.Random,
        n: int,
        *,
        n_candidates: int = 96,
        explore: float = 0.10,
    ) -> list[TuningVector]:
        if not self._x:
            return [TuningVector.random(rng) for _ in range(n)]
        scored: list[tuple[float, TuningVector]] = []
        for _ in range(n_candidates):
            parent = TuningVector(tuple(
                float(v) for v in self._x[rng.randrange(len(self._x))]
            ))
            cand = parent.mutate(rng, sigma=explore)
            scored.append((self.predict(cand), cand))
        scored.sort(key=lambda x: x[0], reverse=True)
        out: list[TuningVector] = []
        seen: set[tuple[float, ...]] = set()
        for _, cand in scored:
            key = tuple(round(v, 5) for v in cand.values)
            if key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= n:
                break
        while len(out) < n:
            out.append(TuningVector.random(rng))
        return out


def trial_to_row(
    t: TrialResult,
    bounds: tuple[ParamBounds, ...] | None = None,
) -> dict[str, str]:
    row = {
        k: f"{v:.6f}" if isinstance(v, float) else str(v)
        for k, v in t.vector.to_dict(bounds).items()
    }
    row.update({
        "score": f"{t.score:.6f}",
        "net_efficiency": f"{t.net_efficiency:.6f}",
        "capture_fraction": f"{t.capture_fraction:.6f}",
        "max_bdc_mm": f"{t.max_bdc_mm:.3f}",
        "elec_per_spring": f"{t.elec_per_spring:.6f}",
        "expansion_work_j": f"{t.expansion_work_j:.2f}",
        "elec_energy_j": f"{t.elec_energy_j:.2f}",
        "peak_pressure_bar": f"{t.peak_pressure_bar:.1f}",
        "rgf": f"{t.rgf:.6f}",
        "scavenge_efficiency": f"{t.scavenge_efficiency:.6f}",
        "stable": str(int(t.stable)),
        "energy_balance_valid": str(int(t.energy_balance_valid)),
        "spring_recovery": f"{t.spring_recovery:.6f}",
        "ke_power_fraction": f"{t.ke_power_fraction:.6f}",
        "unharvested_fraction": f"{t.unharvested_fraction:.6f}",
    })
    return row


def _parse_bool_field(val: str | None) -> bool:
    if not val:
        return False
    try:
        return bool(int(float(val)))
    except ValueError:
        return val.strip().lower() in ("true", "yes", "1")


def row_to_trial(
    row: dict[str, str],
    bounds: tuple[ParamBounds, ...] | None = None,
) -> TrialResult:
    bnds = bounds or SEARCH_BOUNDS
    params: dict[str, float] = {}
    for name in PARAM_NAMES:
        if name in row and row[name] != "":
            params[name] = float(row[name])
        else:
            b = next(x for x in bnds if x.name == name)
            params[name] = 0.5 * (b.lo + b.hi)
    vector = TuningVector.from_dict(params, bounds=bnds)
    return TrialResult(
        vector=vector,
        score=float(row["score"]),
        net_efficiency=float(row["net_efficiency"]),
        capture_fraction=float(row["capture_fraction"]),
        max_bdc_mm=float(row["max_bdc_mm"]),
        elec_per_spring=float(row["elec_per_spring"]),
        expansion_work_j=float(row["expansion_work_j"]),
        elec_energy_j=float(row["elec_energy_j"]),
        peak_pressure_bar=float(row["peak_pressure_bar"]),
        rgf=float(row["rgf"]),
        scavenge_efficiency=float(row["scavenge_efficiency"]),
        stable=_parse_bool_field(row.get("stable")),
        energy_balance_valid=_parse_bool_field(row.get("energy_balance_valid")),
        spring_recovery=float(row.get("spring_recovery") or 0.0),
        ke_power_fraction=float(row.get("ke_power_fraction") or 0.0),
        unharvested_fraction=float(row.get("unharvested_fraction") or 0.0),
    )


def load_corpus(
    path: Path,
    *,
    tier_index: int | None = None,
    actuator_mode: bool = False,
) -> list[TrialResult]:
    if not path.exists():
        return []
    bounds = _bounds_for(actuator_mode, tier_index)
    trials: list[TrialResult] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "net_efficiency" not in reader.fieldnames:
            return []
        for row in reader:
            try:
                trials.append(row_to_trial(row, bounds))
            except (KeyError, TypeError, ValueError):
                continue
    return trials


def append_corpus(
    trials: list[TrialResult],
    path: Path,
    *,
    bounds: tuple[ParamBounds, ...] | None = None,
    tier_index: int | None = None,
    actuator_mode: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.stat().st_size == 0:
        write_corpus(trials, path, bounds=bounds)
        return
    with path.open(newline="", encoding="utf-8") as f:
        existing_header = list(csv.DictReader(f).fieldnames or [])
    missing = [f for f in CORPUS_FIELDS if f not in existing_header]
    if missing:
        prior = load_corpus(path, tier_index=tier_index, actuator_mode=actuator_mode)
        write_corpus(prior + trials, path, bounds=bounds)
        return
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CORPUS_FIELDS)
        for t in trials:
            writer.writerow(trial_to_row(t, bounds))


def write_corpus(
    trials: list[TrialResult],
    path: Path,
    *,
    bounds: tuple[ParamBounds, ...] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CORPUS_FIELDS)
        writer.writeheader()
        for t in trials:
            writer.writerow(trial_to_row(t, bounds))


@dataclass
class OptimizationReport:
    """Outcome of an automated tuning run."""

    trials: list[TrialResult]
    best: TrialResult
    best_net: TrialResult
    best_stable: TrialResult
    new_trials: int
    total_corpus: int
    generations: int
    corpus_path: Path
    elapsed_s: float

    def top(self, n: int = 10, *, by_net: bool = False) -> list[TrialResult]:
        key = (lambda t: t.net_efficiency) if by_net else (lambda t: t.score)
        return sorted(self.trials, key=key, reverse=True)[:n]

    def param_importance(self) -> list[tuple[str, float]]:
        if len(self.trials) < 10:
            return []
        scores = np.array([t.net_efficiency for t in self.trials], dtype=float)
        if np.std(scores) < 1e-12:
            return []
        rows: list[tuple[str, float]] = []
        for name in PARAM_NAMES:
            vals = np.array([t.vector.to_dict()[name] for t in self.trials], dtype=float)
            if np.std(vals) < 1e-12:
                continue
            corr = float(np.corrcoef(vals, scores)[0, 1])
            if not math.isnan(corr):
                rows.append((name, corr))
        rows.sort(key=lambda x: abs(x[1]), reverse=True)
        return rows


def _dedupe_vectors(vectors: list[TuningVector], seen: set[tuple[float, ...]]) -> list[TuningVector]:
    out: list[TuningVector] = []
    for v in vectors:
        key = tuple(round(x, 5) for x in v.values)
        if key in seen:
            continue
        seen.add(key)
        out.append(v)
    return out


def run_optimization(
    *,
    trials: int = 100,
    cycles: int = 8,
    seed: int = 42,
    base: PhoenixV3Config | None = None,
    corpus_path: Path | None = None,
    population: int = 40,
    workers: int = 1,
    resume: bool = False,
    verbose: bool = True,
    actuator_mode: bool = False,
    tier_index: int | None = None,
    tier_profile: TierCartridgeProfile | None = None,
) -> OptimizationReport:
    """Large-scale search with parallel evaluation and optional corpus resume."""
    t0 = time.perf_counter()
    rng = random.Random(seed)
    if tier_profile is not None:
        tier_index = tier_profile.tier_index
    if base is None and tier_index is not None:
        base = native_tier_config(tier_index)
    out_path = corpus_path or (CORPUS_ACTUATOR if actuator_mode else DEFAULT_CORPUS)
    bounds = _bounds_for(actuator_mode, tier_index)
    dims = len(bounds)

    prior = (
        load_corpus(out_path, tier_index=tier_index, actuator_mode=actuator_mode)
        if resume
        else []
    )
    seen: set[tuple[float, ...]] = {
        tuple(round(v, 5) for v in t.vector.values) for t in prior
    }
    all_trials: list[TrialResult] = list(prior)
    new_count = 0

    def run_batch(vectors: list[TuningVector], label: str) -> None:
        nonlocal new_count
        if new_count >= target_new:
            return
        vectors = _dedupe_vectors(vectors, seen)[: target_new - new_count]
        if not vectors:
            return
        if verbose:
            print(f"  {label}: {len(vectors)} trials (workers={workers})...", flush=True)
        batch = evaluate_batch(
            vectors, base=base, cycles=cycles, workers=workers,
            actuator_mode=actuator_mode, tier_index=tier_index,
        )
        all_trials.extend(batch)
        new_count += len(batch)
        if resume:
            append_corpus(
                batch,
                out_path,
                bounds=bounds,
                tier_index=tier_index,
                actuator_mode=actuator_mode,
            )

    target_new = trials
    pop = max(population, 24)

    # Phase 0 — seed non-medium tiers (large uses native geometry, micro uses medium JSON)
    if (
        not actuator_mode
        and not resume
        and new_count < target_new
        and tier_index is not None
        and tier_index != TIER_MEDIUM.tier_index
    ):
        if tier_index == TIER_LARGE.tier_index:
            seed_vec = TuningVector.from_dict(
                native_tuning_seed_dict(tier_index), bounds=bounds,
            )
            if verbose:
                print(
                    f"Phase 0: Seeding tier {tier_index} from native large geometry...",
                    flush=True,
                )
            seed_batch = [seed_vec] + [
                seed_vec.mutate(rng, sigma=s) for s in (0.06, 0.10, 0.14, 0.18)
                for _ in range(6)
            ]
            run_batch(seed_batch[: min(36, target_new)], "seed-native")
        else:
            seed_path = TIER_MEDIUM.best_tuning_path
            if seed_path.is_file():
                payload = json.loads(seed_path.read_text(encoding="utf-8"))
                seed_vec = TuningVector.from_dict(payload["parameters"], bounds=bounds)
                if verbose:
                    print(
                        f"Phase 0: Seeding tier {tier_index} from medium best tuning "
                        f"({seed_path.name})...",
                        flush=True,
                    )
                seed_batch = [seed_vec] + [
                    seed_vec.mutate(rng, sigma=s) for s in (0.05, 0.08, 0.10, 0.12, 0.15)
                    for _ in range(5)
                ]
                run_batch(seed_batch[: min(30, target_new)], "seed-medium")

    # Phase 0b — actuator seed from v2
    if actuator_mode and not resume and new_count < target_new:
        seed_path = OUT_DIR / "phoenix_v3_best_tuning_v2.json"
        if seed_path.exists():
            payload = json.loads(seed_path.read_text(encoding="utf-8"))
            seed_vec = TuningVector.from_dict(payload["parameters"], bounds=bounds)
            if verbose:
                print("Phase 0: Seeding from v2 best tuning (actuator-bounded mutations)...", flush=True)
            seed_batch = [seed_vec] + [
                seed_vec.mutate(rng, sigma=s) for s in (0.04, 0.06, 0.08, 0.10, 0.12)
                for _ in range(6)
            ]
            run_batch(seed_batch[: min(36, target_new)], "seed-v2")

    # Phase 1 — exploration (scale with trial budget)
    explore_n = min(max(pop, target_new // 5), 800, target_new)
    if new_count < target_new:
        if verbose:
            print(f"Phase 1/4: Exploration ({explore_n} trials)...", flush=True)
        run_batch(_latin_hypercube(explore_n, dims, rng), "explore")

    surrogate = KnnSurrogate(k=min(16, max(8, len(all_trials) // 50)))
    surrogate.fit(all_trials)

    # Phase 2 — genetic evolution
    if verbose:
        print("Phase 2/4: Genetic evolution...", flush=True)
    pop_vectors = [t.vector for t in _rank_trials(all_trials)[:pop]]
    gen_count = 0
    while new_count < target_new:
        gen_count += 1
        next_gen: list[TuningVector] = pop_vectors[:6]
        while len(next_gen) < pop:
            a, b = rng.sample(pop_vectors, 2)
            next_gen.append(a.crossover(b, rng).mutate(rng, sigma=0.09))
        batch_size = min(len(next_gen) - 6, target_new - new_count)
        run_batch(next_gen[6:6 + batch_size], f"gen {gen_count}")
        pop_vectors = [t.vector for t in _rank_trials(all_trials)[:pop]]
        surrogate.fit(all_trials)

    # Phase 3 — surrogate-guided batch
    refine_n = min(max(32, target_new // 8), 400)
    if new_count < target_new and verbose:
        print(f"Phase 3/4: Surrogate search ({refine_n} trials)...", flush=True)
    while new_count < target_new:
        need = min(refine_n, target_new - new_count)
        cands = surrogate.suggest_batch(rng, need, n_candidates=need * 8, explore=0.08)
        run_batch(cands, "surrogate")
        surrogate.fit(all_trials)

    # Phase 4 — local search around top harvest scores
    local_n = min(max(16, target_new // 12), 200)
    if new_count < target_new and verbose:
        print(f"Phase 4/4: Local refinement ({local_n} trials)...", flush=True)
    top_harvest = _rank_trials(all_trials)[:10]
    local_cands: list[TuningVector] = []
    for parent in top_harvest:
        for _ in range(local_n // max(len(top_harvest), 1)):
            local_cands.append(parent.vector.mutate(rng, sigma=0.04))
    run_batch(local_cands[: target_new - new_count], "local")

    best = _select_best_stable(all_trials, actuator_mode=actuator_mode)
    best_net = max(all_trials, key=lambda t: t.net_efficiency)
    best_stable = best
    elapsed = time.perf_counter() - t0

    if not resume:
        write_corpus(all_trials, out_path, bounds=bounds)

    return OptimizationReport(
        trials=all_trials,
        best=best,
        best_net=best_net,
        best_stable=best_stable,
        new_trials=new_count,
        total_corpus=len(all_trials),
        generations=gen_count,
        corpus_path=out_path,
        elapsed_s=elapsed,
    )


def print_optimization_report(report: OptimizationReport) -> None:
    best = report.best_stable
    peak = report.best_net
    d = best.vector.to_dict()
    print()
    print("=" * 72)
    print("PHOENIX V3 — AUTOMATED TUNING RESULT (v2 search space)")
    print("=" * 72)
    print(f"  New trials this run:  {report.new_trials}")
    print(f"  Total corpus size:   {report.total_corpus}")
    print(f"  Elapsed:             {report.elapsed_s:.1f} s")
    print(f"  Corpus path:         {report.corpus_path}")
    if not best.stable and peak.net_efficiency > best.net_efficiency:
        print(f"  Peak net (unstable):  {peak.net_efficiency:.1%}  — see top-5 table")
    print()
    print("  Best STABLE config by harvest score:")
    print(f"    Net fuel-to-electric: {best.net_efficiency:.1%}  (target > 40%)")
    print(f"    Harvest score:        {best.score:.4f}")
    print(f"    Generator capture:    {best.capture_fraction:.1%}")
    print(f"    BDC travel:           {best.max_bdc_mm:.1f} mm")
    print(f"    Electrical/cycle:     {best.elec_energy_j:.1f} J")
    print(f"    Spring recovery:      {best.spring_recovery:.1%}")
    print(f"    Power-stroke KE:      {best.ke_power_fraction:.1%} of expansion")
    print(f"    Unharvested (power):  {best.unharvested_fraction:.1%} of expansion")
    print(f"    Eelec / Espring:      {best.elec_per_spring:.2f}")
    print(f"    Peak pressure:        {best.peak_pressure_bar:.0f} bar")
    print(f"    RGF / scavenging:     {best.rgf:.1%} / {best.scavenge_efficiency:.1%}")
    print(f"    Stable / balanced:    {best.stable} / {best.energy_balance_valid}")
    print()
    print("  Recommended parameters:")
    for name in PARAM_NAMES:
        val = d[name]
        if name.endswith("_cc"):
            print(f"    {name:28s} {val:8.1f}")
        elif name.endswith("_ms"):
            print(f"    {name:28s} {val:8.2f}")
        elif name.endswith("_n"):
            print(f"    {name:28s} {val:8.0f}")
        elif name.endswith("_hz"):
            print(f"    {name:28s} {val:8.1f}")
        else:
            print(f"    {name:28s} {val:8.3f}")
    print()
    print("  Top 5 by harvest score (stable preferred):")
    print(f"  {'Rank':>4}  {'Enet':>6}  {'Score':>7}  {'Cap%':>6}  {'BDC':>5}  {'Eelec':>6}  {'Spr':>5}  {'KE%':>5}  Stab")
    for i, t in enumerate(report.top(5, by_net=False), 1):
        stab = "OK" if t.stable else "--"
        print(
            f"  {i:4d}  {t.net_efficiency:5.1%}  {t.score:7.4f}  "
            f"{t.capture_fraction:5.1%}  {t.max_bdc_mm:5.1f}  {t.elec_energy_j:6.1f}  "
            f"{t.spring_recovery:4.0%}  {t.ke_power_fraction*100:4.0f}  {stab}"
        )
    print()
    above_24 = sum(1 for t in report.trials if t.net_efficiency >= 0.24)
    above_20 = sum(1 for t in report.trials if t.net_efficiency >= 0.20)
    print(f"  Corpus stats:  >=24% net: {above_24}  |  >=20% net: {above_20}")
    imp = report.param_importance()
    if imp:
        print()
        print("  Parameter sensitivity (vs net efficiency):")
        for name, corr in imp[:8]:
            bar = "+" * int(abs(corr) * 20)
            sign = "+" if corr >= 0 else "-"
            print(f"    {sign} {name:28s} {corr:+.3f}  {bar}")
    print("=" * 72)
    print()
    print("  Scale up further:")
    print(
        f"    py -3 designs/phoenix_v3_optimizer.py --trials 2000 --workers 8 "
        f"--resume --cycles 8"
    )
    print()
    print("  Re-run best config (full vector — includes frequency):")
    print(
        f"    py -3 designs/phoenix_v3_simulation.py --headless --cycles 10 "
        f"--frequency {d['frequency_hz']:.1f} "
        f"--generator-scale {d['generator_force_scale']:.3f} "
        f"--exhaust-open-ms {d['exhaust_open_ms']:.2f} "
        f"--spring-ref-bar {d['air_spring_reference_bar']:.1f} "
        f"--load {d['load_fraction']:.2f}"
    )
    print("  Or load exported JSON:")
    print("    py -3 designs/phoenix_v3_simulation.py --headless --best-tuning --cycles 10")
    print()


def validate_best_for_export(
    vector: TuningVector,
    *,
    single_cycles: int = 24,
    ring_cycles: int = 60,
    actuator_mode: bool = False,
    tier_index: int | None = None,
) -> tuple[TrialResult, dict[str, float | bool | str]]:
    """Re-simulate best vector with honest late-window + ring metrics."""
    bounds = _bounds_for(actuator_mode, tier_index)
    base = native_tier_config(tier_index) if tier_index is not None else None
    cfg = vector.to_config(base, bounds=bounds)
    if actuator_mode:
        cfg = replace(cfg, **ACTUATOR_PLANT_KWARGS)
    cfg = replace(cfg, thermal_model_enabled=True)
    from designs.phoenix_v3_simulation import apply_generator_cooling

    cfg = apply_generator_cooling(cfg, "water_jacket")
    result = PhoenixV3Simulator(cfg).simulate(cycles=single_cycles, record_history=False)
    trial = _score_result(vector, result, actuator_mode=actuator_mode)
    trial = replace(trial, net_efficiency=canonical_net_efficiency(result))

    ideal_eff = 0.0
    if actuator_mode:
        ideal_cfg = replace(vector.to_config(base, bounds=bounds), thermal_model_enabled=True)
        ideal_cfg = apply_generator_cooling(ideal_cfg, "water_jacket")
        ideal_result = PhoenixV3Simulator(ideal_cfg).simulate(
            cycles=single_cycles, record_history=False,
        )
        ideal_eff = canonical_net_efficiency(ideal_result)

    ring_ok = True
    ring_eff = 0.0
    ring_gap = 0.0
    try:
        from designs.phoenix_v3_ring import RingConfig, simulate_ring

        ring = simulate_ring(
            cfg,
            RingConfig(12),
            cycles=ring_cycles,
            record_history=False,
        )
        ring_eff = ring.ring_efficiency
        ring_gap = abs(ring_eff - trial.net_efficiency)
        ring_ok = (
            ring.energy_balance_valid
            and ring.raw_boundary_valid
            and ring_gap < 0.05
            and ring.max_asymmetry < 0.05
        )
    except Exception:
        ring_ok = False

    extras = {
        "ring_efficiency": ring_eff,
        "ring_gap_pp": ring_gap,
        "ring_valid": ring_ok,
        "physics_validated": trial.stable and trial.energy_balance_valid and ring_ok,
        "actuator_mode": actuator_mode,
        "ideal_plant_efficiency": ideal_eff if actuator_mode else None,
        "actuator_efficiency_delta_pp": (
            (trial.net_efficiency - ideal_eff) * 100.0 if actuator_mode else None
        ),
    }
    extras = {k: v for k, v in extras.items() if v is not None}
    return trial, extras


def export_best_config(
    report: OptimizationReport,
    path: Path | None = None,
    *,
    search_version: str = "v2",
    validate: bool = False,
    actuator_mode: bool = False,
    tier_profile: TierCartridgeProfile | None = None,
) -> Path:
    if actuator_mode:
        out = path or BEST_TUNING_ACTUATOR
    elif path is not None:
        out = path
    elif tier_profile is not None:
        out = tier_profile.best_tuning_path
    elif search_version == "v3":
        out = BEST_TUNING_V3
    elif "v2" in report.corpus_path.name:
        out = OUT_DIR / "phoenix_v3_best_tuning_v2.json"
    else:
        out = OUT_DIR / "phoenix_v3_best_tuning.json"

    best = report.best_stable
    peak = report.best_net
    validated_extras: dict[str, float | bool | str] = {}
    tier_idx = tier_profile.tier_index if tier_profile else None
    bounds = _bounds_for(actuator_mode, tier_idx)
    require_ring = tier_idx == TIER_LARGE.tier_index
    if validate or search_version == "v3" or actuator_mode:
        best, peak, validated_extras = _select_best_v3_export(
            report.trials,
            tier_index=tier_idx,
            actuator_mode=actuator_mode,
            require_ring_valid=require_ring,
        )
        if require_ring and not validated_extras.get("ring_valid", False):
            print(
                "  Warning: no ring-valid config in top 72 — exported best-effort candidate",
                flush=True,
            )

    param_dict = best.vector.to_dict(bounds)

    if out.exists() and not validate and search_version != "v3":
        try:
            prior = json.loads(out.read_text(encoding="utf-8"))
            prior_net = float(prior.get("metrics", {}).get("net_efficiency", 0.0))
            if best.net_efficiency < prior_net - 0.005:
                print(
                    f"  Skipping JSON export: {best.net_efficiency:.1%} net "
                    f"< existing {prior_net:.1%} at {out.name}",
                    flush=True,
                )
                return out
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    payload = {
        "score": best.score,
        "stable": best.stable,
        "tier": tier_profile.key if tier_profile else None,
        "total_displacement_cc": tier_profile.total_displacement_cc if tier_profile else None,
        "displacement_per_side_cc": tier_profile.displacement_per_side_cc if tier_profile else None,
        "metrics": {
            "net_efficiency": best.net_efficiency,
            "capture_fraction": best.capture_fraction,
            "max_bdc_mm": best.max_bdc_mm,
            "elec_energy_j": best.elec_energy_j,
            "peak_pressure_bar": best.peak_pressure_bar,
            "spring_recovery": best.spring_recovery,
            "ke_power_fraction": best.ke_power_fraction,
            "unharvested_fraction": best.unharvested_fraction,
        },
        "peak_unstable": {
            "net_efficiency": peak.net_efficiency,
            "capture_fraction": peak.capture_fraction,
            "max_bdc_mm": peak.max_bdc_mm,
        } if peak.net_efficiency > best.net_efficiency and not peak.stable else None,
        "parameters": param_dict,
        "corpus_size": report.total_corpus,
        "search_version": "actuator" if actuator_mode else search_version,
        "physics_note": (
            "Metrics from validate_best_for_export() on current V3 physics; "
            "not legacy corpus scores."
            if validated_extras
            else None
        ),
        "ring_validation": validated_extras if validated_extras else None,
    }
    # Remove null keys for cleaner JSON
    payload = {k: v for k, v in payload.items() if v is not None}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out


def default_workers() -> int:
    return max(1, (os.cpu_count() or 4) - 1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Automated Phoenix V3 parameter search")
    parser.add_argument("--trials", type=int, default=200,
                        help="New trials to run this session (default 200)")
    parser.add_argument("--cycles", type=int, default=8, help="Cycles per evaluation")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--population", type=int, default=40, help="GA population size")
    parser.add_argument("--workers", type=int, default=default_workers(),
                        help="Parallel worker processes (default: CPU count - 1)")
    parser.add_argument("--resume", action="store_true",
                        help="Append to existing corpus; seed GA from prior trials")
    parser.add_argument("--corpus", type=Path, default=None,
                        help="CSV output path (default: phoenix_v3_tuning_corpus_v2.csv)")
    parser.add_argument("--legacy-corpus", action="store_true",
                        help="Use phoenix_v3_tuning_corpus.csv instead of v2")
    parser.add_argument("--search-version", choices=("v2", "v3"), default="v2",
                        help="Corpus + export version (v3 validates ring + honest metrics)")
    parser.add_argument("--export-json", type=Path, default=None,
                        help="Override best-tuning JSON output path")
    parser.add_argument("--actuators", action="store_true",
                        help="Tune under actuator plant (sensor delay, back-EMF, current limits)")
    parser.add_argument(
        "--tier",
        choices=("micro", "medium", "large"),
        default=None,
        help="Cartridge size: micro=100cc, medium=300cc (150cc/side), large=750cc",
    )
    args = parser.parse_args()
    tier_profile = profile_for_key(args.tier) if args.tier else None
    if args.corpus is not None:
        corpus_path = args.corpus
    elif tier_profile is not None:
        corpus_path = tier_profile.corpus_path
    elif args.actuators:
        corpus_path = CORPUS_ACTUATOR
    elif args.search_version == "v3":
        corpus_path = CORPUS_V3
    elif args.legacy_corpus:
        corpus_path = DEFAULT_CORPUS
    else:
        corpus_path = CORPUS_V2

    tier_label = f" tier={tier_profile.key}" if tier_profile else ""
    if tier_profile is not None:
        freq = next(b for b in search_bounds_for_tier(tier_profile.tier_index) if b.name == "frequency_hz")
        print(
            f"  Tier search bounds: frequency_hz {freq.lo:.1f}–{freq.hi:.1f} Hz",
            flush=True,
        )
    print(f"Phoenix V3 optimizer {args.search_version}{tier_label}"
          f"{' +actuators' if args.actuators else ''}: "
          f"{args.trials} new trials, "
          f"{args.workers} workers, "
          f"{'resume' if args.resume else 'fresh'} corpus -> {corpus_path.name}")
    report = run_optimization(
        trials=args.trials,
        cycles=args.cycles,
        seed=args.seed,
        population=args.population,
        workers=args.workers,
        resume=args.resume,
        corpus_path=corpus_path,
        actuator_mode=args.actuators,
        tier_index=tier_profile.tier_index if tier_profile else None,
        tier_profile=tier_profile,
    )
    print_optimization_report(report)
    export_path = export_best_config(
        report,
        path=args.export_json or (
            tier_profile.best_tuning_path if tier_profile and not args.actuators else None
        ) or (BEST_TUNING_ACTUATOR if args.actuators else None),
        search_version=args.search_version,
        validate=args.search_version == "v3" or args.actuators,
        actuator_mode=args.actuators,
        tier_profile=tier_profile,
    )
    print(f"Best config JSON: {export_path}")


if __name__ == "__main__":
    main()
