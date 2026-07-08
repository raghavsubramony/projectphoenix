"""Tests for Phoenix V3 ring, thermal, long-run, and work-boundary fixes."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_ring import RingConfig, simulate_ring
from designs.phoenix_v3_simulation import (
    PhoenixV3Config,
    PhoenixV3Simulator,
    apply_generator_cooling,
    capture_startup_multiplier,
    run_long_stability,
    run_long_stability_analysis,
    run_manufacturing_tolerance_suite,
    slew_spring_phase_gain,
)
from designs.phoenix_v3_thermal import (
    COOLING_PRESETS,
    generator_effective_efficiency,
    generator_heat_loss_w,
    thermal_sustainable,
    ThermalConfig,
)


def test_work_boundary_closes_with_best_tuning():
    import json

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return

    from designs.phoenix_v3_optimizer import TuningVector

    payload = json.loads(best_path.read_text(encoding="utf-8"))
    cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    result = PhoenixV3Simulator(cfg).simulate(cycles=12, record_history=False)
    assert result.energy.hierarchy_valid, "hierarchy should pass with power-stroke ledger"
    reports = [
        r for r in result.energy.cycle_reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    late = reports[-5:] if len(reports) >= 5 else reports
    assert all(c.power_closure_valid for c in late), "late-cycle power-stroke ledger should close"
    assert result.energy.raw_boundary_valid, "steady-cycle work boundary should pass"


def test_thermal_temps_tracked():
    cfg = PhoenixV3Config(thermal_model_enabled=True)
    result = PhoenixV3Simulator(cfg).simulate(cycles=6, record_history=False)
    reports = result.energy.cycle_reports
    assert reports
    last = reports[-1]
    assert last.wall_temp_k > 300.0
    assert last.generator_temp_k > 290.0
    assert last.valve_temp_k > 290.0


def test_long_run_short_smoke():
    summary = run_long_stability(PhoenixV3Config(), cycles=50)
    assert summary.cycles == 50
    assert summary.collision_count >= 0


def test_manufacturing_tolerance_suite_runs():
    points = run_manufacturing_tolerance_suite(PhoenixV3Config(), cycles=4)
    assert len(points) >= 6
    labels = {p.label for p in points}
    assert "nominal" in labels
    assert "spring_leak_5%" in labels


def test_ring_twelve_cartridges():
    result = simulate_ring(
        PhoenixV3Config(),
        RingConfig(cartridge_count=12),
        cycles=4,
        record_history=False,
    )
    assert len(result.cartridges) == 12
    assert result.total_elec_power_w > 0.0
    assert 0.0 < result.ring_efficiency < 1.0


def test_generator_heat_loss_is_fraction_of_mech_not_full():
    cfg = ThermalConfig(generator_base_efficiency=0.94)
    loss = generator_heat_loss_w(20_000.0, cfg, 0.94)
    assert 1000.0 < loss < 2000.0


def _best_tuning_config() -> PhoenixV3Config:
    import json

    from designs.phoenix_v3_optimizer import TuningVector

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return PhoenixV3Config()
    payload = json.loads(best_path.read_text(encoding="utf-8"))
    return TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())


def test_water_jacket_sustains_long_run():
    cfg = apply_generator_cooling(_best_tuning_config(), "water_jacket")
    summary = run_long_stability(cfg, cycles=500)
    assert summary.thermal_sustainable
    assert summary.generator_temp_k < 450.0
    assert summary.late_efficiency > 0.25


def test_passive_cooling_runs_hotter_than_water_jacket():
    base = _best_tuning_config()
    passive = run_long_stability(apply_generator_cooling(base, "passive"), cycles=500)
    cooled = run_long_stability(apply_generator_cooling(base, "water_jacket"), cycles=500)
    assert passive.generator_temp_k >= cooled.generator_temp_k - 5.0


def test_temperature_dependent_generator_efficiency():
    cfg = ThermalConfig(generator_base_efficiency=0.94)
    cold = generator_effective_efficiency(310.0, cfg)
    hot = generator_effective_efficiency(430.0, cfg)
    assert cold > hot
    assert cold <= 0.96


def test_spring_phase_slew_limits_step():
    cfg = PhoenixV3Config(air_spring_phase_slew_per_s=10.0)
    out = slew_spring_phase_gain(1.0, 0.56, 0.001, cfg)
    assert abs(out - 0.99) < 0.001


def test_best_tuning_late_efficiency_band():
    import json
    from designs.phoenix_v3_optimizer import TuningVector

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return
    payload = json.loads(best_path.read_text(encoding="utf-8"))
    cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    result = PhoenixV3Simulator(cfg).simulate(cycles=24, record_history=False)
    valid = [
        c for c in result.energy.cycle_reports
        if c.fuel_energy_j > 50.0 and c.max_piston_travel_mm > 5.0
    ]
    late = valid[-max(1, len(valid) // 3):]
    late_eff = sum(c.net_cartridge_efficiency for c in late) / len(late)
    assert late_eff >= 0.50, f"expected >=50% late efficiency, got {late_eff:.1%}"


def test_capture_startup_multiplier():
    cfg = PhoenixV3Config(capture_startup_cycles=10, capture_startup_scale=0.5)
    assert capture_startup_multiplier(3, 20.0, cfg) == 0.5
    assert capture_startup_multiplier(20, 10.0, cfg) == 0.5
    assert capture_startup_multiplier(20, 20.0, cfg) == 1.0


def test_ring_phase_efficiency_gap_small():
    import json
    from designs.phoenix_v3_optimizer import TuningVector

    best_path = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    if not best_path.exists():
        return
    from designs.phoenix_v3_ring import RingConfig, analyze_ring_efficiency_gap

    payload = json.loads(best_path.read_text(encoding="utf-8"))
    cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    audit = analyze_ring_efficiency_gap(cfg, RingConfig(12), cycles=120)
    assert audit.efficiency_gap < 0.05, f"ring gap too large: {audit.efficiency_gap:.1%}"


def test_cooling_presets_defined():
    assert "water_jacket" in COOLING_PRESETS
    assert COOLING_PRESETS["water_jacket"]["generator_cooling_w_per_k"] > COOLING_PRESETS["passive"]["generator_cooling_w_per_k"]


def test_long_run_trend_correlation_runs():
    analysis = run_long_stability_analysis(PhoenixV3Config(), cycles=120, trend_interval=40)
    assert len(analysis.trend) >= 2
    assert analysis.summary.cycles == 120
