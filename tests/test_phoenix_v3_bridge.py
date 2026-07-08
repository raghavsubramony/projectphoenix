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
    ref = base.atpe.tiers[1].thermal_efficiency
    assert updated.atpe.tiers[1].thermal_efficiency != ref or ref < 0.30


def test_build_phoenix_v3_twin_generates():
    path = resolve_best_tuning_path()
    if path is None:
        return
    twin = build_phoenix_v3_twin(tuning_path=str(path))
    result = twin.atpe.generate(50_000.0, 0.1)
    assert result.electric_w > 0.0
    assert result.fuel_power_w > result.electric_w
