"""Tests for dynamic ring vehicle twin integration."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import build_dynamic_ring_twin
from digital_twin.atpe_ring import DynamicRingATPE, probe_gate5_ring_slots
from digital_twin.config import phase1_config, with_dynamic_ring


def test_probe_gate5_ring_slots():
    slots, caches, _ = probe_gate5_ring_slots(probe_cycles=3, fast_probe=True)
    assert len(slots) == 12
    assert len(caches) == 12
    assert sum(c.nominal_power_w for c in caches) > 200_000


def test_dynamic_ring_atpe_generate_idle():
    cfg = with_dynamic_ring(phase1_config(), probe_cycles=3, fast_probe=True).atpe
    atpe = DynamicRingATPE(cfg)
    off = atpe.generate(0.0, 1.0)
    assert off.electric_w == 0.0
    idle = atpe.generate(30_000.0, 1.0)
    assert idle.electric_w > 20_000
    assert atpe.last_active_count == 2
    assert atpe.last_dispatch_mode == "idle"


def test_dynamic_ring_atpe_track_mode():
    cfg = with_dynamic_ring(phase1_config(), probe_cycles=3, fast_probe=True).atpe
    atpe = DynamicRingATPE(cfg)
    track = atpe.generate(310_000.0, 1.0)
    assert track.electric_w > 200_000
    assert atpe.last_active_count == 12
    assert atpe.last_dispatch_mode == "track"


def test_build_dynamic_ring_twin_steps():
    twin = build_dynamic_ring_twin(probe_cycles=3, fast_probe=True)
    step = twin.step(speed_ms=25.0, accel_ms2=0.5, grade_rad=0.0, dt_s=1.0)
    assert step.generation_w >= 0.0
    assert step.dispatch_mode in ("idle", "city", "highway", "overtake", "track", "off", "")


def test_injector_fault_reduces_combustion():
    from designs.phoenix_v3.combustion import StochasticCombustionModel
    from designs.phoenix_v3.config import CycleState, PhoenixV3Config, TransientFault, ValveState

    cfg = PhoenixV3Config()
    model = StochasticCombustionModel(cfg)
    state = CycleState(
        t_s=0.0,
        x_a_m=0.0, v_a_ms=0.0, x_b_m=0.0, v_b_ms=0.0,
        pressure_pa=1e5, temperature_k=400.0, mass_kg=0.001,
        residual_mass_kg=0.0, fresh_mass_kg=0.001,
        fuel_burned_frac=0.0,
        valves=ValveState(True, True, True, True),
        stage="7_power",
        p_spring_a_pa=1e5, p_spring_b_pa=1e5,
    )
    fault = TransientFault(kind="injector_failure", trigger_cycle=1)
    _, _, _, q = model.combustion_update(
        5.0, 0.0, state, cycle_index=2, cfg=cfg, fault=fault, time_scale=1.0,
    )
    assert q == 0.0


if __name__ == "__main__":
    test_probe_gate5_ring_slots()
    test_dynamic_ring_atpe_generate_idle()
    test_dynamic_ring_atpe_track_mode()
    test_build_dynamic_ring_twin_steps()
    test_injector_fault_reduces_combustion()
    print("All dynamic ring twin tests passed.")
