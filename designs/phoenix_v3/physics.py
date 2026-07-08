"""Lumped thermodynamic and mechanical primitives."""

from __future__ import annotations

import math

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.constants import GAMMA, R_AIR, clamp


def chamber_volume_m3(x_a: float, x_b: float, cfg: PhoenixV3Config) -> float:
    return cfg.clearance_volume_m3 + cfg.piston_area_m2 * (x_a + x_b)


def air_spring_volume_m3(piston_outward_m: float, cfg: PhoenixV3Config) -> float:
    frac = clamp(piston_outward_m / cfg.half_stroke_m, 0.0, 1.0)
    return cfg.air_spring_volume_max_m3 - frac * (
        cfg.air_spring_volume_max_m3 - cfg.air_spring_volume_min_m3
    )


def air_spring_internal_energy_j(pressure_pa: float, volume_m3: float) -> float:
    return pressure_pa * volume_m3 / max(GAMMA - 1.0, 1e-9)


def air_spring_pressure_pa(
    piston_outward_m: float,
    cfg: PhoenixV3Config,
    *,
    control_gain: float | None = None,
) -> float:
    gain = cfg.air_spring_control_gain if control_gain is None else control_gain
    volume = air_spring_volume_m3(piston_outward_m, cfg)
    ref_p = cfg.air_spring_reference_bar * 1e5 * gain
    ref_v = cfg.air_spring_volume_max_m3
    return ref_p * (ref_v / max(volume, 1e-9)) ** GAMMA


def mass_flow_orifice(
    upstream_pa: float,
    downstream_pa: float,
    open_fraction: float,
    coeff: float,
) -> float:
    if open_fraction <= 0.0 or upstream_pa <= downstream_pa:
        return 0.0
    dp = upstream_pa - downstream_pa
    return coeff * open_fraction * math.sqrt(max(dp, 0.0))


def wiebe_fraction(t_ms: float, start_ms: float, duration_ms: float) -> float:
    if t_ms <= start_ms:
        return 0.0
    if t_ms >= start_ms + duration_ms:
        return 1.0
    x = (t_ms - start_ms) / max(duration_ms, 1e-9)
    return 1.0 - math.exp(-5.0 * (x ** 3.0))


def piston_kinetic_energy_j(v_a: float, v_b: float, mass_kg: float) -> float:
    return 0.5 * mass_kg * (v_a * v_a + v_b * v_b)


def spring_internal_energy_total_j(
    x_a: float,
    x_b: float,
    p_sa: float,
    p_sb: float,
    cfg: PhoenixV3Config,
) -> float:
    return (
        air_spring_internal_energy_j(p_sa, air_spring_volume_m3(x_a, cfg))
        + air_spring_internal_energy_j(p_sb, air_spring_volume_m3(x_b, cfg))
    )
