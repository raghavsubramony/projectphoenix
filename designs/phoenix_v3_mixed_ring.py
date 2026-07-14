"""ATPE mixed-tier ring study — all three cartridge sizes on one logical ring.

The homogeneous V3 ring (``phoenix_v3_ring.py``) models 12× identical medium
cartridges.  Phase-1 vehicle stack is **4 micro + 2 medium + 2 large** (8 slots).
Gate-4 / V4 production ring is **4 micro + 6 medium + 2 large** (12 slots).

This module:
  1. Builds per-slot configs from tier-specific best-tuning JSON files.
  2. Validates every searchable tier parameter against tier-native bounds.
  3. Runs cartridge physics and sums electrical output (DC bus).
  4. Estimates frame vibration via a lumped unbalanced reciprocating-force model.
  5. Computes Ring Balance Index (RBI) across frequency, power, capture, stroke.
  6. Compares scheduling strategies (interleaved, tier blocks, harmonic paired).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Iterable

import numpy as np

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.tier_presets import load_tier_tuning_config, tier_search_bound_overrides
from designs.phoenix_v3.tier_profiles import profile_for_tier_index
from designs.phoenix_v3_simulation import PhoenixV3Simulator, apply_generator_cooling
from designs.phoenix_v3_optimizer import PARAM_NAMES, search_bounds_for_tier

# Phase-1 validated vehicle stack (digital_twin/gate4_scaling.py) — 8 cartridges.
PHASE1_LAYOUT: tuple[int, int, int] = (4, 2, 2)

# Gate-4 / V4 twelve-cartridge ring (4 micro + 6 medium + 2 large).
GATE4_LAYOUT: tuple[int, int, int] = (4, 6, 2)

# Default layout for V4 ring-harmony studies.
DEFAULT_RING_LAYOUT: tuple[int, int, int] = GATE4_LAYOUT

# Opposed-piston residual unbalance after primary cancellation (lumped).
DEFAULT_UNBALANCE_FRACTION = 0.12

# RBI target from ring-harmony strategy (percent).
RBI_TARGET_PCT = 5.0

# Expected fast/slow harmonic ratio (large ≈ half of micro/medium).
HARMONIC_RATIO_TARGET = 2.0
HARMONIC_RATIO_TOLERANCE = 0.15


class RingSchedule(str, Enum):
    """How cartridges are ordered around the ring."""

    INTERLEAVED = "interleaved"
    TIER_BLOCKS = "tier_blocks"
    HARMONIC_PAIRED = "harmonic_paired"
    MICRO_MEDIUM_RING = "micro_medium_ring"
    UNIFORM_MEDIUM = "uniform_medium"


DEFAULT_RING_SCHEDULE = RingSchedule.TIER_BLOCKS


@dataclass(frozen=True)
class RingLoadPolicy:
    """Per-tier bus load multipliers (applied on top of tuned ``load_fraction``)."""

    micro_multiplier: float = 1.00
    medium_multiplier: float = 1.05
    large_multiplier: float = 0.85

    def multiplier_for(self, tier_index: int) -> float:
        return (self.micro_multiplier, self.medium_multiplier, self.large_multiplier)[
            max(0, min(tier_index, 2))
        ]


DEFAULT_LOAD_POLICY = RingLoadPolicy()
UNITY_LOAD_POLICY = RingLoadPolicy(1.0, 1.0, 1.0)

# Gate-4 frozen production stack (V3.1 study verdict — do not force 2:1 harmonic).
GATE4_FROZEN_LOAD_POLICY = RingLoadPolicy(0.98, 1.03, 0.95)

# Option-2 experiment: lighter micro, medium at tier load ceiling, softer large.
GATE4_MEDIUM_HEAVY_POLICY = RingLoadPolicy(0.947, 1.0, 0.944)

# Gate-4 baseline (Option 2+1: medium-heavy load + NVH phase layout).
GATE4_PRODUCTION_LOAD_POLICY = GATE4_MEDIUM_HEAVY_POLICY

# Gate-5 is the approved production freeze candidate (supersedes Gate-4 Option 2+1).
GATE5_PRODUCTION_LOAD_POLICY = GATE4_PRODUCTION_LOAD_POLICY

# V3.1 weighted bus sharing — superseded by production freeze for new work.
V31_LOAD_POLICY = GATE4_FROZEN_LOAD_POLICY

# Dynamics targets (peak force is the remaining open item; harmonic exact-2:1 is deprecated).
V31_TARGET_RING_EFFICIENCY = 0.54
V31_TARGET_TIER_RBI_PCT = 5.0
V31_TARGET_PEAK_FORCE_N = 1000.0

# Gate-5 medium-phase refinement targets (build on approved Gate-4 freeze).
GATE5_TARGET_RING_EFFICIENCY = 0.545
GATE5_TARGET_MIN_POWER_KW = 310.0
GATE5_TARGET_MEDIUM_RBI_PCT = 3.0
GATE5_TARGET_PEAK_FORCE_N = 1200.0


@dataclass(frozen=True)
class RingBuildOptions:
    """Optional overrides for harmonic tuning and NVH phase layout."""

    load_policy: RingLoadPolicy = UNITY_LOAD_POLICY
    large_frequency_hz: float | None = None
    harmonic_lock_large: bool = False  # experimental — requires large-tier re-tune at new Hz
    tier_phase_offsets_ms: dict[int, tuple[float, ...]] | None = None


DEFAULT_BUILD_OPTIONS = RingBuildOptions()


@dataclass(frozen=True)
class MixedRingLayout:
    """Cartridge counts (micro, medium, large)."""

    n_micro: int
    n_medium: int
    n_large: int

    @property
    def total(self) -> int:
        return self.n_micro + self.n_medium + self.n_large

    @property
    def label(self) -> str:
        return f"{self.n_micro}/{self.n_medium}/{self.n_large}"


@dataclass(frozen=True)
class TierParameterViolation:
    """One tuning parameter outside tier-native search bounds."""

    tier_index: int
    parameter: str
    value: float
    bound_lo: float
    bound_hi: float


@dataclass(frozen=True)
class TierConfigAudit:
    """Validation snapshot for one tier's loaded cartridge config."""

    tier_index: int
    tier_name: str
    displacement_cc: float
    tuning_path: str | None
    violations: tuple[TierParameterViolation, ...]
    harmonic_ratio: float | None

    @property
    def valid(self) -> bool:
        return not self.violations


@dataclass(frozen=True)
class MixedCartridgeSlot:
    """One cartridge on the mixed ring."""

    index: int
    tier_index: int
    tier_name: str
    angle_rad: float
    phase_offset_ms: float
    frequency_hz: float
    displacement_cc: float
    load_fraction: float
    cfg: PhoenixV3Config


@dataclass(frozen=True)
class NvhProxyResult:
    """Lumped horizontal force on the ring frame (not FEA)."""

    peak_resultant_n: float
    rms_resultant_n: float
    min_resultant_n: float
    crest_factor: float
    dominant_beat_hz: float
    schedule: RingSchedule
    layout_label: str

    @property
    def peak_per_cartridge_avg_n(self) -> float:
        return self.peak_resultant_n  # set by caller context


@dataclass(frozen=True)
class TierCohortRbi:
    """Within-tier balance — the meaningful RBI for heterogeneous rings."""

    tier_index: int
    tier_name: str
    cartridge_count: int
    total_pct: float
    power_cv_pct: float
    capture_cv_pct: float
    stroke_cv_pct: float
    frequency_cv_pct: float

    @property
    def meets_target(self) -> bool:
        return self.total_pct < RBI_TARGET_PCT


@dataclass(frozen=True)
class RingBalanceIndex:
    """Ring Balance Index — lower is better (tier target < 5%)."""

    # Per-cartridge RBI (all slots) — includes phase spread within a tier.
    total_pct: float
    frequency_cv_pct: float
    power_cv_pct: float
    capture_cv_pct: float
    stroke_cv_pct: float
    # Tier-aggregate RBI (one mean per active tier) — harmony / supervisor metric.
    tier_total_pct: float
    tier_fast_freq_cv_pct: float
    tier_power_cv_pct: float
    tier_capture_cv_pct: float
    tier_stroke_cv_pct: float
    harmonic_deviation_pct: float
    per_tier: tuple[TierCohortRbi, ...]

    @property
    def meets_target(self) -> bool:
        """Tier-aggregate RBI gate (ignores intentional cross-tier frequency split)."""
        return self.tier_total_pct < RBI_TARGET_PCT

    @property
    def meets_cartridge_target(self) -> bool:
        return self.total_pct < RBI_TARGET_PCT


@dataclass(frozen=True)
class MixedCartridgeResult:
    slot: MixedCartridgeSlot
    mean_elec_power_w: float
    mean_net_efficiency: float
    mean_capture_fraction: float
    mean_stroke_mm: float
    peak_pressure_bar: float
    spring_recovery: float
    stable: bool
    boundary_valid: bool


@dataclass(frozen=True)
class MixedRingResult:
    layout: MixedRingLayout
    schedule: RingSchedule
    load_policy: RingLoadPolicy
    cartridges: tuple[MixedCartridgeResult, ...]
    tier_audits: tuple[TierConfigAudit, ...]
    total_elec_power_w: float
    ring_efficiency: float
    rbi: RingBalanceIndex
    nvh: NvhProxyResult
    energy_balance_valid: bool
    all_boundaries_valid: bool


def _config_tuning_dict(cfg: PhoenixV3Config) -> dict[str, float]:
    """Extract searchable tuning parameters from a ``PhoenixV3Config``."""
    return {
        "generator_force_scale": cfg.generator_force_scale,
        "generator_rated_force_n": cfg.generator_rated_force_n,
        "generator_spring_decouple": cfg.generator_spring_decouple,
        "air_spring_reference_bar": cfg.air_spring_reference_bar,
        "air_spring_max_cc": cfg.air_spring_volume_max_m3 * 1e6,
        "air_spring_min_cc": cfg.air_spring_volume_min_m3 * 1e6,
        "air_spring_expansion_gain": cfg.air_spring_expansion_gain,
        "air_spring_compression_gain": cfg.air_spring_compression_gain,
        "generator_adaptive_profile": 1.0 if cfg.generator_adaptive_profile else 0.0,
        "exhaust_open_ms": cfg.exhaust_open_ms,
        "load_fraction": cfg.load_fraction,
        "frequency_hz": cfg.frequency_hz,
        "combustion_pressure_gain": cfg.combustion_pressure_gain,
        "burn_duration_ms": cfg.burn_duration_ms,
        "damping_n_s_m": cfg.damping_n_s_m,
        "ignition_ms": cfg.ignition_ms,
        "compression_ratio": cfg.compression_ratio,
    }


def _clamp_load_fraction(value: float, tier_index: int) -> float:
    bounds = tier_search_bound_overrides(tier_index)
    lo, hi = bounds["load_fraction"]
    return max(lo, min(hi, value))


def _apply_tier_load_policy(
    cfg: PhoenixV3Config,
    tier_index: int,
    policy: RingLoadPolicy,
) -> PhoenixV3Config:
    multiplier = policy.multiplier_for(tier_index)
    if abs(multiplier - 1.0) < 1e-12:
        return cfg
    new_load = _clamp_load_fraction(cfg.load_fraction * multiplier, tier_index)
    return replace(cfg, load_fraction=new_load)


def audit_tier_config(
    cfg: PhoenixV3Config,
    tier_index: int,
    *,
    tuning_path: str | None = None,
    fast_reference_hz: float | None = None,
) -> TierConfigAudit:
    """Verify tier geometry and all searchable parameters against tier bounds."""
    profile = profile_for_tier_index(tier_index)
    bounds_map = {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(tier_index)}
    tuning = _config_tuning_dict(cfg)
    violations: list[TierParameterViolation] = []

    expected_cc = profile.total_displacement_cc
    actual_cc = 2.0 * cfg.displacement_per_side_m3 * 1e6
    if abs(actual_cc - expected_cc) > 0.5:
        violations.append(
            TierParameterViolation(
                tier_index, "displacement_cc", actual_cc, expected_cc, expected_cc,
            )
        )

    for name in PARAM_NAMES:
        if name not in tuning:
            continue
        value = tuning[name]
        lo, hi = bounds_map[name]
        if value < lo - 1e-9 or value > hi + 1e-9:
            violations.append(
                TierParameterViolation(tier_index, name, value, lo, hi)
            )

    harmonic_ratio: float | None = None
    if tier_index == 2 and fast_reference_hz is not None and fast_reference_hz > 0:
        harmonic_ratio = fast_reference_hz / max(cfg.frequency_hz, 1e-9)
        target_lo = HARMONIC_RATIO_TARGET * (1.0 - HARMONIC_RATIO_TOLERANCE)
        target_hi = HARMONIC_RATIO_TARGET * (1.0 + HARMONIC_RATIO_TOLERANCE)
        if harmonic_ratio < target_lo or harmonic_ratio > target_hi:
            violations.append(
                TierParameterViolation(
                    tier_index,
                    "harmonic_ratio",
                    harmonic_ratio,
                    target_lo,
                    target_hi,
                )
            )

    return TierConfigAudit(
        tier_index=tier_index,
        tier_name=profile.name,
        displacement_cc=actual_cc,
        tuning_path=tuning_path,
        violations=tuple(violations),
        harmonic_ratio=harmonic_ratio,
    )


def _coefficient_of_variation_pct(values: Iterable[float]) -> float:
    arr = np.asarray(list(values), dtype=float)
    if arr.size == 0:
        return 0.0
    mean = float(np.mean(arr))
    if abs(mean) < 1e-12:
        return 0.0
    return float(np.std(arr) / abs(mean) * 100.0)


def _empty_ring_balance_index() -> RingBalanceIndex:
    return RingBalanceIndex(
        0.0, 0.0, 0.0, 0.0, 0.0,
        0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
        (),
    )


def _within_tier_rbi(
    cartridges: list[MixedCartridgeResult],
    tier_index: int,
) -> TierCohortRbi | None:
    cohort = [c for c in cartridges if c.slot.tier_index == tier_index]
    if not cohort:
        return None
    profile = profile_for_tier_index(tier_index)
    power_cv = _coefficient_of_variation_pct(c.mean_elec_power_w for c in cohort)
    capture_cv = _coefficient_of_variation_pct(c.mean_capture_fraction for c in cohort)
    stroke_cv = _coefficient_of_variation_pct(c.mean_stroke_mm for c in cohort)
    freq_cv = (
        _coefficient_of_variation_pct(c.slot.frequency_hz for c in cohort)
        if len(cohort) >= 2
        else 0.0
    )
    total = 0.3 * power_cv + 0.3 * capture_cv + 0.2 * stroke_cv + 0.2 * freq_cv
    return TierCohortRbi(
        tier_index=tier_index,
        tier_name=profile.name,
        cartridge_count=len(cohort),
        total_pct=total,
        power_cv_pct=power_cv,
        capture_cv_pct=capture_cv,
        stroke_cv_pct=stroke_cv,
        frequency_cv_pct=freq_cv,
    )


def _tier_group_means(
    cartridges: list[MixedCartridgeResult],
) -> tuple[dict[int, list[MixedCartridgeResult]], list[int]]:
    groups: dict[int, list[MixedCartridgeResult]] = {0: [], 1: [], 2: []}
    for cart in cartridges:
        groups[cart.slot.tier_index].append(cart)
    active = [tier for tier in (0, 1, 2) if groups[tier]]
    return groups, active


def _fast_bank_hz(groups: dict[int, list[MixedCartridgeResult]]) -> float:
    weighted = 0.0
    count = 0
    for tier in (0, 1):
        for cart in groups[tier]:
            weighted += cart.slot.frequency_hz
            count += 1
    return weighted / max(count, 1)


def compute_ring_balance_index(
    cartridges: Iterable[MixedCartridgeResult],
) -> RingBalanceIndex:
    """Cartridge RBI + tier-aggregate RBI (supervisor uses ``tier_total_pct``)."""
    carts = list(cartridges)
    if not carts:
        return _empty_ring_balance_index()

    freq_cv = _coefficient_of_variation_pct(c.slot.frequency_hz for c in carts)
    power_cv = _coefficient_of_variation_pct(c.mean_elec_power_w for c in carts)
    capture_cv = _coefficient_of_variation_pct(c.mean_capture_fraction for c in carts)
    stroke_cv = _coefficient_of_variation_pct(c.mean_stroke_mm for c in carts)
    cartridge_total = (
        0.4 * freq_cv + 0.3 * power_cv + 0.2 * capture_cv + 0.1 * stroke_cv
    )

    groups, active = _tier_group_means(carts)

    fast_freqs = []
    if groups[0]:
        fast_freqs.append(float(np.mean([c.slot.frequency_hz for c in groups[0]])))
    if groups[1]:
        fast_freqs.append(float(np.mean([c.slot.frequency_hz for c in groups[1]])))
    tier_fast_freq_cv = (
        _coefficient_of_variation_pct(fast_freqs) if len(fast_freqs) >= 2 else 0.0
    )

    def _mean_within_tier_cv(getter) -> float:
        values: list[float] = []
        for tier in active:
            if len(groups[tier]) < 2:
                continue
            values.append(_coefficient_of_variation_pct(getter(c) for c in groups[tier]))
        return float(np.mean(values)) if values else 0.0

    tier_power_cv = _mean_within_tier_cv(lambda c: c.mean_elec_power_w)
    tier_capture_cv = _mean_within_tier_cv(lambda c: c.mean_capture_fraction)
    tier_stroke_cv = _mean_within_tier_cv(lambda c: c.mean_stroke_mm)

    harmonic_deviation_pct = 0.0
    if groups[2]:
        fast_hz = _fast_bank_hz(groups)
        large_hz = float(np.mean([c.slot.frequency_hz for c in groups[2]]))
        if large_hz > 1e-9:
            harmonic_deviation_pct = (
                abs(fast_hz / large_hz - HARMONIC_RATIO_TARGET)
                / HARMONIC_RATIO_TARGET
                * 100.0
            )

    tier_total = (
        0.4 * tier_fast_freq_cv
        + 0.3 * tier_power_cv
        + 0.2 * tier_capture_cv
        + 0.1 * tier_stroke_cv
    )

    per_tier: list[TierCohortRbi] = []
    for tier in (0, 1, 2):
        cohort_rbi = _within_tier_rbi(carts, tier)
        if cohort_rbi is not None:
            per_tier.append(cohort_rbi)

    return RingBalanceIndex(
        total_pct=cartridge_total,
        frequency_cv_pct=freq_cv,
        power_cv_pct=power_cv,
        capture_cv_pct=capture_cv,
        stroke_cv_pct=stroke_cv,
        tier_total_pct=tier_total,
        tier_fast_freq_cv_pct=tier_fast_freq_cv,
        tier_power_cv_pct=tier_power_cv,
        tier_capture_cv_pct=tier_capture_cv,
        tier_stroke_cv_pct=tier_stroke_cv,
        harmonic_deviation_pct=harmonic_deviation_pct,
        per_tier=tuple(per_tier),
    )


def harmonic_target_large_hz(
    tier_cfgs: dict[int, PhoenixV3Config],
    layout: MixedRingLayout,
) -> float:
    """Ideal large-cartridge Hz = fast-bank mean / 2 (exact half-harmonic)."""
    fast_hz = _fast_reference_hz(tier_cfgs, layout)
    return fast_hz / HARMONIC_RATIO_TARGET


def _clamp_large_frequency(hz: float) -> float:
    bounds = tier_search_bound_overrides(2)["frequency_hz"]
    return max(bounds[0], min(bounds[1], hz))


def _fast_reference_hz(
    tier_cfgs: dict[int, PhoenixV3Config],
    layout: MixedRingLayout,
) -> float:
    """Count-weighted mean frequency of micro + medium (fast bank)."""
    weighted = 0.0
    count = 0
    if layout.n_micro > 0 and 0 in tier_cfgs:
        weighted += tier_cfgs[0].frequency_hz * layout.n_micro
        count += layout.n_micro
    if layout.n_medium > 0 and 1 in tier_cfgs:
        weighted += tier_cfgs[1].frequency_hz * layout.n_medium
        count += layout.n_medium
    return weighted / max(count, 1)


def _gate4_micro_medium_ring_order(layout: MixedRingLayout) -> list[int]:
    """Gate-4 4/6/2 pattern: M m M m L M m M m L M m around the circumference."""
    if layout.label != "4/6/2":
        return _interleave_micro_medium(layout) + [2] * layout.n_large
    return [0, 1, 0, 1, 2, 1, 0, 1, 0, 1, 2, 1]


def _interleave_micro_medium(layout: MixedRingLayout) -> list[int]:
    """Round-robin micro and medium tier indices."""
    micro = [0] * layout.n_micro
    medium = [1] * layout.n_medium
    order: list[int] = []
    while micro or medium:
        if micro:
            order.append(micro.pop(0))
        if medium:
            order.append(medium.pop(0))
    return order


def _slot_tier_list(layout: MixedRingLayout, schedule: RingSchedule) -> list[int]:
    """Return tier_index per slot (0=micro, 1=medium, 2=large)."""
    micro = [0] * layout.n_micro
    medium = [1] * layout.n_medium
    large = [2] * layout.n_large
    if schedule == RingSchedule.UNIFORM_MEDIUM:
        return [1] * layout.total
    if schedule == RingSchedule.TIER_BLOCKS:
        return micro + medium + large
    if schedule == RingSchedule.HARMONIC_PAIRED:
        return _interleave_micro_medium(layout) + large
    if schedule == RingSchedule.MICRO_MEDIUM_RING:
        return _gate4_micro_medium_ring_order(layout)
    # Interleaved: spread all tiers evenly around the ring.
    pools: list[list[int]] = [micro, medium, large]
    counts = [layout.n_micro, layout.n_medium, layout.n_large]
    order: list[int] = []
    remaining = layout.total
    while remaining > 0:
        for tier, pool in enumerate(pools):
            if counts[tier] > 0:
                order.append(pool.pop(0))
                counts[tier] -= 1
                remaining -= 1
    return order


def _phase_offset_ms(
    tier: int,
    peer_rank: int,
    n_peer: int,
    cfg: PhoenixV3Config,
    *,
    schedule: RingSchedule,
) -> float:
    """Even spacing within a tier; harmonic paired keeps large on fast grid origin."""
    if n_peer <= 0:
        return 0.0
    cycle_ms = cfg.cycle_time_s * 1e3
    return peer_rank * (cycle_ms / n_peer)


def _load_tier_configs(
    layout: MixedRingLayout,
    *,
    cooling_mode: str,
    build_options: RingBuildOptions,
) -> tuple[dict[int, PhoenixV3Config], dict[int, str | None]]:
    """Load and optionally re-scale per-tier configs (geometry + full tuning vector)."""
    tier_cfgs: dict[int, PhoenixV3Config] = {}
    tier_paths: dict[int, str | None] = {}
    for tier in range(3):
        count = (layout.n_micro, layout.n_medium, layout.n_large)[tier]
        if count <= 0:
            continue
        cfg, path = load_tier_tuning_config(tier)
        cfg = apply_generator_cooling(cfg, cooling_mode)
        cfg = _apply_tier_load_policy(cfg, tier, build_options.load_policy)
        tier_cfgs[tier] = cfg
        tier_paths[tier] = path.name if path else None

    if layout.n_large > 0 and 2 in tier_cfgs:
        if build_options.harmonic_lock_large:
            target = _clamp_large_frequency(
                harmonic_target_large_hz(tier_cfgs, layout),
            )
            tier_cfgs[2] = replace(tier_cfgs[2], frequency_hz=target)
        elif build_options.large_frequency_hz is not None:
            tier_cfgs[2] = replace(
                tier_cfgs[2],
                frequency_hz=_clamp_large_frequency(build_options.large_frequency_hz),
            )

    return tier_cfgs, tier_paths


def build_mixed_ring_slots(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    cooling_mode: str = "water_jacket",
    load_policy: RingLoadPolicy | None = None,
    build_options: RingBuildOptions | None = None,
) -> tuple[MixedCartridgeSlot, ...]:
    """Build per-cartridge configs with ring angle, phase offsets, and tier audits."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    options = build_options or DEFAULT_BUILD_OPTIONS
    if load_policy is not None:
        options = replace(options, load_policy=load_policy)
    tier_list = _slot_tier_list(layout, schedule)
    n = layout.total
    if n == 0:
        return ()

    tier_cfgs, _ = _load_tier_configs(
        layout, cooling_mode=cooling_mode, build_options=options,
    )

    tier_positions: dict[int, list[int]] = {0: [], 1: [], 2: []}
    for idx, tier in enumerate(tier_list):
        tier_positions[tier].append(idx)

    tier_phase_overrides = options.tier_phase_offsets_ms or {}

    slots: list[MixedCartridgeSlot] = []
    for idx, tier in enumerate(tier_list):
        profile = profile_for_tier_index(tier)
        cfg = tier_cfgs[tier]
        angle = 2.0 * math.pi * idx / n
        peers = tier_positions[tier]
        peer_rank = peers.index(idx)
        n_peer = len(peers)
        phase_ms = _phase_offset_ms(
            tier, peer_rank, n_peer, cfg, schedule=schedule,
        )
        overrides = tier_phase_overrides.get(tier)
        if overrides and peer_rank < len(overrides):
            phase_ms = overrides[peer_rank]

        slots.append(
            MixedCartridgeSlot(
                index=idx,
                tier_index=tier,
                tier_name=profile.name,
                angle_rad=angle,
                phase_offset_ms=phase_ms,
                frequency_hz=cfg.frequency_hz,
                displacement_cc=profile.total_displacement_cc,
                load_fraction=cfg.load_fraction,
                cfg=replace(cfg, phase_offset_ms=phase_ms),
            )
        )
    return tuple(slots)


def audit_ring_tier_configs(
    layout: MixedRingLayout | None = None,
    *,
    cooling_mode: str = "water_jacket",
    load_policy: RingLoadPolicy = UNITY_LOAD_POLICY,
    build_options: RingBuildOptions | None = None,
) -> tuple[TierConfigAudit, ...]:
    """Audit all active tiers — geometry + full parameter vector vs tier bounds."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    options = build_options or RingBuildOptions(load_policy=load_policy)
    if build_options is None and load_policy is not UNITY_LOAD_POLICY:
        options = replace(options, load_policy=load_policy)
    tier_cfgs, tier_paths = _load_tier_configs(
        layout, cooling_mode=cooling_mode, build_options=options,
    )
    fast_hz = _fast_reference_hz(tier_cfgs, layout)
    audits: list[TierConfigAudit] = []
    for tier, cfg in sorted(tier_cfgs.items()):
        audits.append(
            audit_tier_config(
                cfg,
                tier,
                tuning_path=tier_paths.get(tier),
                fast_reference_hz=fast_hz if tier == 2 else None,
            )
        )
    return tuple(audits)


def _unbalanced_force_amplitude_n(cfg: PhoenixV3Config) -> float:
    """Peak unbalanced shaking force per cartridge (opposed-piston residual)."""
    omega = 2.0 * math.pi * cfg.frequency_hz
    stroke = cfg.half_stroke_m
    return (
        DEFAULT_UNBALANCE_FRACTION
        * cfg.piston_mass_kg
        * omega
        * omega
        * stroke
    )


def analyze_nvh_proxy(
    slots: Iterable[MixedCartridgeSlot],
    *,
    schedule: RingSchedule,
    layout_label: str,
    samples: int = 4000,
    duration_s: float | None = None,
) -> NvhProxyResult:
    """Sum horizontal unbalanced forces; report peak/RMS and beat frequency."""
    slot_list = list(slots)
    if not slot_list:
        return NvhProxyResult(0.0, 0.0, 0.0, 0.0, 0.0, schedule, layout_label)

    freqs = [s.frequency_hz for s in slot_list]
    f_min = min(freqs)
    if duration_s is None:
        duration_s = max(2.0 / f_min, 0.25) if f_min > 0 else 1.0

    t = np.linspace(0.0, duration_s, samples)
    fx = np.zeros_like(t)
    fy = np.zeros_like(t)
    for slot in slot_list:
        f0 = _unbalanced_force_amplitude_n(slot.cfg)
        phase = slot.phase_offset_ms * 1e-3
        arg = 2.0 * math.pi * slot.frequency_hz * (t - phase)
        fx += f0 * np.cos(arg) * math.cos(slot.angle_rad)
        fy += f0 * np.cos(arg) * math.sin(slot.angle_rad)

    resultant = np.hypot(fx, fy)
    peak = float(np.max(resultant))
    rms = float(np.sqrt(np.mean(resultant * resultant)))
    minimum = float(np.min(resultant))

    beat = 0.0
    for i, fi in enumerate(freqs):
        for fj in freqs[i + 1 :]:
            beat = max(beat, abs(fi - fj))

    crest = peak / rms if rms > 1e-9 else 0.0
    return NvhProxyResult(
        peak_resultant_n=peak,
        rms_resultant_n=rms,
        min_resultant_n=minimum,
        crest_factor=crest,
        dominant_beat_hz=beat,
        schedule=schedule,
        layout_label=layout_label,
    )


def _apply_tier_phase_offsets(
    slots: tuple[MixedCartridgeSlot, ...],
    tier_index: int,
    phase_offsets_ms: tuple[float, ...],
) -> tuple[MixedCartridgeSlot, ...]:
    """Return slots with updated phase offsets for one tier cohort."""
    tier_slots = [s for s in slots if s.tier_index == tier_index]
    if len(tier_slots) != len(phase_offsets_ms):
        return slots
    phase_by_index = {
        s.index: phase_offsets_ms[i] for i, s in enumerate(tier_slots)
    }
    updated: list[MixedCartridgeSlot] = []
    for slot in slots:
        if slot.index not in phase_by_index:
            updated.append(slot)
            continue
        phase_ms = phase_by_index[slot.index]
        updated.append(
            replace(
                slot,
                phase_offset_ms=phase_ms,
                cfg=replace(slot.cfg, phase_offset_ms=phase_ms),
            )
        )
    return tuple(updated)


def _simulate_cartridge_slot(
    slot: MixedCartridgeSlot,
    *,
    cycles: int = 12,
) -> tuple[MixedCartridgeResult, float, bool]:
    """Run physics for one mixed-ring cartridge slot.

    Returns (cartridge result, mean fuel power W, energy balance valid).
    """
    sim = PhoenixV3Simulator(slot.cfg).simulate(cycles=cycles, record_history=False)
    reports = [
        r for r in sim.energy.cycle_reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    late = reports[-max(1, len(reports) // 10):] if reports else []
    mean_elec_j = float(np.mean([r.elec_energy_j for r in late])) if late else 0.0
    mean_fuel_j = float(np.mean([r.fuel_energy_j for r in late])) if late else 0.0
    mean_eff = float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0
    mean_cap = float(np.mean([r.capture_fraction for r in late])) if late else 0.0
    mean_stroke = float(np.mean([r.max_piston_travel_mm for r in late])) if late else 0.0
    spring_rec = (
        float(np.mean([r.spring_recovery_efficiency for r in late])) if late else 0.0
    )
    elec_w = mean_elec_j / max(slot.cfg.cycle_time_s, 1e-9)
    fuel_w = mean_fuel_j / max(slot.cfg.cycle_time_s, 1e-9)
    boundary_ok = bool(late) and all(r.raw_boundary_valid for r in late)
    return (
        MixedCartridgeResult(
            slot=slot,
            mean_elec_power_w=elec_w,
            mean_net_efficiency=mean_eff,
            mean_capture_fraction=mean_cap,
            mean_stroke_mm=mean_stroke,
            peak_pressure_bar=sim.metrics.peak_pressure_bar,
            spring_recovery=spring_rec,
            stable=boundary_ok,
            boundary_valid=boundary_ok,
        ),
        fuel_w,
        sim.energy.energy_balance_valid,
    )


def _tier_cohort_rbi_from_slots(
    slots: tuple[MixedCartridgeSlot, ...],
    tier_index: int,
    *,
    cycles: int = 12,
) -> TierCohortRbi | None:
    """Simulate one tier cohort and return within-tier RBI."""
    tier_slots = [s for s in slots if s.tier_index == tier_index]
    if not tier_slots:
        return None
    results = [_simulate_cartridge_slot(slot, cycles=cycles)[0] for slot in tier_slots]
    return _within_tier_rbi(results, tier_index)


def optimize_tier_phases_for_capture_uniformity(
    slots: tuple[MixedCartridgeSlot, ...],
    tier_index: int,
    *,
    cycles: int = 12,
    steps: int = 40,
    passes: int = 2,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    layout_label: str = "",
    baseline_peak_force_n: float | None = None,
    peak_force_slack_n: float = 30.0,
) -> tuple[float, ...]:
    """Coordinate search on one tier's phase offsets to minimize within-tier RBI.

    Uses cartridge physics (capture/stroke dispersion), not the NVH proxy alone.
    Rejects candidates that regress peak frame force beyond ``baseline + slack``.
    """
    tier_slots = [s for s in slots if s.tier_index == tier_index]
    n_peer = len(tier_slots)
    if n_peer == 0:
        return ()
    if n_peer == 1:
        return (tier_slots[0].phase_offset_ms,)

    cycle_ms = tier_slots[0].cfg.cycle_time_s * 1e3
    phases = list(s.phase_offset_ms for s in tier_slots)
    label = layout_label or "mixed"

    def _score(trial_phases: tuple[float, ...]) -> float:
        trial_slots = _apply_tier_phase_offsets(slots, tier_index, trial_phases)
        cohort = _tier_cohort_rbi_from_slots(trial_slots, tier_index, cycles=cycles)
        if cohort is None:
            return float("inf")
        nvh = analyze_nvh_proxy(
            trial_slots, schedule=schedule, layout_label=label,
        )
        if baseline_peak_force_n is not None:
            if nvh.peak_resultant_n > baseline_peak_force_n + peak_force_slack_n:
                return float("inf")
        return cohort.total_pct + 0.01 * nvh.peak_resultant_n

    best_phases = tuple(phases)
    best_score = _score(best_phases)

    for _ in range(passes):
        improved = False
        for peer in range(n_peer):
            local_best = phases[peer]
            local_score = best_score
            for phase_ms in np.linspace(0.0, cycle_ms, steps):
                trial = list(phases)
                trial[peer] = float(phase_ms)
                score = _score(tuple(trial))
                if score < local_score:
                    local_score = score
                    local_best = float(phase_ms)
            if local_best != phases[peer] or local_score < best_score:
                phases[peer] = local_best
                best_phases = tuple(phases)
                best_score = _score(best_phases)
                improved = True
        if not improved:
            break
    return best_phases


def build_gate4_production_options(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    cooling_mode: str = "water_jacket",
    phase_steps: int = 60,
) -> RingBuildOptions:
    """Approved Gate-4 freeze: medium-heavy load + per-tier NVH phase layout."""
    layout = layout or MixedRingLayout(*GATE4_LAYOUT)
    base = RingBuildOptions(load_policy=GATE4_PRODUCTION_LOAD_POLICY)
    slots = build_mixed_ring_slots(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        build_options=base,
    )
    offsets = optimize_nvh_phase_layout(
        slots,
        schedule=schedule,
        layout_label=layout.label,
        tiers=(2, 1, 0),
        steps=phase_steps,
    )
    return replace(base, tier_phase_offsets_ms=offsets)


def optimize_gate5_medium_phases(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    cooling_mode: str = "water_jacket",
    cycles: int = 12,
    capture_steps: int = 40,
    capture_passes: int = 2,
    phase_steps: int = 60,
) -> RingBuildOptions:
    """Gate-5: refine medium-tier phases for capture uniformity on frozen Gate-4 stack."""
    layout = layout or MixedRingLayout(*GATE4_LAYOUT)
    base = build_gate4_production_options(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        phase_steps=phase_steps,
    )
    slots = build_mixed_ring_slots(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        build_options=base,
    )
    baseline_peak = analyze_nvh_proxy(
        slots, schedule=schedule, layout_label=layout.label,
    ).peak_resultant_n
    medium_phases = optimize_tier_phases_for_capture_uniformity(
        slots,
        1,
        cycles=cycles,
        steps=capture_steps,
        passes=capture_passes,
        schedule=schedule,
        layout_label=layout.label,
        baseline_peak_force_n=baseline_peak,
    )
    offsets = dict(base.tier_phase_offsets_ms or {})
    offsets[1] = medium_phases
    return replace(base, tier_phase_offsets_ms=offsets)


def optimize_tier_phases_for_nvh(
    slots: tuple[MixedCartridgeSlot, ...],
    tier_index: int,
    *,
    schedule: RingSchedule,
    layout_label: str,
    steps: int = 80,
) -> tuple[float, ...]:
    """Search phase offsets for one tier to minimize peak frame force (NVH proxy)."""
    tier_slots = [s for s in slots if s.tier_index == tier_index]
    n_peer = len(tier_slots)
    if n_peer == 0:
        return ()
    cycle_ms = tier_slots[0].cfg.cycle_time_s * 1e3

    def _score(phases: tuple[float, ...]) -> float:
        trial = _apply_tier_phase_offsets(slots, tier_index, phases)
        nvh = analyze_nvh_proxy(
            trial, schedule=schedule, layout_label=layout_label,
        )
        return nvh.peak_resultant_n

    if n_peer == 1:
        return (tier_slots[0].phase_offset_ms,)

    if n_peer == 2:
        best_peak = float("inf")
        best_phases = (
            tier_slots[0].phase_offset_ms,
            tier_slots[1].phase_offset_ms,
        )
        for p1 in np.linspace(0.0, cycle_ms, steps):
            phases = (0.0, float(p1))
            peak = _score(phases)
            if peak < best_peak:
                best_peak = peak
                best_phases = phases
        return best_phases

    base_phases = tuple(s.phase_offset_ms for s in tier_slots)
    best_peak = float("inf")
    best_shift = 0.0
    for shift in np.linspace(0.0, cycle_ms, steps):
        shifted = tuple((p + shift) % cycle_ms for p in base_phases)
        peak = _score(shifted)
        if peak < best_peak:
            best_peak = peak
            best_shift = float(shift)
    return tuple((p + best_shift) % cycle_ms for p in base_phases)


def optimize_nvh_phase_layout(
    slots: tuple[MixedCartridgeSlot, ...],
    *,
    schedule: RingSchedule,
    layout_label: str,
    tiers: tuple[int, ...] = (2, 1, 0),
    steps: int = 80,
) -> dict[int, tuple[float, ...]]:
    """Greedy per-tier phase search to reduce peak resultant force."""
    current = slots
    offsets: dict[int, tuple[float, ...]] = {}
    for tier in tiers:
        present = any(s.tier_index == tier for s in current)
        if not present:
            continue
        phases = optimize_tier_phases_for_nvh(
            current,
            tier,
            schedule=schedule,
            layout_label=layout_label,
            steps=steps,
        )
        offsets[tier] = phases
        current = _apply_tier_phase_offsets(current, tier, phases)
    return offsets


def simulate_mixed_ring(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    cycles: int = 12,
    cooling_mode: str = "water_jacket",
    load_policy: RingLoadPolicy | None = None,
    build_options: RingBuildOptions | None = None,
) -> MixedRingResult:
    """Run all tier cartridges and aggregate bus-level KPIs + RBI + NVH proxy."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    options = build_options or DEFAULT_BUILD_OPTIONS
    if load_policy is not None:
        options = replace(options, load_policy=load_policy)
    tier_audits = audit_ring_tier_configs(
        layout, cooling_mode=cooling_mode, build_options=options,
    )
    slots = build_mixed_ring_slots(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        build_options=options,
    )
    results: list[MixedCartridgeResult] = []
    total_elec = 0.0
    total_fuel = 0.0
    balance_ok = True

    for slot in slots:
        cart, fuel_w, slot_balance = _simulate_cartridge_slot(slot, cycles=cycles)
        total_elec += cart.mean_elec_power_w
        total_fuel += fuel_w
        balance_ok = balance_ok and slot_balance
        results.append(cart)

    nvh = analyze_nvh_proxy(
        slots,
        schedule=schedule,
        layout_label=layout.label,
    )
    ring_eff = total_elec / max(total_fuel, 1e-9)
    rbi = compute_ring_balance_index(results)
    all_boundary = all(c.boundary_valid for c in results)
    return MixedRingResult(
        layout=layout,
        schedule=schedule,
        load_policy=options.load_policy,
        cartridges=tuple(results),
        tier_audits=tier_audits,
        total_elec_power_w=total_elec,
        ring_efficiency=ring_eff,
        rbi=rbi,
        nvh=nvh,
        energy_balance_valid=balance_ok,
        all_boundaries_valid=all_boundary,
    )


def compare_schedules(
    layout: MixedRingLayout | None = None,
    *,
    cycles: int = 12,
    load_policy: RingLoadPolicy | None = None,
    build_options: RingBuildOptions | None = None,
) -> tuple[MixedRingResult, ...]:
    """Run primary scheduling strategies for ring-harmony comparison."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    out: list[MixedRingResult] = []
    for schedule in (
        RingSchedule.TIER_BLOCKS,
        RingSchedule.MICRO_MEDIUM_RING,
        RingSchedule.HARMONIC_PAIRED,
        RingSchedule.INTERLEAVED,
        RingSchedule.UNIFORM_MEDIUM,
    ):
        out.append(
            simulate_mixed_ring(
                layout,
                schedule=schedule,
                cycles=cycles,
                load_policy=load_policy,
                build_options=build_options,
            )
        )
    return tuple(out)


def isolated_banks_nvh(
    layout: MixedRingLayout | None = None,
) -> NvhProxyResult:
    """RSS of per-tier peaks — models physically isolated subframes."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    peaks: list[float] = []
    for tier, count in enumerate((layout.n_micro, layout.n_medium, layout.n_large)):
        if count <= 0:
            continue
        sub_layout = MixedRingLayout(
            count if tier == 0 else 0,
            count if tier == 1 else 0,
            count if tier == 2 else 0,
        )
        slots = build_mixed_ring_slots(sub_layout, schedule=RingSchedule.TIER_BLOCKS)
        nvh = analyze_nvh_proxy(
            slots,
            schedule=RingSchedule.TIER_BLOCKS,
            layout_label=sub_layout.label,
        )
        peaks.append(nvh.peak_resultant_n)
    rss = math.sqrt(sum(p * p for p in peaks))
    return NvhProxyResult(
        peak_resultant_n=rss,
        rms_resultant_n=rss * 0.707,
        min_resultant_n=0.0,
        crest_factor=1.414,
        dominant_beat_hz=0.0,
        schedule=RingSchedule.TIER_BLOCKS,
        layout_label=f"isolated_banks({layout.label})",
    )


def print_tier_audit_report(audits: tuple[TierConfigAudit, ...]) -> None:
    print()
    print("  Tier parameter audit (geometry + full tuning vector):")
    for audit in audits:
        status = "PASS" if audit.valid else "FAIL"
        path = audit.tuning_path or "(native defaults)"
        print(
            f"    {audit.tier_name:<18}  {audit.displacement_cc:6.0f} cc  "
            f"{path}  [{status}]"
        )
        if audit.harmonic_ratio is not None:
            print(f"      Harmonic ratio (fast/large): {audit.harmonic_ratio:.3f}")
        for v in audit.violations:
            print(
                f"      VIOLATION {v.parameter}: {v.value:.4f} "
                f"not in [{v.bound_lo:.4f}, {v.bound_hi:.4f}]"
            )


def print_mixed_ring_report(result: MixedRingResult) -> None:
    print()
    print("=" * 72)
    print(
        f"ATPE MIXED RING — layout {result.layout.label}  "
        f"schedule={result.schedule.value}"
    )
    print("=" * 72)
    print(f"  Total electrical power:  {result.total_elec_power_w/1000:8.1f} kW")
    print(f"  Ring efficiency:         {result.ring_efficiency:8.1%}")
    print(
        f"  Energy balance:          "
        f"{'PASS' if result.energy_balance_valid else 'FAIL'}  |  "
        f"Boundary: {'PASS' if result.all_boundaries_valid else 'FAIL'}"
    )
    rbi = result.rbi
    tier_flag = "PASS" if rbi.meets_target else "WARN"
    cart_flag = "PASS" if rbi.meets_cartridge_target else "WARN"
    print(
        f"  RBI (tier-aggregate):  {rbi.tier_total_pct:7.2f}%  "
        f"(target < {RBI_TARGET_PCT:.0f}%)  [{tier_flag}]"
    )
    print(
        f"    fast-freq CV {rbi.tier_fast_freq_cv_pct:.2f}%  "
        f"tier-power CV {rbi.tier_power_cv_pct:.2f}%  "
        f"tier-capture CV {rbi.tier_capture_cv_pct:.2f}%  "
        f"tier-stroke CV {rbi.tier_stroke_cv_pct:.2f}%  "
        f"harmonic dev {rbi.harmonic_deviation_pct:.2f}%"
    )
    print(
        f"  RBI (per-cartridge):   {rbi.total_pct:7.2f}%  [{cart_flag}]  "
        f"(informational — penalizes multi-rate design)"
    )
    if rbi.per_tier:
        print("  RBI (within-tier cohorts):")
        for cohort in rbi.per_tier:
            flag = "PASS" if cohort.meets_target else "WARN"
            print(
                f"    {cohort.tier_name:<18}  n={cohort.cartridge_count}  "
                f"{cohort.total_pct:5.2f}% [{flag}]  "
                f"pwr {cohort.power_cv_pct:.2f}%  cap {cohort.capture_cv_pct:.2f}%  "
                f"str {cohort.stroke_cv_pct:.2f}%"
            )
    policy = result.load_policy
    print(
        f"  Load policy:             "
        f"micro×{policy.micro_multiplier:.2f}  "
        f"medium×{policy.medium_multiplier:.2f}  "
        f"large×{policy.large_multiplier:.2f}"
    )
    print_tier_audit_report(result.tier_audits)
    print()
    print("  Cartridges:")
    print(
        f"  {'#':>3}  {'Tier':<18}  {'Hz':>6}  {'load':>5}  {'phase':>7}  "
        f"{'kW':>7}  {'eta':>6}  {'cap':>6}"
    )
    for cart in result.cartridges:
        s = cart.slot
        print(
            f"  {s.index:3d}  {s.tier_name:<18}  {s.frequency_hz:6.1f}  "
            f"{s.load_fraction:5.2f}  {s.phase_offset_ms:6.2f}ms  "
            f"{cart.mean_elec_power_w/1000:6.2f}  {cart.mean_net_efficiency:5.1%}  "
            f"{cart.mean_capture_fraction:5.1%}"
        )
    n = result.nvh
    print()
    print("  NVH proxy (unbalanced reciprocating force on frame):")
    print(f"    Peak resultant:     {n.peak_resultant_n:8.0f} N")
    print(f"    RMS resultant:      {n.rms_resultant_n:8.0f} N")
    print(f"    Crest factor:       {n.crest_factor:8.2f}")
    print(f"    Dominant beat:      {n.dominant_beat_hz:8.1f} Hz")
    print("=" * 72)
    print()


def print_schedule_comparison(
    results: tuple[MixedRingResult, ...],
    *,
    layout_label: str,
) -> None:
    print()
    print("=" * 72)
    print(f"MIXED-TIER RING — SCHEDULING COMPARISON (layout {layout_label})")
    print("=" * 72)
    print(
        f"  {'Schedule':<18}  {'kW':>8}  {'eta':>7}  "
        f"{'tRBI%':>6}  {'cRBI%':>6}  {'peak N':>8}  {'beat Hz':>8}"
    )
    for r in results:
        n = r.nvh
        print(
            f"  {r.schedule.value:<18}  {r.total_elec_power_w/1000:7.1f}  "
            f"{r.ring_efficiency:6.1%}  {r.rbi.tier_total_pct:5.1f}  "
            f"{r.rbi.total_pct:5.1f}  {n.peak_resultant_n:7.0f}  "
            f"{n.dominant_beat_hz:7.1f}"
        )
    iso = isolated_banks_nvh(results[0].layout if results else None)
    print(
        f"  {'isolated_banks':<18}  {'(NVH)':>8}  {'':>7}  {'':>6}  "
        f"{iso.peak_resultant_n:7.0f}  {'0.0':>8}"
    )
    print("=" * 72)
    print()
