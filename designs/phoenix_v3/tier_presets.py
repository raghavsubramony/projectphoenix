"""Native ATPE tier geometry for Phoenix V3 (100 / 300 / 750 cc cartridges)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.tier_profiles import (
    TIER_PROFILES,
    profile_for_tier_index,
    resolve_tier_tuning_path,
)

# Total opposed-piston swept volume per cartridge (cc).
TIER_DISPLACEMENT_CC: tuple[float, ...] = tuple(
    p.total_displacement_cc for p in TIER_PROFILES
)
TIER_NAMES: tuple[str, ...] = tuple(p.name for p in TIER_PROFILES)
REFERENCE_DISPLACEMENT_CC = 300.0

TIER_FREQUENCY_HZ: tuple[float, ...] = (65.0, 42.0, 35.0)
TIER_COMPRESSION_RATIO: tuple[float, ...] = (13.5, 12.5, 11.8)

# Medium-tier reference optimizer ranges (see designs/phoenix_v3_optimizer.SEARCH_BOUNDS).
_REF_AIR_SPRING_MAX_CC = (50.0, 82.0)
_REF_AIR_SPRING_MIN_CC = (18.0, 48.0)
_REF_GENERATOR_FORCE_N = (2200.0, 7800.0)
_REF_DAMPING_N_S_M = (18.0, 42.0)
_REF_COMBUSTION_GAIN = (1.25, 1.75)
_REF_BURN_DURATION_MS = (1.3, 2.4)
_REF_EXHAUST_OPEN_MS = (7.35, 8.8)
_REF_GENERATOR_FORCE_SCALE = (0.18, 3.0)
_REF_SPRING_DECOUPLE = (0.08, 0.45)
_REF_SPRING_REF_BAR = (10.0, 24.0)
_REF_EXPANSION_GAIN = (0.55, 0.95)
_REF_SPRING_COMPRESSION_GAIN = (1.0, 1.45)
_REF_LOAD_FRACTION = (0.62, 0.95)
_REF_IGNITION_MS = (3.6, 5.4)
_FREQ_SEARCH_LO_FRAC = 0.75
_FREQ_SEARCH_HI_FRAC = 1.45
_FREQ_SEARCH_ABS_LO_HZ = 25.0
_FREQ_SEARCH_ABS_HI_HZ = 110.0
_BOUND_MARGIN = 0.10
_MIN_AIR_SPRING_GAP_CC = 10.0
_GAIN_SPAN_FRAC = 0.12
_CR_ABS_MARGIN = 0.9


def tier_index_for_name(name: str) -> int:
    lower = name.lower()
    if "micro" in lower or "tier 1" in lower:
        return 0
    if "large" in lower or "tier 3" in lower:
        return 2
    return 1


def tier_displacement_cc(tier_index: int) -> float:
    return profile_for_tier_index(tier_index).total_displacement_cc


def _scaled_search_range(lo: float, hi: float, scale: float) -> tuple[float, float]:
    """Scale a medium-reference bound and add margin for tier-native exploration."""
    scaled_lo = lo * scale
    scaled_hi = hi * scale
    span = scaled_hi - scaled_lo
    return scaled_lo - _BOUND_MARGIN * span, scaled_hi + _BOUND_MARGIN * span


def _centered_range(
    center: float,
    *,
    rel_margin: float = 0.12,
    abs_margin: float | None = None,
) -> tuple[float, float]:
    margin = abs_margin if abs_margin is not None else center * rel_margin
    return center - margin, center + margin


def tier_search_bound_overrides(tier_index: int) -> dict[str, tuple[float, float]]:
    """Per-tier optimizer overrides keyed by SEARCH_BOUNDS parameter name."""
    idx = max(0, min(tier_index, len(TIER_DISPLACEMENT_CC) - 1))
    vol_ratio = TIER_DISPLACEMENT_CC[idx] / REFERENCE_DISPLACEMENT_CC
    air_scale = vol_ratio ** 0.45
    force_scale = vol_ratio ** 0.55
    damp_scale = vol_ratio ** 0.35
    gen_scale = vol_ratio ** 0.25
    native_hz = TIER_FREQUENCY_HZ[idx]
    native_cr = TIER_COMPRESSION_RATIO[idx]
    native = native_tier_config(idx)

    max_lo, max_hi = _scaled_search_range(*_REF_AIR_SPRING_MAX_CC, air_scale)
    min_lo, min_hi = _scaled_search_range(*_REF_AIR_SPRING_MIN_CC, air_scale)
    min_hi = min(min_hi, max_hi - _MIN_AIR_SPRING_GAP_CC)

    freq_lo = max(_FREQ_SEARCH_ABS_LO_HZ, native_hz * _FREQ_SEARCH_LO_FRAC)
    freq_hi = min(_FREQ_SEARCH_ABS_HI_HZ, native_hz * _FREQ_SEARCH_HI_FRAC)
    if freq_hi <= freq_lo:
        freq_hi = freq_lo + 5.0

    force_lo, force_hi = _scaled_search_range(*_REF_GENERATOR_FORCE_N, force_scale)
    damp_lo, damp_hi = _scaled_search_range(*_REF_DAMPING_N_S_M, damp_scale)
    gfs_lo, gfs_hi = _scaled_search_range(*_REF_GENERATOR_FORCE_SCALE, gen_scale)

    gain_lo, gain_hi = _centered_range(
        native.combustion_pressure_gain, rel_margin=_GAIN_SPAN_FRAC,
    )
    gain_lo = max(gain_lo, _REF_COMBUSTION_GAIN[0])
    gain_hi = min(gain_hi, _REF_COMBUSTION_GAIN[1])

    cr_lo, cr_hi = _centered_range(native_cr, abs_margin=_CR_ABS_MARGIN)
    cr_lo = max(cr_lo, 10.0)
    cr_hi = min(cr_hi, 15.5 if idx == 0 else 14.0)

    # ignition_ms: 0-10 cycle grid; <4.8 starts burn during compression near opposed TDC.
    if idx == 0:
        ign_lo, ign_hi = 4.0, 5.2
    elif idx == 2:
        ign_lo, ign_hi = 3.6, 5.3
    else:
        ign_lo, ign_hi = _REF_IGNITION_MS

    burn_lo, burn_hi = _scaled_search_range(*_REF_BURN_DURATION_MS, vol_ratio ** 0.15)
    burn_lo = max(burn_lo, 1.1)
    burn_hi = min(burn_hi, 2.6)

    overrides: dict[str, tuple[float, float]] = {
        "frequency_hz": (freq_lo, freq_hi),
        "air_spring_max_cc": (max_lo, max_hi),
        "air_spring_min_cc": (max(min_lo, 8.0), min_hi),
        "generator_rated_force_n": (force_lo, force_hi),
        "damping_n_s_m": (damp_lo, damp_hi),
        "generator_force_scale": (gfs_lo, min(gfs_hi, 3.5)),
        "generator_spring_decouple": _REF_SPRING_DECOUPLE,
        "air_spring_reference_bar": (
            max(8.0, _REF_SPRING_REF_BAR[0] * (air_scale ** 0.3)),
            min(26.0, _REF_SPRING_REF_BAR[1] * (air_scale ** 0.2)),
        ),
        "air_spring_expansion_gain": _REF_EXPANSION_GAIN,
        "air_spring_compression_gain": _REF_SPRING_COMPRESSION_GAIN,
        "exhaust_open_ms": _REF_EXHAUST_OPEN_MS,
        "load_fraction": _REF_LOAD_FRACTION,
        "combustion_pressure_gain": (gain_lo, gain_hi),
        "burn_duration_ms": (burn_lo, burn_hi),
        "compression_ratio": (cr_lo, cr_hi),
        "ignition_ms": (ign_lo, ign_hi),
    }

    if idx == 0:
        overrides.update({
            "load_fraction": (0.70, 0.95),
            "generator_force_scale": (max(gfs_lo, 0.35), min(gfs_hi, 3.2)),
        })
    elif idx == 2:
        # Large (750 cc): lower frequency, bigger springs, softer peak load, earlier ignition.
        overrides.update({
            "generator_spring_decouple": (0.10, 0.50),
            "air_spring_min_cc": (max(28.0, min_lo), min(min_hi, max_hi - 12.0)),
            "air_spring_expansion_gain": (0.48, 0.82),
            "air_spring_compression_gain": (1.0, 1.30),
            "exhaust_open_ms": (7.0, 8.9),
            "load_fraction": (0.58, 0.90),
            "generator_force_scale": (gfs_lo, min(gfs_hi, 2.8)),
            "ignition_ms": (3.5, 4.95),
            "burn_duration_ms": (max(1.1, burn_lo), min(2.0, burn_hi)),
            "combustion_pressure_gain": (
                max(gain_lo, native.combustion_pressure_gain * 0.92),
                min(gain_hi, native.combustion_pressure_gain * 1.12),
            ),
        })

    return overrides


def native_tuning_seed_dict(tier_index: int) -> dict[str, float]:
    """Default searchable parameters from native tier geometry (not medium JSON)."""
    native = native_tier_config(tier_index)
    base = PhoenixV3Config()
    return {
        "generator_force_scale": native.generator_force_scale,
        "generator_rated_force_n": native.generator_rated_force_n,
        "generator_spring_decouple": native.generator_spring_decouple,
        "air_spring_reference_bar": native.air_spring_reference_bar,
        "air_spring_max_cc": native.air_spring_volume_max_m3 * 1e6,
        "air_spring_min_cc": native.air_spring_volume_min_m3 * 1e6,
        "air_spring_expansion_gain": native.air_spring_expansion_gain,
        "air_spring_compression_gain": native.air_spring_compression_gain,
        "generator_adaptive_profile": 1.0 if native.generator_adaptive_profile else 0.0,
        "exhaust_open_ms": native.exhaust_open_ms,
        "load_fraction": native.load_fraction,
        "frequency_hz": native.frequency_hz,
        "combustion_pressure_gain": native.combustion_pressure_gain,
        "burn_duration_ms": native.burn_duration_ms,
        "damping_n_s_m": native.damping_n_s_m,
        "ignition_ms": base.ignition_ms,
        "compression_ratio": native.compression_ratio,
    }


def native_tier_config(tier_index: int) -> PhoenixV3Config:
    """Physical geometry for one cartridge tier (no breathing/generator tuning)."""
    idx = max(0, min(tier_index, len(TIER_DISPLACEMENT_CC) - 1))
    target_cc = TIER_DISPLACEMENT_CC[idx]
    base = PhoenixV3Config()
    volume_ratio = target_cc / REFERENCE_DISPLACEMENT_CC
    linear = volume_ratio ** (1.0 / 3.0)

    disp_per_side_m3 = target_cc * 0.5e-6
    bore = base.bore_m * linear
    half_stroke = base.half_stroke_m * linear
    mass = base.piston_mass_kg * (volume_ratio ** 0.75)

    pressure_gain = base.combustion_pressure_gain
    if idx == 0:
        pressure_gain *= 1.03
    elif idx == 2:
        pressure_gain *= 0.94

    air_vol_scale = volume_ratio ** 0.45
    gen_force = base.generator_rated_force_n * (volume_ratio ** 0.55)
    damping = base.damping_n_s_m * (volume_ratio ** 0.35)

    return replace(
        base,
        bore_m=bore,
        half_stroke_m=half_stroke,
        displacement_per_side_m3=disp_per_side_m3,
        compression_ratio=TIER_COMPRESSION_RATIO[idx],
        frequency_hz=TIER_FREQUENCY_HZ[idx],
        piston_mass_kg=mass,
        combustion_pressure_gain=pressure_gain,
        damping_n_s_m=damping,
        air_spring_volume_max_m3=base.air_spring_volume_max_m3 * air_vol_scale,
        air_spring_volume_min_m3=base.air_spring_volume_min_m3 * air_vol_scale,
        generator_rated_force_n=gen_force,
        generator_force_scale=base.generator_force_scale * (volume_ratio ** 0.25),
    )


def apply_tier_geometry(base: PhoenixV3Config, tier_index: int) -> PhoenixV3Config:
    """Replace geometry fields on ``base`` with the native tier layout."""
    native = native_tier_config(tier_index)
    geom_fields = (
        "bore_m",
        "half_stroke_m",
        "displacement_per_side_m3",
        "compression_ratio",
        "frequency_hz",
        "piston_mass_kg",
        "combustion_pressure_gain",
        "damping_n_s_m",
        "air_spring_volume_max_m3",
        "air_spring_volume_min_m3",
        "generator_rated_force_n",
        "generator_force_scale",
    )
    overrides = {f: getattr(native, f) for f in geom_fields}
    return replace(base, **overrides)


def load_tier_tuning_config(
    tier_index: int,
    tuning_path: Path | str | None = None,
    *,
    load_fraction: float | None = None,
) -> tuple[PhoenixV3Config, Path | None]:
    """Native tier geometry + optional best-tuning control parameters."""
    from designs.phoenix_v3_optimizer import TuningVector

    path = resolve_tier_tuning_path(tier_index, tuning_path)
    native = native_tier_config(tier_index)
    if path is None:
        cfg = native
    else:
        import json

        from designs.phoenix_v3_optimizer import search_bounds_for_tier

        payload = json.loads(path.read_text(encoding="utf-8"))
        bounds = search_bounds_for_tier(tier_index)
        cfg = TuningVector.from_dict(
            payload["parameters"], bounds=bounds,
        ).to_config(native, bounds=bounds)
    if load_fraction is not None:
        cfg = replace(cfg, load_fraction=load_fraction)
    return cfg, path


def config_for_atpe_tier(
    base: PhoenixV3Config,
    tier_index: int,
    *,
    load_fraction: float | None = None,
) -> PhoenixV3Config:
    """Tier geometry applied to an existing config (legacy helper)."""
    cfg = apply_tier_geometry(base, tier_index)
    if load_fraction is not None:
        cfg = replace(cfg, load_fraction=load_fraction)
    return cfg
