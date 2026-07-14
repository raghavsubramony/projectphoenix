"""Tests for ATPE tier geometry presets."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.tier_presets import (
    TIER_DISPLACEMENT_CC,
    TIER_FREQUENCY_HZ,
    apply_tier_geometry,
    tier_displacement_cc,
    tier_search_bound_overrides,
)
from designs.phoenix_v3_optimizer import search_bounds_for_tier


def test_tier_displacement_targets():
    assert tier_displacement_cc(0) == 100.0
    assert tier_displacement_cc(1) == 300.0
    assert tier_displacement_cc(2) == 750.0


def test_apply_tier_geometry_scales_volume():
    base = PhoenixV3Config()
    micro = apply_tier_geometry(base, 0)
    large = apply_tier_geometry(base, 2)
    micro_cc = 2.0 * micro.displacement_per_side_m3 * 1e6
    large_cc = 2.0 * large.displacement_per_side_m3 * 1e6
    assert abs(micro_cc - TIER_DISPLACEMENT_CC[0]) < 1.0
    assert abs(large_cc - TIER_DISPLACEMENT_CC[2]) < 1.0
    assert large.bore_m > micro.bore_m
    assert large.frequency_hz < micro.frequency_hz


def test_large_tier_frequency_search_allows_below_medium_floor():
    overrides = tier_search_bound_overrides(2)
    freq_lo, freq_hi = overrides["frequency_hz"]
    assert freq_lo < 42.0
    assert freq_hi > TIER_FREQUENCY_HZ[2]
    assert freq_lo <= TIER_FREQUENCY_HZ[2] <= freq_hi


def test_tier_search_bounds_scale_with_displacement():
    micro = {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(0)}
    large = {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(2)}
    assert large["air_spring_max_cc"][1] > micro["air_spring_max_cc"][1]
    assert large["generator_rated_force_n"][1] > micro["generator_rated_force_n"][1]
    assert large["damping_n_s_m"][1] > micro["damping_n_s_m"][1]


def test_medium_tier_search_bounds_match_global_defaults_for_frequency():
    medium = next(
        b for b in search_bounds_for_tier(1) if b.name == "frequency_hz"
    )
    assert medium.lo < TIER_FREQUENCY_HZ[1]
    assert medium.hi > TIER_FREQUENCY_HZ[1]


def test_tier_compression_ratio_bounds_centered_on_native():
    from designs.phoenix_v3.tier_presets import TIER_COMPRESSION_RATIO

    for idx in range(3):
        overrides = tier_search_bound_overrides(idx)
        cr_lo, cr_hi = overrides["compression_ratio"]
        native = TIER_COMPRESSION_RATIO[idx]
        assert cr_lo < native < cr_hi


def test_large_tier_allows_early_ignition_during_compression():
    overrides = tier_search_bound_overrides(2)
    ign_lo, _ = overrides["ignition_ms"]
    assert ign_lo < 4.8


def test_tier_combustion_gain_bounds_centered_on_native_geometry():
    from designs.phoenix_v3.tier_presets import native_tier_config

    for idx in range(3):
        overrides = tier_search_bound_overrides(idx)
        gain_lo, gain_hi = overrides["combustion_pressure_gain"]
        native_gain = native_tier_config(idx).combustion_pressure_gain
        assert gain_lo < native_gain < gain_hi


def test_large_tier_bounds_differ_from_medium():
    medium = {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(1)}
    large = {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(2)}
    assert large["load_fraction"][1] < medium["load_fraction"][1]
    assert large["frequency_hz"][1] < medium["frequency_hz"][1]
    assert large["generator_force_scale"][1] < medium["generator_force_scale"][1]
