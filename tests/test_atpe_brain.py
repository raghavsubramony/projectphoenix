"""Tests for ATPE Brain supervisor package."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from atpe_brain import (
    ATPESupervisor,
    BufferTelemetry,
    CartridgeProbe,
    DigitalTwin,
    PcmritmsCoordinator,
    RingContext,
    StateEstimator,
)
from designs.phoenix_v3.cartridge_scheduler import DispatchMode, inject_cartridge_fault
from designs.phoenix_v3.cartridge_state import initial_ring_states
from designs.phoenix_v3_mixed_ring import GATE4_LAYOUT, MixedRingLayout, build_mixed_ring_slots
from designs.phoenix_v3.cartridge_scheduler import build_gate5_production_options
from digital_twin.atpe_ring import DynamicRingATPE, probe_gate5_ring_slots
from digital_twin.config import phase1_config, with_dynamic_ring


def _minimal_ring() -> RingContext:
    layout = MixedRingLayout(*GATE4_LAYOUT)
    opts = build_gate5_production_options(
        layout, capture_steps=10, capture_passes=1, phase_steps=10, cycles=2,
    )
    slots = build_mixed_ring_slots(layout, build_options=opts)
    probes = tuple(
        CartridgeProbe(
            index=s.index,
            tier_index=s.tier_index,
            tier_name=s.tier_name,
            nominal_power_w=27_000.0,
            nominal_efficiency=0.54,
            wall_temp_k=450.0,
            generator_temp_k=400.0,
            valve_temp_k=430.0,
            generator_derate=1.0,
            spring_recovery=0.95,
        )
        for s in slots
    )
    return RingContext(
        slots=slots,
        probes=probes,
        states=initial_ring_states(slots),
    )


def test_digital_twin_predict_horizon():
    ring = _minimal_ring()
    est = StateEstimator().update(ring, demand_w=90_000.0)
    pred = DigitalTwin().predict(est, horizon_cycles=50)
    assert pred.horizon_cycles == 50
    assert len(pred.forecasts) == 51
    assert pred.mean_efficiency > 0.4
    assert pred.at(0) is not None
    assert pred.at(50) is not None


def test_supervisor_idle_dispatch():
    ring = _minimal_ring()
    sup = ATPESupervisor(ring)
    result = sup.cycle(30_000.0, 1.0)
    assert result.commands.mode == DispatchMode.IDLE
    assert len(result.commands.enabled_indices) == 2
    assert result.commands.score.total > 0.0
    assert result.prediction.horizon_cycles == 50


def test_supervisor_track_dispatch():
    ring = _minimal_ring()
    sup = ATPESupervisor(ring)
    result = sup.cycle(310_000.0, 1.0)
    assert result.commands.mode == DispatchMode.TRACK
    assert len(result.commands.enabled_indices) == 12


def test_supervisor_fault_isolation():
    ring = _minimal_ring()
    states = inject_cartridge_fault(ring.states, 5, "cartridge_isolation")
    ring.states = states
    sup = ATPESupervisor(ring)
    result = sup.cycle(250_000.0, 1.0)
    assert 5 not in result.commands.enabled_indices
    assert len(result.commands.enabled_indices) >= 9


def test_dynamic_ring_uses_supervisor():
    cfg = with_dynamic_ring(phase1_config(), probe_cycles=3, fast_probe=True).atpe
    atpe = DynamicRingATPE(cfg)
    assert atpe.supervisor is not None
    track = atpe.generate(310_000.0, 1.0)
    assert track.electric_w > 200_000
    assert atpe.last_optimization_score > 0.0


def test_pcmritms_assist_uses_slow_setpoint_residual():
    """Spike-floored setpoint still yields assist vs filtered slow reference."""
    coord = PcmritmsCoordinator()
    buf = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    plan = coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=200_000.0,  # spike floor
        buffer=buf,
        slow_setpoint_w=80_000.0,  # filter lag
    )
    assert plan.assist_event is True
    assert plan.buffer_assist_w > 0.0
    assert plan.engine_demand_w < 200_000.0
    assert plan.surge_event is True  # residual 120 kW > continuous 90 kW


def test_pcmritms_assist_reduces_engine_demand():
    coord = PcmritmsCoordinator()
    buf = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
        soc_target=0.70,
    )
    # Residual spike: slow setpoint trails bus → assist owns the difference.
    plan = coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=100_000.0,
        buffer=buf,
    )
    assert plan.buffer_assist_w > 0.0
    assert plan.engine_demand_w < 100_000.0
    assert plan.assist_event is True


def test_pcmritms_pass_through_when_smooth_and_soc_healthy():
    """Benefit-seeking: no assist/precharge on highway-like demand."""
    coord = PcmritmsCoordinator()
    buf = BufferTelemetry(
        soc=0.85,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    plan = coord.solve(
        bus_demand_w=80_000.0,
        engine_setpoint_w=80_000.0,
        buffer=buf,
        slow_setpoint_w=80_000.0,
    )
    assert plan.buffer_assist_w == 0.0
    assert plan.precharge_w == 0.0
    assert plan.engine_demand_w == 80_000.0


def test_pcmritms_precharge_when_soc_low():
    """Post-assist recovery may precharge while SoC is below target."""
    coord = PcmritmsCoordinator(
        post_assist_precharge_cooldown_s=0.0,
        post_spike_precharge_window_s=30.0,
    )
    ready = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=200_000.0,
        buffer=ready,
        slow_setpoint_w=80_000.0,
        dt_s=1.0,
    )
    buf = BufferTelemetry(
        soc=0.32,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    plan = coord.solve(
        bus_demand_w=80_000.0,
        engine_setpoint_w=80_000.0,
        buffer=buf,
        dt_s=1.0,
    )
    assert plan.precharge_w > 0.0
    assert plan.precharge_event is True


def test_pcmritms_tow_like_no_opportunistic_precharge():
    """Steady grade without assist must not raise engine for precharge."""
    coord = PcmritmsCoordinator()
    buf = BufferTelemetry(
        soc=0.0,  # empty buffer still must not brain-precharge without assist
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    plan = coord.solve(
        bus_demand_w=120_000.0,
        engine_setpoint_w=120_000.0,
        buffer=buf,
        slow_setpoint_w=120_000.0,
        dt_s=1.0,
    )
    assert plan.precharge_event is False
    assert plan.assist_event is False


def test_pcmritms_steady_load_suppresses_precharge():
    coord = PcmritmsCoordinator(
        steady_load_suppress_s=5.0,
        post_assist_precharge_cooldown_s=0.0,
        post_spike_precharge_window_s=3.0,
    )
    ready = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=200_000.0,
        buffer=ready,
        slow_setpoint_w=80_000.0,
        dt_s=1.0,
    )
    low = BufferTelemetry(
        soc=0.32,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    for _ in range(6):
        plan = coord.solve(
            bus_demand_w=80_000.0,
            engine_setpoint_w=80_000.0,
            buffer=low,
            slow_setpoint_w=80_000.0,
            dt_s=1.0,
        )
    assert plan.precharge_event is False


def test_pcmritms_precharge_cooldown_after_assist():
    coord = PcmritmsCoordinator(
        post_assist_precharge_cooldown_s=5.0,
        steady_load_suppress_s=60.0,
        post_spike_precharge_window_s=60.0,
        emergency_soc=0.28,
    )
    ready = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=200_000.0,
        buffer=ready,
        slow_setpoint_w=80_000.0,
        dt_s=1.0,
    )
    low = BufferTelemetry(
        soc=0.32,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    blocked = coord.solve(
        bus_demand_w=80_000.0,
        engine_setpoint_w=80_000.0,
        buffer=low,
        dt_s=1.0,
    )
    assert blocked.precharge_event is False
    assert blocked.precharge_blocked_cooldown is True
    for _ in range(6):
        coord.solve(
            bus_demand_w=80_000.0,
            engine_setpoint_w=80_000.0,
            buffer=low,
            dt_s=1.0,
        )
    after = coord.solve(
        bus_demand_w=80_000.0,
        engine_setpoint_w=80_000.0,
        buffer=low,
        dt_s=1.0,
    )
    assert after.precharge_event is True


def test_supervisor_with_pcmritms_telemetry():
    ring = _minimal_ring()
    buf = BufferTelemetry(
        soc=0.95,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    bare = ATPESupervisor(ring).cycle(100_000.0, 1.0, bus_demand_w=200_000.0)
    with_buf = ATPESupervisor(ring).cycle(
        100_000.0,
        1.0,
        bus_demand_w=200_000.0,
        buffer=buf,
    )
    assert with_buf.buffer_plan.buffer_assist_w > 0.0
    assert with_buf.commands.demand_w < bare.commands.demand_w


def test_dynamic_ring_accepts_buffer_telemetry():
    cfg = with_dynamic_ring(phase1_config(), probe_cycles=3, fast_probe=True).atpe
    atpe = DynamicRingATPE(cfg)
    # Force assist then recovery precharge via supervisor path.
    buf_hi = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    atpe.generate(200_000.0, 1.0, bus_demand_w=200_000.0, buffer=buf_hi, slow_setpoint_w=80_000.0)
    # Clear cooldown by cycling coordinator state for test: zero cooldown on fresh call path
    atpe.supervisor.pcmritms._precharge_cooldown_s = 0.0
    buf = BufferTelemetry(
        soc=0.32,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
        soc_target=0.70,
    )
    gen = atpe.generate(
        100_000.0,
        1.0,
        bus_demand_w=100_000.0,
        buffer=buf,
        slow_setpoint_w=100_000.0,
    )
    assert gen.electric_w > 0.0
    assert atpe.last_buffer_precharge_w > 0.0
    assert atpe.last_buffer_burst_w is not None


def test_pcmritms_closed_loop_reduces_assist_after_under_delivery():
    coord = PcmritmsCoordinator(closed_loop_gain=1.0, closed_loop_alpha=1.0)
    buf = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
        measured_buffer_w=0.0,
        closed_loop=True,
    )
    first = coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=100_000.0,
        buffer=buf,
    )
    assert first.buffer_assist_w > 0.0
    under = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
        measured_buffer_w=10_000.0,
        closed_loop=True,
    )
    second = coord.solve(
        bus_demand_w=200_000.0,
        engine_setpoint_w=100_000.0,
        buffer=under,
    )
    assert second.assist_error_w > 0.0
    assert second.buffer_assist_w < first.buffer_assist_w


def test_multi_horizon_bundle():
    ring = _minimal_ring()
    est = StateEstimator().update(ring, demand_w=90_000.0)
    multi = DigitalTwin().predict_multi_horizon(est)
    assert multi.control.horizon_cycles == 50
    assert multi.thermal.horizon_cycles == 500
    assert multi.health.horizon_cycles == 5000
    assert multi.health.peak_thermal_w >= multi.control.peak_thermal_w


def test_dynamic_ring_pcmritms_toggle_off():
    twin_cfg = with_dynamic_ring(
        phase1_config(),
        probe_cycles=3,
        fast_probe=True,
        pcmritms_brain_enabled=False,
    )
    atpe = DynamicRingATPE(twin_cfg.atpe)
    buf = BufferTelemetry(
        soc=0.90,
        max_discharge_w=90_000.0,
        max_charge_w=90_000.0,
        peak_transient_w=140_000.0,
    )
    atpe.generate(100_000.0, 1.0, bus_demand_w=200_000.0, buffer=buf)
    assert atpe.last_buffer_assist_w == 0.0


def test_physics_twin_fidelity():
    from atpe_brain import PhysicsTwinBackend, ProbeTwinBackend

    ring = _minimal_ring()
    est = StateEstimator().update(ring, demand_w=90_000.0, buffer_soc=0.8)
    probe = ProbeTwinBackend().predict(est, horizon_cycles=200, load_fraction=1.0)
    backend = PhysicsTwinBackend()
    phys = backend.predict(est, horizon_cycles=200, load_fraction=1.0)
    assert backend.fidelity == "physics_lumped"
    assert probe.horizon_cycles == phys.horizon_cycles
    assert phys.peak_thermal_w > 0.0
    assert phys.max_fault_risk_pct >= 0.0


def test_rotor_phaser_improves_coherence():
    from atpe_brain import ClosedLoopRotorPhaser
    from dataclasses import replace
    from digital_twin.pcmritms_rotor import RotorSet

    phaser = ClosedLoopRotorPhaser(kp=0.5, max_step_rad=0.2)
    # Start badly phased.
    phaser._rotors = replace(RotorSet(), phases_rad=(0.0, 0.1, 0.2))
    start = phaser.coherence()
    for _ in range(40):
        state = phaser.step(0.05)
    assert state.coherence > start
    assert 0.0 < state.surge_scale <= 1.0


def test_health_aware_assist_lowers_spike_threshold():
    coord = PcmritmsCoordinator(spike_assist_threshold_w=25_000.0)
    buf = BufferTelemetry(
        soc=0.80,
        max_discharge_w=80_000.0,
        max_charge_w=80_000.0,
        peak_transient_w=120_000.0,
        closed_loop=False,
    )
    healthy = coord.solve(
        bus_demand_w=100_000.0,
        engine_setpoint_w=60_000.0,
        buffer=buf,
        slow_setpoint_w=60_000.0,
        min_health_pct=96.0,
    )
    # Residual 40 kW > 25 kW → assist; reset and try borderline residual with health protect.
    coord.reset()
    mild = coord.solve(
        bus_demand_w=78_000.0,
        engine_setpoint_w=60_000.0,
        buffer=buf,
        slow_setpoint_w=60_000.0,
        min_health_pct=96.0,
    )
    coord.reset()
    protect = coord.solve(
        bus_demand_w=78_000.0,
        engine_setpoint_w=60_000.0,
        buffer=buf,
        slow_setpoint_w=60_000.0,
        min_health_pct=85.0,
    )
    assert mild.buffer_assist_w == 0.0  # 18 kW residual < 25 kW
    assert protect.buffer_assist_w > 0.0  # threshold scaled to 17.5 kW
    assert "health-protect" in protect.notes
    assert healthy.buffer_assist_w > 0.0


def test_fleet_weight_adapter_raises_thermal():
    from atpe_brain import FleetSample, FleetWeightAdapter, OptimizationScore, OptimizerWeights

    adapter = FleetWeightAdapter(OptimizerWeights(), learning_rate=0.1)
    base_thermal = adapter.weights.thermal_margin
    for _ in range(5):
        adapter.observe(
            FleetSample(
                score=OptimizationScore(0.5, 0.8, 0.8, 0.3, 0.7, 0.8),
                mode="thermal",
            )
        )
    assert adapter.weights.thermal_margin > base_thermal


def test_hil_vv_001_stub():
    from atpe_brain import DeterministicEcuStub, HilHarness
    from atpe_brain.hil import synthetic_commands

    h = HilHarness(DeterministicEcuStub(latency_s=0.02))
    r = h.step(synthetic_commands(90_000.0))
    assert r.pass_
    h.ecu.watchdog_ok = False
    r2 = h.step(synthetic_commands(120_000.0))
    assert not r2.pass_
    assert r2.ack.applied_indices == ()
    assert r2.ack.applied_mode == DispatchMode.OFF


def test_hil_fault_suite_real_world_cases():
    from atpe_brain.hil import run_hil_fault_suite

    results = run_hil_fault_suite()
    assert len(results) == 3
    failed = [r for r in results if not r.pass_]
    assert not failed, "; ".join(f"{r.name}:{r.detail}" for r in failed)


def test_supervisor_coolant_heat_is_in_watts():
    """advance_coolant_bus expects watts; supervisor must not pre-multiply by dt."""
    ring = _minimal_ring()
    t0 = ring.coolant.temp_k
    sup = ATPESupervisor(ring)
    dt = 0.1
    result = sup.cycle(120_000.0, dt)
    assert result.commands.enabled_indices
    heat_w = 0.0
    for idx in result.commands.enabled_indices:
        scale = result.commands.load_scales.get(idx, 1.0)
        heat_w += (27_000.0 * scale / 27_000.0) * 8000.0
    cfg0 = ring.slots[0].cfg
    cool_out = cfg0.coolant_radiator_w_per_k * (t0 - cfg0.ambient_temp_k)
    expected = max(
        cfg0.ambient_temp_k,
        t0 + (heat_w - cool_out) * dt / max(cfg0.coolant_thermal_mass_j_per_k, 1.0),
    )
    assert abs(ring.coolant.temp_k - expected) < 1e-6
    buggy = max(
        cfg0.ambient_temp_k,
        t0
        + (heat_w * dt - cool_out) * dt / max(cfg0.coolant_thermal_mass_j_per_k, 1.0),
    )
    assert abs(ring.coolant.temp_k - buggy) > 1e-3


if __name__ == "__main__":
    test_digital_twin_predict_horizon()
    test_supervisor_idle_dispatch()
    test_supervisor_track_dispatch()
    test_supervisor_fault_isolation()
    test_dynamic_ring_uses_supervisor()
    test_pcmritms_assist_reduces_engine_demand()
    test_pcmritms_assist_uses_slow_setpoint_residual()
    test_pcmritms_pass_through_when_smooth_and_soc_healthy()
    test_pcmritms_precharge_when_soc_low()
    test_pcmritms_tow_like_no_opportunistic_precharge()
    test_pcmritms_steady_load_suppresses_precharge()
    test_pcmritms_precharge_cooldown_after_assist()
    test_supervisor_with_pcmritms_telemetry()
    test_dynamic_ring_accepts_buffer_telemetry()
    test_pcmritms_closed_loop_reduces_assist_after_under_delivery()
    test_multi_horizon_bundle()
    test_dynamic_ring_pcmritms_toggle_off()
    test_physics_twin_fidelity()
    test_rotor_phaser_improves_coherence()
    test_health_aware_assist_lowers_spike_threshold()
    test_fleet_weight_adapter_raises_thermal()
    test_hil_vv_001_stub()
    test_hil_fault_suite_real_world_cases()
    test_supervisor_coolant_heat_is_in_watts()
    print("All ATPE Brain tests passed.")
