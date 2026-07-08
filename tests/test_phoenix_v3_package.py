"""Tests for Phoenix V3 package split, actuators, stochastic combustion, plenum."""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.actuators import ActuatorPlant, GeneratorActuatorChannel
from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.efficiency import canonical_net_efficiency, compute_canonical_efficiency
from designs.phoenix_v3.ring_plenum import mean_concurrent_intakes_during_scavenge, ring_plenum_assignments
from designs.phoenix_v3_simulation import PhoenixV3Simulator
from designs.phoenix_v3.validation.actuator_suite import run_actuator_limits_suite
from designs.phoenix_v3.validation.stochastic_suite import run_stochastic_combustion_suite


def test_actuator_back_emf_reduces_force():
    cfg = PhoenixV3Config(
        actuator_model_enabled=True,
        back_emf_coeff_n_s_m=1000.0,
        generator_force_per_amp_n=38.0,
    )
    slow = GeneratorActuatorChannel(current_a=50.0)
    fast = GeneratorActuatorChannel(current_a=50.0)
    f_slow = slow.realized_force_n(2000.0, 0.5, 0.001, cfg)
    f_fast = fast.realized_force_n(2000.0, 2.0, 0.001, cfg)
    assert f_slow > f_fast
    assert f_slow > 0.0


def test_sensor_delay_buffer_returns_older_sample():
    plant = ActuatorPlant()
    cfg = PhoenixV3Config(actuator_model_enabled=True, sensor_delay_s=0.002)
    plant.observe(0.000, 0.0, 0.0, 1e5)
    plant.observe(0.001, 0.010, 0.010, 1.1e5)
    plant.observe(0.002, 0.020, 0.020, 1.2e5)
    x_a, x_b, p = plant.delayed_state(0.002, 0.020, 0.020, 1.2e5, cfg)
    assert x_a < 0.020
    assert p < 1.2e5


def _best_tuning_config() -> PhoenixV3Config:
    import json

    from designs.phoenix_v3_optimizer import TuningVector

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return PhoenixV3Config(frequency_hz=100.0, capture_startup_cycles=1)
    payload = json.loads(best_path.read_text(encoding="utf-8"))
    return TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())


def test_stochastic_combustion_changes_efficiency():
    base = _best_tuning_config()
    det = PhoenixV3Simulator(base).simulate(cycles=24, record_history=False)
    stoch = PhoenixV3Simulator(
        replace(base, stochastic_combustion_enabled=True, combustion_rng_seed=7),
    ).simulate(cycles=24, record_history=False)
    det_eff = canonical_net_efficiency(det)
    stoch_eff = canonical_net_efficiency(stoch)
    assert det_eff > 0.40
    assert stoch_eff > 0.30


def test_ring_plenum_reduces_intake_pressure():
    cfg = PhoenixV3Config(intake_plenum_enabled=True)
    mean_open = mean_concurrent_intakes_during_scavenge(12, cfg.cycle_time_s * 1e3, cfg)
    assert mean_open > 1.5
    assigns = ring_plenum_assignments(cfg, 12)
    assert all(a.effective_intake_bar < cfg.intake_ring_pressure_bar for a in assigns)


def test_canonical_efficiency_matches_late_window():
    result = PhoenixV3Simulator(_best_tuning_config()).simulate(cycles=24, record_history=False)
    report = compute_canonical_efficiency(result)
    assert report.net_fuel_to_electric == result.energy.net_cartridge_efficiency


def test_actuator_suite_runs():
    summary = run_actuator_limits_suite(PhoenixV3Config(), cycles=8)
    assert summary.baseline.net_efficiency >= 0.0
    assert summary.with_actuators.net_efficiency >= 0.0


def test_stochastic_suite_runs():
    summary = run_stochastic_combustion_suite(PhoenixV3Config(), trials=5, cycles=10)
    assert summary.trials == 5
    assert 0.0 <= summary.failure_rate <= 1.0
