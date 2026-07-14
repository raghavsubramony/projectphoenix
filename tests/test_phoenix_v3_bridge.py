"""Tests for Phoenix V3 → vehicle twin bridge."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import build_phoenix_v3_twin, phase1_config
from digital_twin.config import with_phoenix_v3
from digital_twin.phoenix_v3_bridge import (
    calibrate_all_v3_tiers,
    calibrate_v3_tier,
    measure_v3_cartridge,
    resolve_best_tuning_path,
)


def test_resolve_best_tuning_prefers_v3_when_present():
    v3 = _REPO / "designs" / "phoenix_v3_best_tuning_v3.json"
    v2 = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if v3.is_file():
        assert resolve_best_tuning_path() == v3
    elif v2.is_file():
        assert resolve_best_tuning_path() == v2


def test_measure_v3_cartridge_runs():
    path = resolve_best_tuning_path()
    if path is None:
        return
    metrics = measure_v3_cartridge(cycles=8, tuning_path=path)
    assert metrics.net_efficiency > 0.20
    assert metrics.elec_power_w > 0.0
    assert metrics.energy_balance_valid


def test_with_phoenix_v3_updates_tiers():
    path = resolve_best_tuning_path()
    if path is None:
        return
    base = phase1_config()
    updated = with_phoenix_v3(base, tuning_path=str(path), v3_cycles=8)
    assert updated.atpe.gate1 is not None
    assert updated.atpe.gate1.use_phoenix_v3
    assert len(updated.atpe.gate1.v3_tier_curves) == 3
    for i, tier in enumerate(updated.atpe.tiers):
        curve = updated.atpe.gate1.v3_tier_curves[i]
        assert curve.tier_index == i
        assert len(curve.points) >= 4
        assert 0.10 < tier.thermal_efficiency <= 0.58


def test_calibrate_v3_tiers_differ():
    path = resolve_best_tuning_path()
    if path is None:
        return
    curves = calibrate_all_v3_tiers(tuning_path=path, cycles=6, tier_count=3)
    assert len(curves) == 3
    etas = [c.efficiency_at(0.75) for c in curves]
    assert all(e > 0.15 for e in etas)
    # Micro and large should not be identical to medium at same load.
    assert etas[0] != etas[1] or etas[1] != etas[2]


def test_phoenix_v3_gate1_point_uses_tier_curve():
    path = resolve_best_tuning_path()
    if path is None:
        return
    from digital_twin.phoenix_v3_bridge import phoenix_v3_gate1_point

    curve = calibrate_v3_tier(1, tuning_path=path, cycles=6)
    pt = phoenix_v3_gate1_point(
        0.75,
        tier_index=1,
        tier_curves=(curve,),
    )
    assert abs(pt.electric_efficiency - curve.efficiency_at(0.75)) < 1e-6


def test_build_phoenix_v3_twin_generates():
    path = resolve_best_tuning_path()
    if path is None:
        return
    twin = build_phoenix_v3_twin(tuning_path=str(path))
    result = twin.atpe.generate(50_000.0, 0.1)
    assert result.electric_w > 0.0
    assert result.fuel_power_w > result.electric_w
