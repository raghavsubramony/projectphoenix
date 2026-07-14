"""Tests for ATPE ring supervisor closed-loop balance."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.ring_supervisor import (
    DEFAULT_SUPERVISOR_CONFIG,
    CartridgeObservation,
    RingAverages,
    RingSupervisorConfig,
    SupervisorAdjustment,
    _apply_adjustment,
    _clamp_param,
    _compute_adjustment,
    _ring_averages,
    simulate_supervised_mixed_ring,
)
from designs.phoenix_v3_mixed_ring import GATE4_LAYOUT, MixedRingLayout, RBI_TARGET_PCT, UNITY_LOAD_POLICY
from designs.phoenix_v3.tier_presets import load_tier_tuning_config


def test_clamp_param_respects_tier_bounds():
    micro_load = _clamp_param("load_fraction", 0.99, 0)
    assert micro_load <= 0.95 + 1e-9
    large_load = _clamp_param("load_fraction", 0.50, 2)
    assert large_load >= 0.58


def test_supervisor_adjustment_under_power_increases_extraction():
    cfg, _ = load_tier_tuning_config(1)
    baseline = cfg
    obs = CartridgeObservation(
        index=0,
        tier_index=1,
        power_w=20.0,
        capture_fraction=0.50,
        stroke_mm=15.0,
        net_efficiency=0.50,
        peak_pressure_bar=90.0,
        spring_recovery=0.95,
        boundary_valid=True,
        energy_balance_valid=True,
    )
    tier_stats = RingAverages(mean_power_w=30.0, mean_capture=0.70, mean_stroke_mm=15.0)
    ring_stats = RingAverages(mean_power_w=26.0, mean_capture=0.65, mean_stroke_mm=15.5)
    adj = _compute_adjustment(
        obs,
        tier_stats,
        ring_stats,
        cfg=cfg,
        baseline=baseline,
        policy=DEFAULT_SUPERVISOR_CONFIG,
    )
    assert adj.delta_load > 0.0
    new_cfg = _apply_adjustment(cfg, baseline, adj, policy=DEFAULT_SUPERVISOR_CONFIG)
    assert new_cfg.load_fraction >= cfg.load_fraction


def test_supervisor_adjustments_stay_within_baseline_drift():
    cfg, _ = load_tier_tuning_config(0)
    baseline = cfg
    adj = SupervisorAdjustment(
        index=0,
        tier_index=0,
        delta_load=0.05,
        delta_generator_force_scale=0.2,
        delta_ignition_ms=0.2,
        delta_combustion_gain=0.1,
        power_error=0.2,
        capture_error=0.2,
        stroke_error=0.0,
    )
    new_cfg = _apply_adjustment(
        cfg, baseline, adj, policy=DEFAULT_SUPERVISOR_CONFIG,
    )
    assert new_cfg.load_fraction <= baseline.load_fraction * 1.07
    assert new_cfg.generator_force_scale <= baseline.generator_force_scale * 1.06
    assert abs(new_cfg.ignition_ms - baseline.ignition_ms) <= 0.16


def test_tier_aggregate_rbi_lower_than_cartridge_on_gate4():
    from designs.phoenix_v3_mixed_ring import simulate_mixed_ring

    result = simulate_mixed_ring(
        MixedRingLayout(*GATE4_LAYOUT),
        cycles=8,
        load_policy=UNITY_LOAD_POLICY,
    )
    assert result.rbi.tier_total_pct < result.rbi.total_pct


def test_supervised_ring_improves_tier_rbi_on_gate4():
    fast = RingSupervisorConfig(iterations=5, probe_cycles=6, final_cycles=8)
    result = simulate_supervised_mixed_ring(
        MixedRingLayout(*GATE4_LAYOUT),
        supervisor=fast,
    )
    assert result.ring.energy_balance_valid
    assert result.ring.all_boundaries_valid
    assert result.ring.total_elec_power_w > 0.0
    assert result.ring.rbi.tier_total_pct < RBI_TARGET_PCT
    assert result.ring.rbi.tier_total_pct < result.ring.rbi.total_pct


def test_supervised_ring_improves_or_maintains_rbi_on_gate4():
    fast = RingSupervisorConfig(iterations=2, probe_cycles=4, final_cycles=6)
    result = simulate_supervised_mixed_ring(
        MixedRingLayout(*GATE4_LAYOUT),
        supervisor=fast,
    )
    assert result.ring.energy_balance_valid
    assert result.ring.all_boundaries_valid
    assert result.ring.total_elec_power_w > 0.0
    assert result.tier_rbi_improvement_pct >= -1.0


def test_supervised_ring_uses_unity_load_policy():
    result = simulate_supervised_mixed_ring(
        MixedRingLayout(2, 1, 1),
        supervisor=RingSupervisorConfig(iterations=1, probe_cycles=4, final_cycles=4),
    )
    assert result.ring.load_policy == UNITY_LOAD_POLICY


def test_ring_averages_from_observations():
    obs = (
        CartridgeObservation(
            0, 0, 10.0, 0.5, 12.0, 0.5, 80.0, 0.9, True, True,
        ),
        CartridgeObservation(
            1, 1, 30.0, 0.7, 16.0, 0.5, 90.0, 0.9, True, True,
        ),
    )
    stats = _ring_averages(obs)
    assert abs(stats.mean_power_w - 20.0) < 1e-9
    assert abs(stats.mean_capture - 0.6) < 1e-9


if __name__ == "__main__":
    test_clamp_param_respects_tier_bounds()
    test_supervisor_adjustment_under_power_increases_extraction()
    test_supervisor_adjustments_stay_within_baseline_drift()
    test_tier_aggregate_rbi_lower_than_cartridge_on_gate4()
    test_supervised_ring_improves_tier_rbi_on_gate4()
    test_supervised_ring_improves_or_maintains_rbi_on_gate4()
    test_supervised_ring_uses_unity_load_policy()
    test_ring_averages_from_observations()
    print("All ring supervisor tests passed.")
