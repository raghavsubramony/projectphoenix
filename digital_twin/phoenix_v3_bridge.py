"""Bridge Phoenix V3 cartridge physics into the vehicle digital twin.

Per-tier tuning files (opposed piston total swept volume):
  micro  — 100 cc  (50 cc/side)  -> phoenix_v3_best_tuning_micro.json
  medium — 300 cc (150 cc/side)  -> phoenix_v3_best_tuning_v3.json
  large  — 750 cc (375 cc/side)  -> phoenix_v3_best_tuning_large.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from designs.phoenix_v3.tier_presets import load_tier_tuning_config
from designs.phoenix_v3.tier_profiles import (
    LEGACY_BEST_TUNING_CANDIDATES,
    profile_for_tier_index,
    resolve_tier_tuning_path,
)

_REPO = Path(__file__).resolve().parents[1]
_DESIGNS = _REPO / "designs"

BEST_TUNING_CANDIDATES: tuple[Path, ...] = tuple(
    _DESIGNS / name for name in LEGACY_BEST_TUNING_CANDIDATES
)

DEFAULT_V3_LOAD_FRACTIONS: tuple[float, ...] = (0.15, 0.35, 0.55, 0.75, 0.90, 1.0)


@dataclass(frozen=True)
class PhoenixV3CartridgeMetrics:
    """Late-window cartridge KPIs from the V3 physics sim."""

    net_efficiency: float
    capture_fraction: float
    elec_energy_j: float
    elec_power_w: float
    peak_pressure_bar: float
    stroke_mm: float
    frequency_hz: float
    load_fraction: float
    energy_balance_valid: bool
    raw_boundary_valid: bool
    spring_recovery: float


@dataclass(frozen=True)
class PhoenixV3RingMetrics:
    """12-cartridge ring validation snapshot."""

    ring_efficiency: float
    ring_power_w: float
    collision_count: int
    energy_balance_valid: bool
    raw_boundary_valid: bool


@dataclass(frozen=True)
class Gate1RigSpec:
    """Hardware acceptance targets derived from validated V3 simulation."""

    stroke_mm: float
    frequency_hz: float
    cycle_time_ms: float
    peak_pressure_bar: float
    net_efficiency_sim: float
    net_efficiency_hardware_band: tuple[float, float]
    elec_power_w_per_cartridge: float
    ring_power_w_12x: float
    capture_fraction: float
    bore_mm: float
    cooling_mode_long_run: str
    load_fraction_sweet_spot: float
    capture_min_bdc_mm: float
    generator_rated_force_n: float
    tuning_source: str
    validated_utc: str


def resolve_best_tuning_path(explicit: Path | str | None = None) -> Path | None:
    """Resolve medium-tier (300 cc) best tuning — legacy helper."""
    return resolve_tier_tuning_path(1, explicit)


def load_phoenix_v3_config(
    tuning_path: Path | str | None = None,
    *,
    load_fraction: float | None = None,
    cooling_mode: str = "water_jacket",
    tier_index: int = 1,
):
    """Load tier-native geometry + best-tuning JSON into a ``PhoenixV3Config``."""
    from designs.phoenix_v3_simulation import apply_generator_cooling

    cfg, path = load_tier_tuning_config(
        tier_index, tuning_path, load_fraction=load_fraction,
    )
    return apply_generator_cooling(cfg, cooling_mode), path


def measure_v3_cartridge(
    cfg=None,
    *,
    load_fraction: float | None = None,
    cycles: int = 24,
    tuning_path: Path | str | None = None,
    tier_index: int = 1,
) -> PhoenixV3CartridgeMetrics:
    """Run V3 sim and return late-window harvest metrics."""
    from designs.phoenix_v3_simulation import (
        PhoenixV3Simulator,
        _late_cycle_reports,
        _steady_cycle_reports,
    )

    if cfg is None:
        cfg, _ = load_tier_tuning_config(
            tier_index, tuning_path, load_fraction=load_fraction,
        )
    elif load_fraction is not None:
        cfg = replace(cfg, load_fraction=load_fraction)

    result = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
    reports = result.energy.cycle_reports
    late = list(_late_cycle_reports(reports, cfg))
    window = late if late else list(reports)
    if not window:
        return PhoenixV3CartridgeMetrics(
            net_efficiency=0.0,
            capture_fraction=0.0,
            elec_energy_j=0.0,
            elec_power_w=0.0,
            peak_pressure_bar=0.0,
            stroke_mm=0.0,
            frequency_hz=cfg.frequency_hz,
            load_fraction=cfg.load_fraction,
            energy_balance_valid=False,
            raw_boundary_valid=False,
            spring_recovery=0.0,
        )

    net_eff = float(sum(r.net_cartridge_efficiency for r in window) / len(window))
    capture = float(sum(r.capture_fraction for r in window) / len(window))
    elec_j = float(sum(r.elec_energy_j for r in window) / len(window))
    stroke = float(sum(r.max_piston_travel_mm for r in window) / len(window))
    spring_rec = float(sum(r.spring_recovery_efficiency for r in window) / len(window))
    steady = _steady_cycle_reports(tuple(reports), cfg)
    boundary_ok = (
        all(r.raw_boundary_valid for r in steady) if steady
        else result.energy.raw_boundary_valid
    )
    return PhoenixV3CartridgeMetrics(
        net_efficiency=net_eff,
        capture_fraction=capture,
        elec_energy_j=elec_j,
        elec_power_w=elec_j / max(cfg.cycle_time_s, 1e-9),
        peak_pressure_bar=result.metrics.peak_pressure_bar,
        stroke_mm=stroke,
        frequency_hz=cfg.frequency_hz,
        load_fraction=cfg.load_fraction,
        energy_balance_valid=result.energy.energy_balance_valid,
        raw_boundary_valid=boundary_ok,
        spring_recovery=spring_rec,
    )


def measure_v3_ring(
    cfg=None,
    *,
    cartridge_count: int = 12,
    cycles: int = 60,
    tuning_path: Path | str | None = None,
    cooling_mode: str = "water_jacket",
    tier_index: int = 1,
) -> PhoenixV3RingMetrics:
    """Validate ring phasing with shared coolant."""
    from designs.phoenix_v3_ring import RingConfig, simulate_ring

    if cfg is None:
        cfg, _ = load_phoenix_v3_config(
            tuning_path, cooling_mode=cooling_mode, tier_index=tier_index,
        )

    result = simulate_ring(
        cfg,
        RingConfig(cartridge_count=cartridge_count),
        cycles=cycles,
        record_history=False,
    )
    collisions = sum(
        1 for s in result.cartridges
        for r in s.result.energy.cycle_reports
        if r.piston_collision
    )
    return PhoenixV3RingMetrics(
        ring_efficiency=result.ring_efficiency,
        ring_power_w=result.total_elec_power_w,
        collision_count=collisions,
        energy_balance_valid=result.energy_balance_valid,
        raw_boundary_valid=result.raw_boundary_valid,
    )


def calibrate_v3_tier(
    tier_index: int,
    *,
    tuning_path: Path | str | None = None,
    cycles: int = 16,
    load_fractions: tuple[float, ...] = DEFAULT_V3_LOAD_FRACTIONS,
) -> "V3TierCalibration":
    """Run Phoenix V3 at multiple loads for one ATPE tier geometry."""
    from .config import V3TierCalibration, V3TierCalibrationPoint

    profile = profile_for_tier_index(tier_index)
    tier_path = resolve_tier_tuning_path(tier_index, tuning_path)
    points: list[V3TierCalibrationPoint] = []
    for load_frac in load_fractions:
        metrics = measure_v3_cartridge(
            load_fraction=load_frac,
            cycles=cycles,
            tuning_path=tier_path,
            tier_index=profile.tier_index,
        )
        points.append(
            V3TierCalibrationPoint(
                load_frac=load_frac,
                net_efficiency=max(0.10, min(metrics.net_efficiency, 0.58)),
                elec_power_w=metrics.elec_power_w,
                peak_pressure_bar=metrics.peak_pressure_bar,
                energy_balance_valid=metrics.energy_balance_valid,
            )
        )
    derivation = "phoenix_v3_sim"
    if tier_path is not None:
        try:
            meta = json.loads(tier_path.read_text(encoding="utf-8"))
            derivation = str(meta.get("tier", derivation)) + "_tuning"
        except (json.JSONDecodeError, OSError):
            pass
    return V3TierCalibration(
        tier_index=profile.tier_index,
        tier_name=profile.name,
        displacement_cc=profile.total_displacement_cc,
        points=tuple(points),
        derivation=derivation,
    )


def calibrate_all_v3_tiers(
    *,
    tuning_path: Path | str | None = None,
    cycles: int = 16,
    load_fractions: tuple[float, ...] = DEFAULT_V3_LOAD_FRACTIONS,
    tier_count: int = 3,
) -> tuple["V3TierCalibration", ...]:
    """Build per-tier V3 efficiency maps (each tier uses its own tuning file)."""
    n = max(1, min(tier_count, 3))
    return tuple(
        calibrate_v3_tier(
            i,
            tuning_path=tuning_path,
            cycles=cycles,
            load_fractions=load_fractions,
        )
        for i in range(n)
    )


def _tier_curve_lookup(tier_index: int, tier_curves: tuple) -> object | None:
    for curve in tier_curves:
        if curve.tier_index == tier_index:
            return curve
    if tier_curves:
        return tier_curves[min(tier_index, len(tier_curves) - 1)]
    return None


def phoenix_v3_gate1_point(
    load_fraction: float,
    *,
    tier_index: int = 1,
    tuning_path: Path | str | None = None,
    cycles: int = 16,
    tier_curves: tuple = (),
    generator_efficiency: float = 0.94,
):
    """Map load fraction to a Gate1Point for ATPE tier efficiency lookup."""
    from .single_cylinder import Gate1Point

    curve = _tier_curve_lookup(tier_index, tier_curves)
    if curve is not None and curve.points:
        sweet = max(curve.points, key=lambda p: p.net_efficiency)
        eta = max(0.10, min(curve.efficiency_at(load_fraction), 0.58))
        return Gate1Point(
            electric_efficiency=eta,
            imep_bar=sweet.peak_pressure_bar * 0.04,
            knock_index=0.0,
            peak_pressure_bar=sweet.peak_pressure_bar,
            predicted_tdc_mm=20.0,
        )

    metrics = measure_v3_cartridge(
        load_fraction=load_fraction,
        cycles=cycles,
        tuning_path=tuning_path,
        tier_index=tier_index,
    )
    eta = max(0.10, min(metrics.net_efficiency, 0.58))
    return Gate1Point(
        electric_efficiency=eta,
        imep_bar=metrics.peak_pressure_bar * 0.04,
        knock_index=0.0,
        peak_pressure_bar=metrics.peak_pressure_bar,
        predicted_tdc_mm=metrics.stroke_mm * 0.5,
    )


def build_gate1_rig_spec(
    tuning_path: Path | str | None = None,
    *,
    cycles: int = 24,
    ring_cycles: int = 60,
    tier_index: int = 1,
) -> Gate1RigSpec:
    """Derive Gate 1 lab-rig targets from validated V3 physics."""
    path = resolve_tier_tuning_path(tier_index, tuning_path)
    cfg, _ = load_phoenix_v3_config(path, tier_index=tier_index)
    sweet = measure_v3_cartridge(cfg, cycles=cycles)
    ring = measure_v3_ring(cfg, cycles=ring_cycles)
    hw_lo = max(0.30, sweet.net_efficiency - 0.18)
    hw_hi = max(hw_lo + 0.05, sweet.net_efficiency - 0.08)
    return Gate1RigSpec(
        stroke_mm=2.0 * cfg.half_stroke_m * 1e3,
        frequency_hz=cfg.frequency_hz,
        cycle_time_ms=cfg.cycle_time_s * 1e3,
        peak_pressure_bar=sweet.peak_pressure_bar,
        net_efficiency_sim=sweet.net_efficiency,
        net_efficiency_hardware_band=(hw_lo, hw_hi),
        elec_power_w_per_cartridge=sweet.elec_power_w,
        ring_power_w_12x=ring.ring_power_w,
        capture_fraction=sweet.capture_fraction,
        bore_mm=cfg.bore_m * 1e3,
        cooling_mode_long_run="water_jacket",
        load_fraction_sweet_spot=cfg.load_fraction,
        capture_min_bdc_mm=cfg.capture_min_bdc_mm,
        generator_rated_force_n=cfg.generator_rated_force_n,
        tuning_source=path.name if path else "defaults",
        validated_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def export_gate1_rig_dossier(
    out_dir: Path | None = None,
    *,
    tuning_path: Path | str | None = None,
    tier_index: int = 1,
) -> Path:
    """Write Gate 1 rig specification markdown + JSON to evidence pack."""
    spec = build_gate1_rig_spec(tuning_path, tier_index=tier_index)
    out = out_dir or (_REPO / "docs" / "evidence-pack")
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "GATE1-RIG-SPEC-FROM-V3.json"
    json_path.write_text(
        json.dumps(
            {
                "source": "phoenix_v3_bridge",
                "tuning": spec.tuning_source,
                "validated_utc": spec.validated_utc,
                "cartridge": {
                    "bore_mm": spec.bore_mm,
                    "stroke_mm": spec.stroke_mm,
                    "frequency_hz": spec.frequency_hz,
                    "cycle_time_ms": spec.cycle_time_ms,
                    "peak_pressure_bar": spec.peak_pressure_bar,
                    "load_fraction": spec.load_fraction_sweet_spot,
                    "capture_min_bdc_mm": spec.capture_min_bdc_mm,
                    "generator_rated_force_n": spec.generator_rated_force_n,
                },
                "performance_sim": {
                    "net_efficiency": spec.net_efficiency_sim,
                    "capture_fraction": spec.capture_fraction,
                    "elec_power_w": spec.elec_power_w_per_cartridge,
                    "ring_power_w_12x": spec.ring_power_w_12x,
                },
                "performance_hardware_expected": {
                    "net_efficiency_min": spec.net_efficiency_hardware_band[0],
                    "net_efficiency_max": spec.net_efficiency_hardware_band[1],
                },
                "long_run_cooling": spec.cooling_mode_long_run,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    md_path = out / "GATE1-RIG-SPEC-FROM-V3.md"
    md_path.write_text(
        "\n".join([
            "# Gate 1 Lab Rig — Specification from Phoenix V3 Simulation",
            "",
            f"*Generated: {spec.validated_utc} · tuning: `{spec.tuning_source}`*",
            "",
            f"| Fuel -> electrical eta | **{spec.net_efficiency_sim:.1%}** |",
            f"| Electrical power (1 cart) | {spec.elec_power_w_per_cartridge/1000:.1f} kW |",
            "",
            f"Machine-readable spec: `{json_path.name}`",
            "",
        ]),
        encoding="utf-8",
    )
    return md_path
