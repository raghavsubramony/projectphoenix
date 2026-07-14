"""Dynamic cartridge scheduler — demand-driven enablement on frozen Gate-5 ring.

Schedules by bus power demand (distributed power plant), not cartridge geometry.
Builds on Gate-5 production freeze: medium-heavy load + NVH + medium capture phases.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

import numpy as np

from designs.phoenix_v3.cartridge_state import (
    CartridgeRuntimeState,
    ISOLATING_FAULTS,
    initial_ring_states,
    update_state_from_telemetry,
)
from designs.phoenix_v3.config import TransientFault
from designs.phoenix_v3.health_score import (
    CartridgeHealthScore,
    apply_health_to_states,
    compute_cartridge_health,
)
from designs.phoenix_v3_mixed_ring import (
    DEFAULT_RING_SCHEDULE,
    GATE4_LAYOUT,
    GATE5_PRODUCTION_LOAD_POLICY,
    MixedCartridgeResult,
    MixedCartridgeSlot,
    MixedRingLayout,
    MixedRingResult,
    RingBuildOptions,
    RingSchedule,
    analyze_nvh_proxy,
    build_mixed_ring_slots,
    optimize_gate5_medium_phases,
)
from designs.phoenix_v3_thermal import CoolantBus, advance_coolant_bus, estimate_cartridge_heat_w

# Nominal electrical output per tier (Gate-5 production probe, watts).
TIER_NOMINAL_POWER_W: tuple[float, float, float] = (27_700.0, 29_260.0, 13_400.0)


class DispatchMode(str, Enum):
    """Demand ladder — smallest tier set covering setpoint."""

    OFF = "off"
    IDLE = "idle"           # 2 micro
    CITY = "city"           # 4 micro
    HIGHWAY = "highway"     # 4 micro + 2 medium
    OVERTAKE = "overtake"   # 4 micro + 6 medium
    TRACK = "track"         # all 12 cartridges


# Demand thresholds (watts) — hysteresis band applied in scheduler.
DISPATCH_THRESHOLDS_W: dict[DispatchMode, float] = {
    DispatchMode.OFF: 0.0,
    DispatchMode.IDLE: 60_000.0,
    DispatchMode.CITY: 120_000.0,
    DispatchMode.HIGHWAY: 180_000.0,
    DispatchMode.OVERTAKE: 300_000.0,
    DispatchMode.TRACK: 320_000.0,
}

# Cartridges enabled per mode for Gate-4 4/6/2 tier_blocks order.
DISPATCH_SLOT_COUNTS: dict[DispatchMode, tuple[int, int, int]] = {
    DispatchMode.OFF: (0, 0, 0),
    DispatchMode.IDLE: (2, 0, 0),
    DispatchMode.CITY: (4, 0, 0),
    DispatchMode.HIGHWAY: (4, 2, 0),
    DispatchMode.OVERTAKE: (4, 6, 0),
    DispatchMode.TRACK: (4, 6, 2),
}


@dataclass(frozen=True)
class SchedulerConfig:
    """Multi-objective scheduler weights and limits."""

    thermal_setpoint_c: float = 200.0
    thermal_spread_threshold_c: float = 15.0
    thermal_rotation_gain: float = 0.12
    health_deprioritize_threshold_pct: float = 92.0
    health_load_penalty: float = 0.15
    demand_hysteresis_w: float = 5_000.0
    min_health_for_peak_load_pct: float = 85.0


DEFAULT_SCHEDULER_CONFIG = SchedulerConfig()


@dataclass(frozen=True)
class SchedulerDecision:
    """Per-step dispatch plan."""

    demand_w: float
    mode: DispatchMode
    enabled_indices: tuple[int, ...]
    load_scales: dict[int, float]
    target_power_w: float
    notes: str = ""


@dataclass(frozen=True)
class DynamicRingStepResult:
    """One scheduler timestep on the ring."""

    decision: SchedulerDecision
    cartridges: tuple[MixedCartridgeResult, ...]
    states: tuple[CartridgeRuntimeState, ...]
    health: tuple[CartridgeHealthScore, ...]
    total_elec_power_w: float
    ring_efficiency: float
    nvh_peak_n: float
    active_count: int
    coolant_temp_k: float | None = None


@dataclass(frozen=True)
class DynamicRingProfileResult:
    """Full demand profile run."""

    layout: MixedRingLayout
    steps: tuple[DynamicRingStepResult, ...]
    mean_efficiency: float
    total_energy_wh: float
    peak_nvh_n: float


def build_gate5_production_options(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    cooling_mode: str = "water_jacket",
    cycles: int = 12,
    capture_steps: int = 40,
    capture_passes: int = 2,
    phase_steps: int = 60,
) -> RingBuildOptions:
    """Gate-5 production freeze: Gate-4 load + NVH + medium capture uniformity."""
    return optimize_gate5_medium_phases(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        cycles=cycles,
        capture_steps=capture_steps,
        capture_passes=capture_passes,
        phase_steps=phase_steps,
    )


def dispatch_mode_for_demand(
    demand_w: float,
    *,
    prev_mode: DispatchMode | None = None,
    hysteresis_w: float = 5_000.0,
) -> DispatchMode:
    """Map bus demand to dispatch ladder mode."""
    if demand_w <= 1e-3:
        return DispatchMode.OFF
    ordered = (
        DispatchMode.TRACK,
        DispatchMode.OVERTAKE,
        DispatchMode.HIGHWAY,
        DispatchMode.CITY,
        DispatchMode.IDLE,
    )
    thresholds = [
        (DispatchMode.TRACK, DISPATCH_THRESHOLDS_W[DispatchMode.OVERTAKE]),
        (DispatchMode.OVERTAKE, DISPATCH_THRESHOLDS_W[DispatchMode.HIGHWAY]),
        (DispatchMode.HIGHWAY, DISPATCH_THRESHOLDS_W[DispatchMode.CITY]),
        (DispatchMode.CITY, DISPATCH_THRESHOLDS_W[DispatchMode.IDLE]),
        (DispatchMode.IDLE, 0.0),
    ]
    for mode, threshold in thresholds:
        effective = threshold
        if prev_mode == mode:
            effective -= hysteresis_w
        if demand_w > effective:
            return mode
    return DispatchMode.IDLE


def _slots_by_tier(
    slots: tuple[MixedCartridgeSlot, ...],
) -> dict[int, list[MixedCartridgeSlot]]:
    groups: dict[int, list[MixedCartridgeSlot]] = {0: [], 1: [], 2: []}
    for slot in slots:
        groups[slot.tier_index].append(slot)
    return groups


def _rank_slots_for_activation(
    tier_slots: list[MixedCartridgeSlot],
    states: dict[int, CartridgeRuntimeState],
    count: int,
    config: SchedulerConfig,
) -> list[int]:
    """Pick coolest, healthiest cartridges within a tier cohort."""
    candidates = [
        s for s in tier_slots
        if states[s.index].is_available
    ]

    def _sort_key(slot: MixedCartridgeSlot) -> tuple[float, float, int]:
        st = states[slot.index]
        thermal = st.thermal_headroom_c(setpoint_c=config.thermal_setpoint_c)
        health = st.health_pct
        if st.health_pct < config.health_deprioritize_threshold_pct:
            health -= config.health_load_penalty * 100.0
        return (-thermal, -health, slot.index)

    ranked = sorted(candidates, key=_sort_key)
    return [s.index for s in ranked[:count]]


def schedule_for_demand(
    demand_w: float,
    slots: tuple[MixedCartridgeSlot, ...],
    states: tuple[CartridgeRuntimeState, ...],
    *,
    config: SchedulerConfig | None = None,
    prev_mode: DispatchMode | None = None,
) -> SchedulerDecision:
    """Compute which cartridges to enable for a bus demand setpoint."""
    config = config or DEFAULT_SCHEDULER_CONFIG
    state_map = {s.slot_index: s for s in states}
    mode = dispatch_mode_for_demand(
        demand_w,
        prev_mode=prev_mode,
        hysteresis_w=config.demand_hysteresis_w,
    )
    if mode == DispatchMode.OFF:
        return SchedulerDecision(
            demand_w=demand_w,
            mode=mode,
            enabled_indices=(),
            load_scales={},
            target_power_w=0.0,
            notes="engine off",
        )

    counts = DISPATCH_SLOT_COUNTS[mode]
    groups = _slots_by_tier(slots)
    enabled: list[int] = []
    for tier, n_active in enumerate(counts):
        if n_active <= 0:
            continue
        tier_slots = groups.get(tier, [])
        enabled.extend(
            _rank_slots_for_activation(tier_slots, state_map, n_active, config)
        )

    # If faults reduced capacity, enable additional healthy slots in same tier.
    for tier, n_needed in enumerate(counts):
        if n_needed <= 0:
            continue
        tier_indices = [i for i in enabled if state_map[i].tier_index == tier]
        if len(tier_indices) >= n_needed:
            continue
        spare = _rank_slots_for_activation(
            groups.get(tier, []),
            state_map,
            n_needed,
            config,
        )
        for idx in spare:
            if idx not in enabled:
                enabled.append(idx)
        enabled = enabled[: sum(counts)]

    enabled = sorted(set(enabled))
    nominal = sum(
        TIER_NOMINAL_POWER_W[state_map[i].tier_index]
        for i in enabled
        if i in state_map
    )
    load_scales: dict[int, float] = {}
    if nominal > 1e-3 and demand_w > 0.0:
        scale = min(1.0, demand_w / nominal)
        for idx in enabled:
            st = state_map[idx]
            slot_scale = scale
            if st.health_pct < config.min_health_for_peak_load_pct:
                slot_scale *= max(0.7, st.health_pct / 100.0)
            if st.fault_kind == "generator_derate":
                slot_scale *= 0.75
            load_scales[idx] = slot_scale

    return SchedulerDecision(
        demand_w=demand_w,
        mode=mode,
        enabled_indices=tuple(sorted(enabled)),
        load_scales=load_scales,
        target_power_w=demand_w,
        notes=f"{len(enabled)} slots active",
    )


def _apply_slot_load_scale(
    slot: MixedCartridgeSlot,
    scale: float,
) -> MixedCartridgeSlot:
    if abs(scale - 1.0) < 1e-9:
        return slot
    from designs.phoenix_v3_mixed_ring import _clamp_load_fraction

    new_load = _clamp_load_fraction(slot.load_fraction * scale, slot.tier_index)
    cfg = replace(slot.cfg, load_fraction=new_load)
    return replace(slot, load_fraction=new_load, cfg=cfg)


def _fault_for_state(st: CartridgeRuntimeState) -> TransientFault | None:
    if st.fault_kind in ISOLATING_FAULTS or st.fault_kind == "none":
        return None
    return TransientFault(kind=st.fault_kind, trigger_cycle=0, trigger_time_s=0.0)


def _probe_cartridge_with_telemetry(
    slot: MixedCartridgeSlot,
    *,
    cycles: int,
    fault: TransientFault | None = None,
    coolant: CoolantBus | None = None,
) -> tuple[MixedCartridgeResult, float, bool, float, float, float, float, CoolantBus | None]:
    """Simulate one slot and return harvest + late-window thermal telemetry."""
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    cfg = slot.cfg
    if coolant is not None:
        cfg = replace(
            cfg,
            shared_coolant_enabled=True,
            coolant_temp_k=coolant.temp_k,
        )
    sim = PhoenixV3Simulator(cfg).simulate(
        cycles=cycles, record_history=False, fault=fault,
    )
    reports = [
        r for r in sim.energy.cycle_reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    late = reports[-max(1, len(reports) // 10):] if reports else []
    mean_elec_j = float(np.mean([r.elec_energy_j for r in late])) if late else 0.0
    mean_fuel_j = float(np.mean([r.fuel_energy_j for r in late])) if late else 0.0
    mean_eff = float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0
    mean_cap = float(np.mean([r.capture_fraction for r in late])) if late else 0.0
    mean_stroke = float(np.mean([r.max_piston_travel_mm for r in late])) if late else 0.0
    spring_rec = (
        float(np.mean([r.spring_recovery_efficiency for r in late])) if late else 0.0
    )
    elec_w = mean_elec_j / max(slot.cfg.cycle_time_s, 1e-9)
    boundary_ok = bool(late) and all(r.raw_boundary_valid for r in late)
    cart = MixedCartridgeResult(
        slot=replace(slot, cfg=cfg),
        mean_elec_power_w=elec_w,
        mean_net_efficiency=mean_eff,
        mean_capture_fraction=mean_cap,
        mean_stroke_mm=mean_stroke,
        peak_pressure_bar=sim.metrics.peak_pressure_bar,
        spring_recovery=spring_rec,
        stable=boundary_ok,
        boundary_valid=boundary_ok,
    )
    if late:
        wall = float(np.mean([r.wall_temp_k for r in late]))
        gen = float(np.mean([r.generator_temp_k for r in late]))
        valve = float(np.mean([r.valve_temp_k for r in late]))
        derate = float(np.mean([r.generator_derate for r in late]))
        spring = float(np.mean([r.spring_recovery_efficiency for r in late]))
        collision = any(r.piston_collision for r in late)
    else:
        wall = gen = valve = 293.15
        derate = 1.0
        spring = 0.95
        collision = False
    out_coolant = coolant
    if coolant is not None and cfg.cycle_time_s > 0:
        heat_w = estimate_cartridge_heat_w(sim.energy.cycle_reports, cfg.cycle_time_s)
        out_coolant = advance_coolant_bus(
            coolant,
            ambient_temp_k=cfg.ambient_temp_k,
            coolant_thermal_mass_j_per_k=cfg.coolant_thermal_mass_j_per_k,
            coolant_radiator_w_per_k=cfg.coolant_radiator_w_per_k,
            heat_in_w=heat_w,
            duration_s=cycles * cfg.cycle_time_s,
        )
    return cart, spring, collision, wall, gen, valve, derate, out_coolant


def simulate_dynamic_ring_step(
    slots: tuple[MixedCartridgeSlot, ...],
    states: tuple[CartridgeRuntimeState, ...],
    demand_w: float,
    *,
    cycles: int = 6,
    config: SchedulerConfig | None = None,
    prev_mode: DispatchMode | None = None,
    shared_coolant: bool = True,
    coolant: CoolantBus | None = None,
) -> DynamicRingStepResult:
    """Run one demand timestep with dynamic cartridge enablement."""
    config = config or DEFAULT_SCHEDULER_CONFIG
    decision = schedule_for_demand(
        demand_w, slots, states, config=config, prev_mode=prev_mode,
    )
    state_map = {s.slot_index: s for s in states}
    bus = coolant or CoolantBus(temp_k=293.15)
    probe_results: dict[int, tuple] = {}

    for idx in sorted(decision.enabled_indices):
        slot = slots[idx]
        st = state_map[idx]
        if not st.is_available:
            continue
        scale = decision.load_scales.get(idx, 1.0)
        active_slot = _apply_slot_load_scale(slot, scale)
        fault = _fault_for_state(st)
        use_bus = bus if shared_coolant else None
        cart, spring, collision, wall, gen, valve, derate, bus = _probe_cartridge_with_telemetry(
            active_slot, cycles=cycles, fault=fault, coolant=use_bus,
        )
        probe_results[idx] = (cart, spring, collision, wall, gen, valve, derate, scale, st)

    results: list[MixedCartridgeResult] = []
    new_states: list[CartridgeRuntimeState] = []
    for slot in slots:
        st = state_map[slot.index]
        if slot.index not in probe_results:
            new_states.append(replace(st, mean_power_w=0.0))
            continue
        cart, spring, collision, wall, gen, valve, derate, scale, st = probe_results[slot.index]
        updated = update_state_from_telemetry(
            replace(st, load_scale=scale, fault_kind=st.fault_kind),
            wall_temp_k=wall,
            generator_temp_k=gen,
            valve_temp_k=valve,
            generator_derate=derate,
            mean_power_w=cart.mean_elec_power_w,
            mean_capture_fraction=cart.mean_capture_fraction,
            mean_efficiency=cart.mean_net_efficiency,
            boundary_valid=cart.boundary_valid,
            cycle_time_s=slot.cfg.cycle_time_s,
            cycles=cycles,
        )
        health = compute_cartridge_health(
            updated, spring_recovery=spring, collision=collision,
        )
        updated = replace(updated, health_pct=health.score_pct)
        results.append(cart)
        new_states.append(updated)

    active_slots = tuple(
        _apply_slot_load_scale(slots[i], decision.load_scales.get(i, 1.0))
        for i in decision.enabled_indices
        if i < len(slots)
    )
    nvh = analyze_nvh_proxy(
        active_slots,
        schedule=DEFAULT_RING_SCHEDULE,
        layout_label="4/6/2",
    ) if active_slots else analyze_nvh_proxy(
        (), schedule=DEFAULT_RING_SCHEDULE, layout_label="4/6/2",
    )

    total_elec = sum(c.mean_elec_power_w for c in results)
    total_fuel = 0.0
    for c in results:
        eff = max(c.mean_net_efficiency, 1e-9)
        total_fuel += c.mean_elec_power_w / eff
    ring_eff = total_elec / max(total_fuel, 1e-9)
    health_scores = tuple(
        compute_cartridge_health(s)
        for s in new_states
        if s.slot_index in decision.enabled_indices
    )
    final_states = apply_health_to_states(tuple(new_states), health_scores)

    return DynamicRingStepResult(
        decision=decision,
        cartridges=tuple(results),
        states=final_states,
        health=health_scores,
        total_elec_power_w=total_elec,
        ring_efficiency=ring_eff,
        nvh_peak_n=nvh.peak_resultant_n,
        active_count=len(decision.enabled_indices),
        coolant_temp_k=bus.temp_k if shared_coolant else None,
    )


def simulate_dynamic_ring_profile(
    layout: MixedRingLayout | None = None,
    demand_profile_w: tuple[float, ...] | None = None,
    *,
    build_options: RingBuildOptions | None = None,
    cycles_per_step: int = 6,
    config: SchedulerConfig | None = None,
    shared_coolant: bool = True,
) -> DynamicRingProfileResult:
    """Run a sequence of demand setpoints through the dynamic scheduler."""
    layout = layout or MixedRingLayout(*GATE4_LAYOUT)
    if build_options is None:
        build_options = build_gate5_production_options(
            layout, capture_steps=20, capture_passes=1, phase_steps=25,
        )
    slots = build_mixed_ring_slots(layout, build_options=build_options)
    states = initial_ring_states(slots)
    profile = demand_profile_w or default_demand_profile_w()
    bus = CoolantBus(temp_k=293.15) if shared_coolant else None

    steps: list[DynamicRingStepResult] = []
    prev_mode: DispatchMode | None = None
    for demand in profile:
        step = simulate_dynamic_ring_step(
            slots,
            states,
            demand,
            cycles=cycles_per_step,
            config=config,
            prev_mode=prev_mode,
            shared_coolant=shared_coolant,
            coolant=bus,
        )
        if shared_coolant and step.coolant_temp_k is not None:
            bus = CoolantBus(temp_k=step.coolant_temp_k)
        steps.append(step)
        states = step.states
        prev_mode = step.decision.mode

    mean_eff = float(np.mean([s.ring_efficiency for s in steps])) if steps else 0.0
    energy_wh = sum(s.total_elec_power_w for s in steps) / 3600.0 * (cycles_per_step * 0.016)
    peak_nvh = max((s.nvh_peak_n for s in steps), default=0.0)
    return DynamicRingProfileResult(
        layout=layout,
        steps=tuple(steps),
        mean_efficiency=mean_eff,
        total_energy_wh=energy_wh,
        peak_nvh_n=peak_nvh,
    )


def simulate_static_gate5_ring(
    layout: MixedRingLayout | None = None,
    *,
    build_options: RingBuildOptions | None = None,
    cycles: int = 12,
) -> MixedRingResult:
    """Full static ring (all cartridges on) for Gate-5 production baseline."""
    from designs.phoenix_v3_mixed_ring import simulate_mixed_ring

    layout = layout or MixedRingLayout(*GATE4_LAYOUT)
    if build_options is None:
        build_options = build_gate5_production_options(
            layout, capture_steps=20, capture_passes=1, phase_steps=25,
        )
    return simulate_mixed_ring(
        layout,
        schedule=DEFAULT_RING_SCHEDULE,
        cycles=cycles,
        build_options=build_options,
    )


def default_demand_profile_w() -> tuple[float, ...]:
    """Synthetic drive profile: idle → city → highway → overtake → track → coast."""
    return (
        25_000.0,
        25_000.0,
        90_000.0,
        90_000.0,
        150_000.0,
        150_000.0,
        250_000.0,
        310_000.0,
        310_000.0,
        80_000.0,
        40_000.0,
        0.0,
    )


def inject_cartridge_fault(
    states: tuple[CartridgeRuntimeState, ...],
    slot_index: int,
    fault_kind: str,
) -> tuple[CartridgeRuntimeState, ...]:
    """Mark a cartridge faulted for tolerance testing."""
    out: list[CartridgeRuntimeState] = []
    for st in states:
        if st.slot_index == slot_index:
            out.append(replace(st, fault_kind=fault_kind))
        else:
            out.append(st)
    return tuple(out)


def thermal_rotation_adjustment(
    states: tuple[CartridgeRuntimeState, ...],
    config: SchedulerConfig | None = None,
) -> dict[int, float]:
    """Suggest load scale reductions for hot cartridges (thermal zoning)."""
    config = config or DEFAULT_SCHEDULER_CONFIG
    temps = [s.wall_temp_c for s in states if s.enabled]
    if len(temps) < 2:
        return {}
    spread = max(temps) - min(temps)
    if spread < config.thermal_spread_threshold_c:
        return {}
    mean_temp = sum(temps) / len(temps)
    adjustments: dict[int, float] = {}
    for st in states:
        if st.slot_index not in adjustments:
            continue
        excess = st.wall_temp_c - mean_temp
        if excess > config.thermal_spread_threshold_c * 0.5:
            cut = min(0.25, excess * config.thermal_rotation_gain / 100.0)
            adjustments[st.slot_index] = max(0.5, 1.0 - cut)
    return adjustments
