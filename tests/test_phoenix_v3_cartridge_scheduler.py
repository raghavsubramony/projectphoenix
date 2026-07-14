"""Tests for dynamic cartridge scheduler (Gate-5 production freeze)."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.cartridge_scheduler import (
    DispatchMode,
    build_gate5_production_options,
    dispatch_mode_for_demand,
    inject_cartridge_fault,
    initial_ring_states,
    schedule_for_demand,
    simulate_dynamic_ring_step,
)
from designs.phoenix_v3.cartridge_state import initial_ring_states
from designs.phoenix_v3_mixed_ring import GATE4_LAYOUT, MixedRingLayout, build_mixed_ring_slots


def test_dispatch_mode_ladder():
    assert dispatch_mode_for_demand(25_000) == DispatchMode.IDLE
    assert dispatch_mode_for_demand(90_000) == DispatchMode.CITY
    assert dispatch_mode_for_demand(150_000) == DispatchMode.HIGHWAY
    assert dispatch_mode_for_demand(250_000) == DispatchMode.OVERTAKE
    assert dispatch_mode_for_demand(310_000) == DispatchMode.TRACK


def test_idle_enables_two_micro():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    opts = build_gate5_production_options(
        layout, capture_steps=10, capture_passes=1, phase_steps=10, cycles=4,
    )
    slots = build_mixed_ring_slots(layout, build_options=opts)
    states = initial_ring_states(slots)
    decision = schedule_for_demand(30_000.0, slots, states)
    assert decision.mode == DispatchMode.IDLE
    assert len(decision.enabled_indices) == 2
    assert all(states[i].tier_index == 0 for i in decision.enabled_indices)


def test_track_enables_all_twelve():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    opts = build_gate5_production_options(
        layout, capture_steps=10, capture_passes=1, phase_steps=10, cycles=4,
    )
    slots = build_mixed_ring_slots(layout, build_options=opts)
    states = initial_ring_states(slots)
    decision = schedule_for_demand(310_000.0, slots, states)
    assert decision.mode == DispatchMode.TRACK
    assert len(decision.enabled_indices) == 12


def test_fault_isolation_reduces_active_count():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    opts = build_gate5_production_options(
        layout, capture_steps=10, capture_passes=1, phase_steps=10, cycles=4,
    )
    slots = build_mixed_ring_slots(layout, build_options=opts)
    states = inject_cartridge_fault(initial_ring_states(slots), 4, "cartridge_isolation")
    decision = schedule_for_demand(310_000.0, slots, states)
    assert 4 not in decision.enabled_indices
    assert len(decision.enabled_indices) == 11


def test_dynamic_step_produces_power():
    layout = MixedRingLayout(*GATE4_LAYOUT)
    opts = build_gate5_production_options(
        layout, capture_steps=10, capture_passes=1, phase_steps=10, cycles=4,
    )
    slots = build_mixed_ring_slots(layout, build_options=opts)
    states = initial_ring_states(slots)
    step = simulate_dynamic_ring_step(slots, states, 150_000.0, cycles=4)
    assert step.active_count >= 4
    assert step.total_elec_power_w > 50_000
    assert step.ring_efficiency > 0.45
    assert len(step.health) == step.active_count


if __name__ == "__main__":
    test_dispatch_mode_ladder()
    test_idle_enables_two_micro()
    test_track_enables_all_twelve()
    test_fault_isolation_reduces_active_count()
    test_dynamic_step_produces_power()
    print("All cartridge scheduler tests passed.")
