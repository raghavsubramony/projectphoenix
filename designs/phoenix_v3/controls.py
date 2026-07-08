"""ECU control laws, valve timing, and generator load shaping."""

from __future__ import annotations

import math

from designs.phoenix_v3.config import PhoenixV3Config, TransientFault, ValveState
from designs.phoenix_v3.constants import clamp


def phased_cycle_time_ms(t_ms: float, cfg: PhoenixV3Config, *, valve_lag: bool = False) -> float:
    cycle_ms = cfg.cycle_time_s * 1e3
    lag = cfg.tolerance_valve_lag_ms if valve_lag else 0.0
    return (t_ms - cfg.phase_offset_ms - lag) % max(cycle_ms, 1e-9)


def valve_schedule(
    t_ms: float,
    cfg: PhoenixV3Config,
    *,
    fault: TransientFault | None = None,
    cycle_index: int = 0,
) -> tuple[ValveState, str]:
    t_eff = phased_cycle_time_ms(t_ms, cfg, valve_lag=True)
    s = cfg.cycle_time_s * 1e3 / 10.0
    if t_eff < 1.5 * s:
        valves = ValveState(True, True, True, True)
        stage = "1_scavenge"
    elif t_eff < 2.0 * s:
        valves = ValveState(True, True, False, False)
        stage = "2_exhaust_close"
    elif t_eff < 4.8 * s:
        valves = ValveState(False, False, False, False)
        stage = "3_4_trapped_compression"
    elif t_eff < 5.0 * s:
        valves = ValveState(False, False, False, False)
        stage = "5_6_injection_ignition"
    elif t_eff < cfg.exhaust_open_ms * s:
        valves = ValveState(False, False, False, False)
        stage = "7_power"
    elif t_eff < (cfg.exhaust_open_ms + cfg.staged_exhaust_lead_ms) * s:
        valves = ValveState(False, False, True, True)
        stage = "8_blowdown_exhaust_only"
    elif t_eff < 10.0 * s:
        valves = ValveState(True, True, True, True)
        stage = "9_scavenge_push"
    else:
        valves = ValveState(True, True, True, True)
        stage = "10_reset"

    if fault and fault.kind == "stuck_intake_a" and cycle_index >= fault.trigger_cycle:
        valves = ValveState(False, valves.intake_b, valves.exhaust_a, valves.exhaust_b)

    return valves, stage


def intake_open_at_local_ms(local_ms: float, cfg: PhoenixV3Config) -> bool:
    """Whether either intake port is open at local cycle time (ms)."""
    s = cfg.cycle_time_s * 1e3 / 10.0
    if local_ms < 1.5 * s:
        return True
    if local_ms < 2.0 * s:
        return True
    if local_ms >= (cfg.exhaust_open_ms + cfg.staged_exhaust_lead_ms) * s:
        if local_ms < 10.0 * s:
            return True
        return True
    return False


def cap_generator_work_step(
    p_pa: float,
    area: float,
    dx_a: float,
    dx_b: float,
    f_gen_a: float,
    f_gen_b: float,
) -> tuple[float, float]:
    w_a = f_gen_a * dx_a if dx_a > 0.0 else 0.0
    w_b = f_gen_b * dx_b if dx_b > 0.0 else 0.0
    d_v = area * (dx_a + dx_b)
    if d_v <= 0.0:
        return w_a, w_b
    chamber_work = p_pa * d_v
    w_total = w_a + w_b
    if w_total <= chamber_work + 1e-12:
        return w_a, w_b
    scale = chamber_work / max(w_total, 1e-12)
    return w_a * scale, w_b * scale


def generator_load_profile(
    x_outward_m: float,
    half_stroke_m: float,
    stage: str,
    cfg: PhoenixV3Config,
) -> float:
    if stage not in ("7_power", "3_4_trapped_compression"):
        return 0.0
    frac = clamp(x_outward_m / max(half_stroke_m, 1e-9), 0.0, 1.0)
    if not cfg.generator_adaptive_profile:
        return 1.0 if stage == "7_power" else 0.0
    shape = math.sin(math.pi * frac) ** 2
    lo = max(cfg.generator_profile_tdc_fraction, cfg.generator_midstroke_min_profile)
    hi = cfg.generator_profile_peak_fraction
    return lo + (hi - lo) * shape


def piston_travel_fraction(x_outward_m: float, cfg: PhoenixV3Config) -> float:
    return clamp(x_outward_m / max(cfg.half_stroke_m, 1e-9), 0.0, 1.0)


def virtual_end_stop_gain(x_outward_m: float, cfg: PhoenixV3Config) -> float:
    if not cfg.virtual_end_stop_enabled:
        return 1.0
    frac = piston_travel_fraction(x_outward_m, cfg)
    lo = cfg.virtual_end_stop_start_fraction
    hi = cfg.virtual_end_stop_hard_fraction
    if frac <= lo:
        return 1.0
    if frac >= hi:
        return 0.0
    return 1.0 - (frac - lo) / max(hi - lo, 1e-9)


def spring_end_stop_relief_gain(x_outward_m: float, cfg: PhoenixV3Config) -> float:
    if not cfg.virtual_end_stop_enabled or not cfg.spring_relief_at_end_stop:
        return 1.0
    frac = piston_travel_fraction(x_outward_m, cfg)
    lo = cfg.virtual_end_stop_start_fraction
    hi = cfg.virtual_end_stop_hard_fraction
    lo_gain = cfg.spring_relief_min_fraction
    if frac <= lo:
        return 1.0
    if frac >= hi:
        return lo_gain
    t = (frac - lo) / max(hi - lo, 1e-9)
    return 1.0 - t * (1.0 - lo_gain)


def slew_generator_load_scale(
    current: float,
    target: float,
    dt: float,
    cfg: PhoenixV3Config,
) -> float:
    target = clamp(target, 0.0, cfg.max_generator_load_scale)
    if not cfg.load_spike_limiter_enabled:
        return target
    max_delta = cfg.load_spike_slew_per_s * dt
    delta = clamp(target - current, -max_delta, max_delta)
    return clamp(current + delta, 0.0, cfg.max_generator_load_scale)


def integrate_piston_travel(
    x_m: float,
    v_ms: float,
    f_net_n: float,
    dt: float,
    cfg: PhoenixV3Config,
    *,
    motion_guard: bool,
) -> tuple[float, float, bool]:
    v_new = v_ms + (f_net_n / cfg.piston_mass_kg) * dt
    x_trial = x_m + v_new * dt
    lo = 0.0
    hi = cfg.half_stroke_m
    impact = False

    if not motion_guard:
        x_new = clamp(x_trial, lo, hi)
        return x_new, v_new, False

    if x_trial < lo:
        impact = v_new < -cfg.collision_speed_threshold_ms
        x_trial = lo
        if v_new < 0.0:
            v_new = -v_new * cfg.travel_limit_restitution
    elif x_trial > hi:
        impact = v_new > cfg.collision_speed_threshold_ms
        x_trial = hi
        if v_new > 0.0:
            v_new = -v_new * cfg.travel_limit_restitution
    return x_trial, v_new, impact


def generator_brake_force_n(
    f_gas_n: float,
    f_spring_n: float,
    v_outward_ms: float,
    stage: str,
    gen_scale: float,
    cfg: PhoenixV3Config,
    *,
    x_outward_m: float = 0.0,
    end_stop_gain: float = 1.0,
) -> float:
    profile = generator_load_profile(x_outward_m, cfg.half_stroke_m, stage, cfg)
    if profile <= 0.0 or v_outward_ms <= 0.0 or end_stop_gain <= 0.0:
        return 0.0
    f_cmd = gen_scale * cfg.generator_rated_force_n * profile * end_stop_gain
    f_spring_eff = cfg.generator_spring_decouple * f_spring_n
    f_avail = max(f_gas_n - f_spring_eff, 0.0)
    return min(f_cmd, f_avail)


def generator_balance_scales(
    x_a: float,
    x_b: float,
    scale_a: float,
    scale_b: float,
    cfg: PhoenixV3Config,
) -> tuple[float, float]:
    if not cfg.generator_balance_control:
        return scale_a, scale_b
    asym = (x_a - x_b) / max(cfg.half_stroke_m, 1e-9)
    trim_a = clamp(1.0 + cfg.generator_balance_gain * asym, 0.80, 1.20)
    trim_b = clamp(1.0 - cfg.generator_balance_gain * asym, 0.80, 1.20)
    return scale_a * trim_a, scale_b * trim_b


def slew_spring_phase_gain(
    current: float,
    target: float,
    dt: float,
    cfg: PhoenixV3Config,
) -> float:
    if not cfg.air_spring_phase_control or cfg.air_spring_phase_slew_per_s <= 0.0:
        return target
    max_delta = cfg.air_spring_phase_slew_per_s * dt
    return current + clamp(target - current, -max_delta, max_delta)


def capture_startup_multiplier(
    cycle_index: int,
    prev_cycle_bdc_mm: float,
    cfg: PhoenixV3Config,
) -> float:
    if cycle_index < cfg.capture_startup_cycles:
        return cfg.capture_startup_scale
    if prev_cycle_bdc_mm < cfg.capture_min_bdc_mm:
        return cfg.capture_startup_scale
    return 1.0


def spring_phase_gain(
    dx_outward_m: float,
    stage: str,
    cfg: PhoenixV3Config,
) -> float:
    if not cfg.air_spring_phase_control:
        return 1.0
    if dx_outward_m > 0.0 and stage in ("7_power", "8_blowdown_exhaust_only"):
        return cfg.air_spring_expansion_gain
    if dx_outward_m < 0.0 and stage in ("3_4_trapped_compression", "2_exhaust_close"):
        return cfg.air_spring_compression_gain
    return 1.0
