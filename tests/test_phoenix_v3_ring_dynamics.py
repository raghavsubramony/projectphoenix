"""Tests for V3.1 ring dynamics (harmonic lock, phase NVH, load policy)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_mixed_ring import (
    DEFAULT_RING_SCHEDULE,
    GATE4_LAYOUT,
    GATE4_PRODUCTION_LOAD_POLICY,
    HARMONIC_RATIO_TARGET,
    MixedRingLayout,
    RingBuildOptions,
    UNITY_LOAD_POLICY,
    V31_LOAD_POLICY,
    _apply_tier_phase_offsets,
    _tier_cohort_rbi_from_slots,
    analyze_nvh_proxy,
    build_gate4_production_options,
    build_mixed_ring_slots,
    harmonic_target_large_hz,
    optimize_tier_phases_for_capture_uniformity,
    optimize_tier_phases_for_nvh,
    simulate_mixed_ring,
    _load_tier_configs,
)
from scripts.run_v31_ring_dynamics import build_v31_options


def test_harmonic_lock_sets_large_near_fast_over_two():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    base_cfgs, _ = _load_tier_configs(
        layout, cooling_mode="water_jacket",
        build_options=RingBuildOptions(load_policy=UNITY_LOAD_POLICY),
    )
    target = harmonic_target_large_hz(base_cfgs, layout)
    locked_cfgs, _ = _load_tier_configs(
        layout,
        cooling_mode="water_jacket",
        build_options=RingBuildOptions(
            load_policy=UNITY_LOAD_POLICY,
            harmonic_lock_large=True,
        ),
    )
    assert abs(locked_cfgs[2].frequency_hz - target) < 0.01
    fast = (
        base_cfgs[0].frequency_hz * 4 + base_cfgs[1].frequency_hz * 6
    ) / 10.0
    ratio = fast / locked_cfgs[2].frequency_hz
    assert abs(ratio - HARMONIC_RATIO_TARGET) < 0.02


def test_v31_load_policy_clamps_within_bounds():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    slots = build_mixed_ring_slots(
        layout, build_options=RingBuildOptions(load_policy=V31_LOAD_POLICY),
    )
    for slot in slots:
        if slot.tier_index == 0:
            assert slot.load_fraction <= 0.95 + 1e-9
        if slot.tier_index == 1:
            assert slot.load_fraction <= 0.95 + 1e-9
        if slot.tier_index == 2:
            assert 0.58 <= slot.load_fraction <= 0.90


def test_large_phase_optimization_reduces_or_matches_peak_force():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    slots = build_mixed_ring_slots(layout, schedule=DEFAULT_RING_SCHEDULE)
    before = analyze_nvh_proxy(
        slots, schedule=DEFAULT_RING_SCHEDULE, layout_label=layout.label,
    )
    phases = optimize_tier_phases_for_nvh(
        slots, 2, schedule=DEFAULT_RING_SCHEDULE, layout_label=layout.label, steps=40,
    )
    after_slots = _apply_tier_phase_offsets(slots, 2, phases)
    after = analyze_nvh_proxy(
        after_slots, schedule=DEFAULT_RING_SCHEDULE, layout_label=layout.label,
    )
    assert after.peak_resultant_n <= before.peak_resultant_n + 1.0


def test_gate4_production_options_meets_approved_targets():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    options = build_gate4_production_options(layout, phase_steps=20)
    result = simulate_mixed_ring(layout, cycles=6, build_options=options)
    assert result.energy_balance_valid
    assert result.all_boundaries_valid
    assert result.ring_efficiency >= 0.54
    assert result.total_elec_power_w >= 300_000
    assert result.rbi.tier_total_pct < 5.0
    assert options.load_policy is GATE4_PRODUCTION_LOAD_POLICY


def test_medium_capture_phase_optimization_runs():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    base = build_gate4_production_options(layout, phase_steps=15)
    slots = build_mixed_ring_slots(layout, build_options=base)
    baseline_peak = analyze_nvh_proxy(
        slots, schedule=DEFAULT_RING_SCHEDULE, layout_label=layout.label,
    ).peak_resultant_n
    phases = optimize_tier_phases_for_capture_uniformity(
        slots,
        1,
        cycles=4,
        steps=12,
        passes=1,
        schedule=DEFAULT_RING_SCHEDULE,
        layout_label=layout.label,
        baseline_peak_force_n=baseline_peak,
    )
    assert len(phases) == 6
    after_slots = _apply_tier_phase_offsets(slots, 1, phases)
    after = _tier_cohort_rbi_from_slots(after_slots, 1, cycles=4)
    assert after is not None
    after_nvh = analyze_nvh_proxy(
        after_slots, schedule=DEFAULT_RING_SCHEDULE, layout_label=layout.label,
    )
    assert after_nvh.peak_resultant_n <= baseline_peak + 30.0


def test_v31_recommended_scenario_runs_clean():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    options = build_v31_options(layout, harmonic_lock=False, phase_steps=20)
    result = simulate_mixed_ring(layout, cycles=6, build_options=options)
    assert result.energy_balance_valid
    assert result.all_boundaries_valid
    assert result.rbi.tier_total_pct < 5.0
    assert result.ring_efficiency >= 0.53


if __name__ == "__main__":
    test_harmonic_lock_sets_large_near_fast_over_two()
    test_v31_load_policy_clamps_within_bounds()
    test_large_phase_optimization_reduces_or_matches_peak_force()
    test_gate4_production_options_meets_approved_targets()
    test_medium_capture_phase_optimization_runs()
    test_v31_recommended_scenario_runs_clean()
    print("All V3.1 dynamics tests passed.")
