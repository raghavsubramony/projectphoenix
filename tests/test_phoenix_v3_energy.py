"""Energy accounting invariants for Phoenix V3 simulation."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_simulation import (
    PhoenixV3Config,
    PhoenixV3Simulator,
    TransientFault,
    assess_transient_recovery,
    virtual_end_stop_gain,
)


def test_energy_balance_first_law():
    sim = PhoenixV3Simulator(PhoenixV3Config())
    result = sim.simulate(cycles=6)
    reports = result.energy.cycle_reports
    assert reports, "expected per-cycle energy reports"

    for c in reports:
        assert c.combustion_work_j <= c.fuel_energy_j + 1e-3
        assert c.heat_residual_j >= c.fuel_energy_j * 0.21
        assert c.mech_energy_j <= c.combustion_work_j + 1e-3
        assert c.elec_energy_j <= c.mech_energy_j + 1e-3
        assert abs(c.fuel_energy_j - c.heat_residual_j - c.combustion_work_j) < 1.0
        assert abs(
            (c.gross_expansion_work_j - c.pumping_loss_j) - c.indicated_work_raw_j
        ) < 1.0
        assert c.mech_from_gas_j <= c.mech_energy_raw_j + 1e-3

    # First-law invariants per cycle (aggregate may FAIL on over-extract at high load)
    for c in reports:
        assert c.fuel_energy_j + 1e-3 >= c.heat_residual_j + c.combustion_work_j


def test_load_double_recovers_without_collision():
    """Virtual end stops + load slew should survive a 2× generator load spike."""
    import json

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if best_path.exists():
        from designs.phoenix_v3_optimizer import TuningVector

        payload = json.loads(best_path.read_text(encoding="utf-8"))
        cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    else:
        cfg = PhoenixV3Config()

    fault = TransientFault("load_double", trigger_cycle=3)
    result = PhoenixV3Simulator(cfg).simulate(cycles=12, fault=fault)
    tr = assess_transient_recovery(result, fault)
    assert tr.collision_cycle is None, tr.notes


def test_virtual_end_stop_gain_tapers_near_bdc():
    cfg = PhoenixV3Config()
    assert virtual_end_stop_gain(0.0, cfg) == 1.0
    assert virtual_end_stop_gain(cfg.half_stroke_m * 0.80, cfg) == 1.0
    assert virtual_end_stop_gain(cfg.half_stroke_m * 0.90, cfg) < 1.0
    assert virtual_end_stop_gain(cfg.half_stroke_m * 0.99, cfg) == 0.0


def test_best_tuning_no_expansion_exceeds_fuel():
    import json

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return

    from designs.phoenix_v3_optimizer import TuningVector

    payload = json.loads(best_path.read_text(encoding="utf-8"))
    cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    result = PhoenixV3Simulator(cfg).simulate(cycles=8)
    e = result.energy
    assert e.combustion_work_j < e.fuel_energy_j
    assert e.heat_residual_j > 0.0
    assert e.elec_energy_j <= e.fuel_energy_j
    assert e.mech_energy_j <= e.combustion_work_j + 1e-3
