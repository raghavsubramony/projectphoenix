"""Ring-level supervisory ECU for mixed-tier ATPE rings.

Slow loop (outer iterations) measures per-cartridge harvest KPIs, compares them
to ring averages, and nudges ``load_fraction``, ``generator_force_scale``,
``ignition_ms``, and ``combustion_pressure_gain`` within tier-native bounds.

Starts from unity load policy and tier-block scheduling; adjustments are capped
relative to each cartridge's tuned baseline so the supervisor cannot wander
outside validated tier search envelopes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3_optimizer import search_bounds_for_tier
from designs.phoenix_v3_simulation import PhoenixV3Simulator
from designs.phoenix_v3_mixed_ring import (
    DEFAULT_RING_LAYOUT,
    DEFAULT_RING_SCHEDULE,
    MixedCartridgeResult,
    MixedCartridgeSlot,
    MixedRingLayout,
    MixedRingResult,
    RingSchedule,
    UNITY_LOAD_POLICY,
    analyze_nvh_proxy,
    audit_ring_tier_configs,
    build_mixed_ring_slots,
    compute_ring_balance_index,
)

# Tier role authority — medium leads, large is damped, micro is responsive.
TIER_ROLE_GAIN: tuple[float, float, float] = (0.85, 1.00, 0.65)


@dataclass(frozen=True)
class RingSupervisorConfig:
    """Supervisory loop gains and safety limits."""

    iterations: int = 5
    probe_cycles: int = 8
    final_cycles: int = 12
    power_deadband: float = 0.02
    capture_deadband: float = 0.03
    stroke_deadband: float = 0.04
    k_load: float = 0.045
    k_generator_force: float = 0.035
    k_ignition_ms: float = 0.06
    k_combustion_gain: float = 0.025
    k_stroke: float = 0.020
    k_tier_power: float = 0.030
    tier_power_blend: float = 0.35
    max_load_drift_frac: float = 0.06
    max_gfs_drift_frac: float = 0.05
    max_ignition_drift_ms: float = 0.15
    max_combustion_drift_frac: float = 0.04
    max_step_load: float = 0.012
    max_step_gfs: float = 0.025
    max_step_ignition_ms: float = 0.04
    max_step_combustion: float = 0.015


DEFAULT_SUPERVISOR_CONFIG = RingSupervisorConfig()


@dataclass(frozen=True)
class CartridgeObservation:
    """Late-window harvest snapshot for one cartridge."""

    index: int
    tier_index: int
    power_w: float
    capture_fraction: float
    stroke_mm: float
    net_efficiency: float
    peak_pressure_bar: float
    spring_recovery: float
    boundary_valid: bool
    energy_balance_valid: bool


@dataclass(frozen=True)
class RingAverages:
    mean_power_w: float
    mean_capture: float
    mean_stroke_mm: float


@dataclass(frozen=True)
class SupervisorAdjustment:
    """One supervisory nudge applied to a cartridge."""

    index: int
    tier_index: int
    delta_load: float
    delta_generator_force_scale: float
    delta_ignition_ms: float
    delta_combustion_gain: float
    power_error: float
    capture_error: float
    stroke_error: float


@dataclass(frozen=True)
class SupervisorIteration:
    iteration: int
    ring_averages: RingAverages
    tier_rbi_pct: float
    cartridge_rbi_pct: float
    adjustments: tuple[SupervisorAdjustment, ...]


@dataclass(frozen=True)
class SupervisedMixedRingResult:
    """Mixed ring run with supervisory loop history."""

    ring: MixedRingResult
    baseline_tier_rbi_pct: float
    baseline_cartridge_rbi_pct: float
    iterations: tuple[SupervisorIteration, ...]
    supervisor_config: RingSupervisorConfig

    @property
    def tier_rbi_improvement_pct(self) -> float:
        return self.baseline_tier_rbi_pct - self.ring.rbi.tier_total_pct

    @property
    def cartridge_rbi_improvement_pct(self) -> float:
        return self.baseline_cartridge_rbi_pct - self.ring.rbi.total_pct

    @property
    def rbi_improvement_pct(self) -> float:
        """Alias — supervisor optimizes tier-aggregate RBI."""
        return self.tier_rbi_improvement_pct


def _tier_bounds(tier_index: int) -> dict[str, tuple[float, float]]:
    return {b.name: (b.lo, b.hi) for b in search_bounds_for_tier(tier_index)}


def _clamp_param(name: str, value: float, tier_index: int) -> float:
    lo, hi = _tier_bounds(tier_index)[name]
    return max(lo, min(hi, value))


def _clamp_near_baseline(
    value: float,
    baseline: float,
    *,
    max_abs_delta: float,
    tier_index: int,
    param_name: str,
) -> float:
    bounded = max(baseline - max_abs_delta, min(baseline + max_abs_delta, value))
    return _clamp_param(param_name, bounded, tier_index)


def _replace_slot_config(
    slot: MixedCartridgeSlot,
    cfg: PhoenixV3Config,
) -> MixedCartridgeSlot:
    phased = replace(cfg, phase_offset_ms=slot.phase_offset_ms)
    return replace(
        slot,
        cfg=phased,
        load_fraction=phased.load_fraction,
        frequency_hz=phased.frequency_hz,
    )


def _valid_late_reports(reports: tuple) -> list:
    return [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]


def _observe_cartridge(
    slot: MixedCartridgeSlot,
    *,
    cycles: int,
) -> CartridgeObservation:
    sim = PhoenixV3Simulator(slot.cfg).simulate(cycles=cycles, record_history=False)
    reports = _valid_late_reports(sim.energy.cycle_reports)
    late = reports[-max(1, len(reports) // 10):] if reports else []
    if not late:
        return CartridgeObservation(
            index=slot.index,
            tier_index=slot.tier_index,
            power_w=0.0,
            capture_fraction=0.0,
            stroke_mm=0.0,
            net_efficiency=0.0,
            peak_pressure_bar=sim.metrics.peak_pressure_bar,
            spring_recovery=0.0,
            boundary_valid=False,
            energy_balance_valid=sim.energy.energy_balance_valid,
        )
    mean_elec_j = float(np.mean([r.elec_energy_j for r in late]))
    power_w = mean_elec_j / max(slot.cfg.cycle_time_s, 1e-9)
    return CartridgeObservation(
        index=slot.index,
        tier_index=slot.tier_index,
        power_w=power_w,
        capture_fraction=float(np.mean([r.capture_fraction for r in late])),
        stroke_mm=float(np.mean([r.max_piston_travel_mm for r in late])),
        net_efficiency=float(np.mean([r.net_cartridge_efficiency for r in late])),
        peak_pressure_bar=sim.metrics.peak_pressure_bar,
        spring_recovery=float(np.mean([r.spring_recovery_efficiency for r in late])),
        boundary_valid=all(r.raw_boundary_valid for r in late),
        energy_balance_valid=sim.energy.energy_balance_valid,
    )


def _ring_averages(observations: tuple[CartridgeObservation, ...]) -> RingAverages:
    if not observations:
        return RingAverages(0.0, 0.0, 0.0)
    return RingAverages(
        mean_power_w=float(np.mean([o.power_w for o in observations])),
        mean_capture=float(np.mean([o.capture_fraction for o in observations])),
        mean_stroke_mm=float(np.mean([o.stroke_mm for o in observations])),
    )


def _tier_averages_map(
    observations: tuple[CartridgeObservation, ...],
) -> dict[int, RingAverages]:
    groups: dict[int, list[CartridgeObservation]] = {0: [], 1: [], 2: []}
    for obs in observations:
        groups[obs.tier_index].append(obs)
    return {
        tier: _ring_averages(tuple(items))
        for tier, items in groups.items()
        if items
    }


def _compute_adjustment(
    obs: CartridgeObservation,
    tier_stats: RingAverages,
    ring_stats: RingAverages,
    *,
    cfg: PhoenixV3Config,
    baseline: PhoenixV3Config,
    policy: RingSupervisorConfig,
) -> SupervisorAdjustment:
    tier = obs.tier_index
    gain = TIER_ROLE_GAIN[tier]

    within_power_err = (
        (tier_stats.mean_power_w - obs.power_w) / max(tier_stats.mean_power_w, 1e-6)
    )
    between_power_err = (
        (ring_stats.mean_power_w - tier_stats.mean_power_w)
        / max(ring_stats.mean_power_w, 1e-6)
    )
    blend = policy.tier_power_blend
    power_err = (1.0 - blend) * within_power_err + blend * between_power_err
    capture_err = (
        (tier_stats.mean_capture - obs.capture_fraction) / max(tier_stats.mean_capture, 1e-6)
    )
    stroke_err = (
        (tier_stats.mean_stroke_mm - obs.stroke_mm) / max(tier_stats.mean_stroke_mm, 1e-6)
    )

    d_load = 0.0
    d_gfs = 0.0
    d_ign = 0.0
    d_cpg = 0.0

    if power_err > policy.power_deadband:
        d_load += policy.k_load * power_err * gain
        d_gfs += policy.k_generator_force * max(capture_err, 0.0) * gain
        if capture_err <= 0.0:
            d_ign -= policy.k_ignition_ms * power_err * gain
            d_cpg += policy.k_combustion_gain * power_err * gain
        d_load += policy.k_tier_power * between_power_err * gain
    elif power_err < -policy.power_deadband:
        d_load += policy.k_load * power_err * gain * 0.5
        d_gfs += policy.k_generator_force * min(capture_err, 0.0) * gain * 0.5

    if capture_err > policy.capture_deadband and power_err <= policy.power_deadband:
        d_gfs += policy.k_generator_force * capture_err * gain * 0.6
        d_load += policy.k_load * capture_err * gain * 0.3

    if abs(stroke_err) > policy.stroke_deadband:
        d_gfs -= policy.k_stroke * stroke_err * gain

    d_load = max(-policy.max_step_load, min(policy.max_step_load, d_load))
    d_gfs = max(-policy.max_step_gfs, min(policy.max_step_gfs, d_gfs))
    d_ign = max(-policy.max_step_ignition_ms, min(policy.max_step_ignition_ms, d_ign))
    d_cpg = max(-policy.max_step_combustion, min(policy.max_step_combustion, d_cpg))

    return SupervisorAdjustment(
        index=obs.index,
        tier_index=tier,
        delta_load=d_load,
        delta_generator_force_scale=d_gfs,
        delta_ignition_ms=d_ign,
        delta_combustion_gain=d_cpg,
        power_error=power_err,
        capture_error=capture_err,
        stroke_error=stroke_err,
    )


def _apply_adjustment(
    cfg: PhoenixV3Config,
    baseline: PhoenixV3Config,
    adj: SupervisorAdjustment,
    *,
    policy: RingSupervisorConfig,
) -> PhoenixV3Config:
    tier = adj.tier_index
    new_load = _clamp_near_baseline(
        cfg.load_fraction + adj.delta_load,
        baseline.load_fraction,
        max_abs_delta=baseline.load_fraction * policy.max_load_drift_frac,
        tier_index=tier,
        param_name="load_fraction",
    )
    new_gfs = _clamp_near_baseline(
        cfg.generator_force_scale + adj.delta_generator_force_scale,
        baseline.generator_force_scale,
        max_abs_delta=baseline.generator_force_scale * policy.max_gfs_drift_frac,
        tier_index=tier,
        param_name="generator_force_scale",
    )
    new_ign = _clamp_near_baseline(
        cfg.ignition_ms + adj.delta_ignition_ms,
        baseline.ignition_ms,
        max_abs_delta=policy.max_ignition_drift_ms,
        tier_index=tier,
        param_name="ignition_ms",
    )
    new_cpg = _clamp_near_baseline(
        cfg.combustion_pressure_gain + adj.delta_combustion_gain,
        baseline.combustion_pressure_gain,
        max_abs_delta=baseline.combustion_pressure_gain * policy.max_combustion_drift_frac,
        tier_index=tier,
        param_name="combustion_pressure_gain",
    )
    return replace(
        cfg,
        load_fraction=new_load,
        generator_force_scale=new_gfs,
        ignition_ms=new_ign,
        injection_start_ms=max(0.5, new_ign - 0.4),
        combustion_pressure_gain=new_cpg,
    )


def _aggregate_ring(
    layout: MixedRingLayout,
    schedule: RingSchedule,
    slots: tuple[MixedCartridgeSlot, ...],
    cartridges: tuple[MixedCartridgeResult, ...],
    *,
    tier_audits,
    energy_balance_valid: bool,
) -> MixedRingResult:
    total_elec = sum(c.mean_elec_power_w for c in cartridges)
    total_fuel = sum(
        c.mean_elec_power_w / max(c.mean_net_efficiency, 1e-9) for c in cartridges
    )
    ring_eff = total_elec / max(total_fuel, 1e-9)
    nvh = analyze_nvh_proxy(slots, schedule=schedule, layout_label=layout.label)
    rbi = compute_ring_balance_index(cartridges)
    all_boundary = all(c.boundary_valid for c in cartridges)
    return MixedRingResult(
        layout=layout,
        schedule=schedule,
        load_policy=UNITY_LOAD_POLICY,
        cartridges=cartridges,
        tier_audits=tier_audits,
        total_elec_power_w=total_elec,
        ring_efficiency=ring_eff,
        rbi=rbi,
        nvh=nvh,
        energy_balance_valid=energy_balance_valid,
        all_boundaries_valid=all_boundary,
    )


def simulate_supervised_mixed_ring(
    layout: MixedRingLayout | None = None,
    *,
    schedule: RingSchedule = DEFAULT_RING_SCHEDULE,
    supervisor: RingSupervisorConfig | None = None,
    cooling_mode: str = "water_jacket",
) -> SupervisedMixedRingResult:
    """Run mixed ring with closed-loop supervisory iterations (unity load)."""
    layout = layout or MixedRingLayout(*DEFAULT_RING_LAYOUT)
    policy = supervisor or DEFAULT_SUPERVISOR_CONFIG
    tier_audits = audit_ring_tier_configs(
        layout, cooling_mode=cooling_mode, load_policy=UNITY_LOAD_POLICY,
    )

    base_slots = build_mixed_ring_slots(
        layout,
        schedule=schedule,
        cooling_mode=cooling_mode,
        load_policy=UNITY_LOAD_POLICY,
    )
    baselines = {s.index: s.cfg for s in base_slots}
    slots = list(base_slots)
    history: list[SupervisorIteration] = []

    baseline_obs = tuple(
        _observe_cartridge(s, cycles=policy.probe_cycles) for s in slots
    )
    baseline_carts = tuple(
        MixedCartridgeResult(
            slot=s,
            mean_elec_power_w=o.power_w,
            mean_net_efficiency=o.net_efficiency,
            mean_capture_fraction=o.capture_fraction,
            mean_stroke_mm=o.stroke_mm,
            peak_pressure_bar=o.peak_pressure_bar,
            spring_recovery=o.spring_recovery,
            stable=o.boundary_valid,
            boundary_valid=o.boundary_valid,
        )
        for s, o in zip(slots, baseline_obs)
    )
    baseline_rbi = compute_ring_balance_index(baseline_carts)
    baseline_tier_rbi = baseline_rbi.tier_total_pct
    baseline_cartridge_rbi = baseline_rbi.total_pct

    for iteration in range(policy.iterations):
        observations = tuple(
            _observe_cartridge(s, cycles=policy.probe_cycles) for s in slots
        )
        ring_stats = _ring_averages(observations)
        tier_stats_map = _tier_averages_map(observations)
        adjustments: list[SupervisorAdjustment] = []
        new_slots: list[MixedCartridgeSlot] = []

        for slot, obs in zip(slots, observations):
            baseline = baselines[slot.index]
            tier_stats = tier_stats_map[obs.tier_index]
            adj = _compute_adjustment(
                obs,
                tier_stats,
                ring_stats,
                cfg=slot.cfg,
                baseline=baseline,
                policy=policy,
            )
            adjustments.append(adj)
            new_cfg = _apply_adjustment(slot.cfg, baseline, adj, policy=policy)
            new_slots.append(_replace_slot_config(slot, new_cfg))

        slots = new_slots
        probe_carts = tuple(
            MixedCartridgeResult(
                slot=s,
                mean_elec_power_w=o.power_w,
                mean_net_efficiency=o.net_efficiency,
                mean_capture_fraction=o.capture_fraction,
                mean_stroke_mm=o.stroke_mm,
                peak_pressure_bar=o.peak_pressure_bar,
                spring_recovery=o.spring_recovery,
                stable=o.boundary_valid,
                boundary_valid=o.boundary_valid,
            )
            for s, o in zip(slots, observations)
        )
        probe_rbi = compute_ring_balance_index(probe_carts)
        history.append(
            SupervisorIteration(
                iteration=iteration,
                ring_averages=ring_stats,
                tier_rbi_pct=probe_rbi.tier_total_pct,
                cartridge_rbi_pct=probe_rbi.total_pct,
                adjustments=tuple(adjustments),
            )
        )

    final_slots = tuple(slots)
    final_carts: list[MixedCartridgeResult] = []
    balance_ok = True
    for slot in final_slots:
        obs = _observe_cartridge(slot, cycles=policy.final_cycles)
        balance_ok = balance_ok and obs.energy_balance_valid
        final_carts.append(
            MixedCartridgeResult(
                slot=slot,
                mean_elec_power_w=obs.power_w,
                mean_net_efficiency=obs.net_efficiency,
                mean_capture_fraction=obs.capture_fraction,
                mean_stroke_mm=obs.stroke_mm,
                peak_pressure_bar=obs.peak_pressure_bar,
                spring_recovery=obs.spring_recovery,
                stable=obs.boundary_valid,
                boundary_valid=obs.boundary_valid,
            )
        )

    ring = _aggregate_ring(
        layout,
        schedule,
        final_slots,
        tuple(final_carts),
        tier_audits=tier_audits,
        energy_balance_valid=balance_ok,
    )
    return SupervisedMixedRingResult(
        ring=ring,
        baseline_tier_rbi_pct=baseline_tier_rbi,
        baseline_cartridge_rbi_pct=baseline_cartridge_rbi,
        iterations=tuple(history),
        supervisor_config=policy,
    )


def print_supervisor_report(result: SupervisedMixedRingResult) -> None:
    from designs.phoenix_v3_mixed_ring import print_mixed_ring_report

    print()
    print("=" * 72)
    print("RING SUPERVISOR — closed-loop balance run")
    print("=" * 72)
    print(f"  Iterations:          {result.supervisor_config.iterations}")
    print(f"  Baseline tier RBI:   {result.baseline_tier_rbi_pct:.2f}%")
    print(f"  Final tier RBI:      {result.ring.rbi.tier_total_pct:.2f}%")
    print(f"  Tier RBI change:     {result.tier_rbi_improvement_pct:+.2f} pp")
    print(
        f"  Tier RBI target:     "
        f"{'PASS' if result.ring.rbi.meets_target else 'WARN'} (< 5%)"
    )
    print(f"  Cartridge RBI:       {result.ring.rbi.total_pct:.2f}%  "
          f"(baseline {result.baseline_cartridge_rbi_pct:.2f}%)")
    print()
    print(f"  {'Iter':>4}  {'tRBI%':>6}  {'cRBI%':>6}  {'kW avg':>8}  {'cap avg':>8}")
    for step in result.iterations:
        a = step.ring_averages
        print(
            f"  {step.iteration:4d}  {step.tier_rbi_pct:5.1f}  "
            f"{step.cartridge_rbi_pct:5.1f}  {a.mean_power_w/1000:7.2f}  "
            f"{a.mean_capture:7.1%}"
        )
    last_adj = result.iterations[-1].adjustments if result.iterations else ()
    if last_adj:
        print()
        print("  Final-iteration adjustments (sample):")
        for adj in last_adj[:4]:
            print(
                f"    slot {adj.index:2d} tier {adj.tier_index}  "
                f"dLoad {adj.delta_load:+.4f}  dGFS {adj.delta_generator_force_scale:+.4f}  "
                f"dIgn {adj.delta_ignition_ms:+.4f}ms  dCpg {adj.delta_combustion_gain:+.4f}"
            )
    print("=" * 72)
    print_mixed_ring_report(result.ring)
