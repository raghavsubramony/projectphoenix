"""Tests for mixed-tier ATPE ring study."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_mixed_ring import (
    DEFAULT_LOAD_POLICY,
    GATE4_FROZEN_LOAD_POLICY,
    GATE4_LAYOUT,
    HARMONIC_RATIO_TARGET,
    PHASE1_LAYOUT,
    RBI_TARGET_PCT,
    UNITY_LOAD_POLICY,
    MixedRingLayout,
    RingBuildOptions,
    RingSchedule,
    analyze_nvh_proxy,
    audit_ring_tier_configs,
    build_mixed_ring_slots,
    compare_schedules,
    simulate_mixed_ring,
)
from designs.phoenix_v3.tier_presets import load_tier_tuning_config


def test_phase1_layout_has_eight_slots():
    slots = build_mixed_ring_slots(MixedRingLayout(*PHASE1_LAYOUT))
    assert len(slots) == 8
    tiers = [s.tier_index for s in slots]
    assert tiers.count(0) == 4
    assert tiers.count(1) == 2
    assert tiers.count(2) == 2


def test_gate4_layout_has_twelve_slots():
    slots = build_mixed_ring_slots(MixedRingLayout(*GATE4_LAYOUT))
    assert len(slots) == 12
    tiers = [s.tier_index for s in slots]
    assert tiers.count(0) == 4
    assert tiers.count(1) == 6
    assert tiers.count(2) == 2


def test_interleaved_spreads_tiers():
    slots = build_mixed_ring_slots(
        MixedRingLayout(*PHASE1_LAYOUT), schedule=RingSchedule.INTERLEAVED,
    )
    first_three = [s.tier_index for s in slots[:3]]
    assert len(set(first_three)) > 1


def test_harmonic_paired_interleaves_fast_bank_then_large():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    slots = build_mixed_ring_slots(layout, schedule=RingSchedule.HARMONIC_PAIRED)
    tiers = [s.tier_index for s in slots]
    assert tiers[-2:] == [2, 2]
    fast = tiers[:-2]
    assert fast.count(0) == 4
    assert fast.count(1) == 6
    assert fast.count(2) == 0
    # Fast bank alternates micro/medium until micro exhausted, then medium only.
    assert fast[:8] == [0, 1, 0, 1, 0, 1, 0, 1]
    assert fast[8:] == [1, 1]


def test_nvh_uniform_medium_lower_beat_than_mixed():
    layout = MixedRingLayout(*PHASE1_LAYOUT)
    mixed = analyze_nvh_proxy(
        build_mixed_ring_slots(layout, schedule=RingSchedule.INTERLEAVED),
        schedule=RingSchedule.INTERLEAVED,
        layout_label=layout.label,
    )
    uniform = analyze_nvh_proxy(
        build_mixed_ring_slots(layout, schedule=RingSchedule.UNIFORM_MEDIUM),
        schedule=RingSchedule.UNIFORM_MEDIUM,
        layout_label=layout.label,
    )
    assert mixed.dominant_beat_hz > uniform.dominant_beat_hz


def test_tier_blocks_lower_nvh_than_interleaved_on_gate4():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    blocks = analyze_nvh_proxy(
        build_mixed_ring_slots(layout, schedule=RingSchedule.TIER_BLOCKS),
        schedule=RingSchedule.TIER_BLOCKS,
        layout_label=layout.label,
    )
    interleaved = analyze_nvh_proxy(
        build_mixed_ring_slots(layout, schedule=RingSchedule.INTERLEAVED),
        schedule=RingSchedule.INTERLEAVED,
        layout_label=layout.label,
    )
    assert blocks.peak_resultant_n < interleaved.peak_resultant_n


def test_all_tier_configs_pass_audit_on_gate4():
    audits = audit_ring_tier_configs(MixedRingLayout(*GATE4_LAYOUT))
    assert len(audits) == 3
    for audit in audits:
        assert audit.valid, f"{audit.tier_name} violations: {audit.violations}"


def test_large_harmonic_ratio_near_two_to_one():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    audits = audit_ring_tier_configs(layout)
    large = next(a for a in audits if a.tier_index == 2)
    assert large.harmonic_ratio is not None
    assert abs(large.harmonic_ratio - HARMONIC_RATIO_TARGET) < 0.2


def test_load_policy_clamps_within_tier_bounds():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    slots = build_mixed_ring_slots(layout, load_policy=DEFAULT_LOAD_POLICY)
    for slot in slots:
        if slot.tier_index == 1:
            assert slot.load_fraction <= 0.95 + 1e-9
        if slot.tier_index == 2:
            assert slot.load_fraction < 0.90


def test_unity_load_policy_preserves_tuned_load_fraction():
    layout = MixedRingLayout(1, 1, 1)
    tuned = build_mixed_ring_slots(layout, load_policy=UNITY_LOAD_POLICY)
    for slot in tuned:
        cfg, _ = load_tier_tuning_config(slot.tier_index)
        assert abs(slot.load_fraction - cfg.load_fraction) < 1e-9


def test_micro_medium_ring_layout_counts():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    slots = build_mixed_ring_slots(layout, schedule=RingSchedule.MICRO_MEDIUM_RING)
    tiers = [s.tier_index for s in slots]
    assert tiers == [0, 1, 0, 1, 2, 1, 0, 1, 0, 1, 2, 1]
    assert tiers.count(0) == 4
    assert tiers.count(1) == 6
    assert tiers.count(2) == 2


def test_per_tier_rbi_lower_than_cartridge_rbi():
    result = simulate_mixed_ring(
        MixedRingLayout(*GATE4_LAYOUT),
        cycles=6,
        build_options=RingBuildOptions(load_policy=GATE4_FROZEN_LOAD_POLICY),
    )
    assert len(result.rbi.per_tier) == 3
    for cohort in result.rbi.per_tier:
        assert cohort.total_pct < result.rbi.total_pct
        assert cohort.meets_target


def test_rbi_computed_from_cartridge_results():
    result = simulate_mixed_ring(MixedRingLayout(2, 1, 1), cycles=4)
    rbi = result.rbi
    assert rbi.total_pct >= 0.0
    assert rbi.tier_total_pct >= 0.0
    assert rbi.frequency_cv_pct >= 0.0
    assert rbi.meets_target == (rbi.tier_total_pct < RBI_TARGET_PCT)
    assert rbi.meets_cartridge_target == (rbi.total_pct < RBI_TARGET_PCT)


def test_compare_schedules_runs_five_modes():
    results = compare_schedules(MixedRingLayout(2, 1, 1), cycles=4)
    assert len(results) == 5
    assert all(r.total_elec_power_w >= 0.0 for r in results)
    assert all(r.energy_balance_valid for r in results)


def test_gate4_simulation_matches_expected_power_band():
    """Regression guard on 4/6/2 ring KPIs (±2% power, ±0.5pp efficiency)."""
    result = simulate_mixed_ring(
        MixedRingLayout(*GATE4_LAYOUT),
        schedule=RingSchedule.TIER_BLOCKS,
        cycles=12,
        load_policy=UNITY_LOAD_POLICY,
    )
    kw = result.total_elec_power_w / 1000.0
    assert 300.0 <= kw <= 330.0, f"ring power {kw:.1f} kW outside expected band"
    assert 0.52 <= result.ring_efficiency <= 0.56
    assert result.energy_balance_valid
    assert result.all_boundaries_valid


def test_per_tier_frequency_matches_best_tuning_json():
    for tier_index in range(3):
        cfg, _ = load_tier_tuning_config(tier_index)
        slots = build_mixed_ring_slots(
            MixedRingLayout(1, 0, 0) if tier_index == 0 else (
                MixedRingLayout(0, 1, 0) if tier_index == 1 else MixedRingLayout(0, 0, 1)
            ),
            load_policy=UNITY_LOAD_POLICY,
        )
        assert abs(slots[0].frequency_hz - cfg.frequency_hz) < 1e-6


if __name__ == "__main__":
    test_phase1_layout_has_eight_slots()
    test_gate4_layout_has_twelve_slots()
    test_interleaved_spreads_tiers()
    test_harmonic_paired_interleaves_fast_bank_then_large()
    test_nvh_uniform_medium_lower_beat_than_mixed()
    test_tier_blocks_lower_nvh_than_interleaved_on_gate4()
    test_all_tier_configs_pass_audit_on_gate4()
    test_large_harmonic_ratio_near_two_to_one()
    test_load_policy_clamps_within_tier_bounds()
    test_unity_load_policy_preserves_tuned_load_fraction()
    test_micro_medium_ring_layout_counts()
    test_per_tier_rbi_lower_than_cartridge_rbi()
    test_rbi_computed_from_cartridge_results()
    test_compare_schedules_runs_five_modes()
    test_gate4_simulation_matches_expected_power_band()
    test_per_tier_frequency_matches_best_tuning_json()
    print("All mixed-ring tests passed.")
