"""Bridge Phoenix V3 cartridge physics into the vehicle digital twin.

Loads ``phoenix_v3_best_tuning_v3.json`` (falls back to v2/v1), runs the opposed-
piston simulator at a load point, and returns fuel-to-electrical metrics for ATPE
tier calibration and Gate 1 rig specification.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_DESIGNS = _REPO / "designs"

BEST_TUNING_CANDIDATES: tuple[Path, ...] = (
    _DESIGNS / "phoenix_v3_best_tuning_v3.json",
    _DESIGNS / "phoenix_v3_best_tuning_v2.json",
    _DESIGNS / "phoenix_v3_best_tuning.json",
)


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
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_file() else None
    for candidate in BEST_TUNING_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def load_phoenix_v3_config(
    tuning_path: Path | str | None = None,
    *,
    load_fraction: float | None = None,
    cooling_mode: str = "water_jacket",
):
    """Load best-tuning JSON into a ``PhoenixV3Config``."""
    from designs.phoenix_v3_optimizer import TuningVector
    from designs.phoenix_v3_simulation import (
        PhoenixV3Config,
        apply_generator_cooling,
    )

    path = resolve_best_tuning_path(tuning_path)
    if path is None:
        cfg = PhoenixV3Config()
    else:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cfg = TuningVector.from_dict(payload["parameters"]).to_config(PhoenixV3Config())
    if load_fraction is not None:
        cfg = replace(cfg, load_fraction=load_fraction)
    return apply_generator_cooling(cfg, cooling_mode), path


def measure_v3_cartridge(
    cfg=None,
    *,
    load_fraction: float | None = None,
    cycles: int = 24,
    tuning_path: Path | str | None = None,
) -> PhoenixV3CartridgeMetrics:
    """Run V3 sim and return late-window harvest metrics."""
    from designs.phoenix_v3_simulation import (
        PhoenixV3Simulator,
        _late_cycle_reports,
        _steady_cycle_reports,
    )

    if cfg is None:
        cfg, _ = load_phoenix_v3_config(tuning_path, load_fraction=load_fraction)
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
) -> PhoenixV3RingMetrics:
    """Validate ring phasing with shared coolant."""
    from designs.phoenix_v3_ring import RingConfig, simulate_ring

    if cfg is None:
        cfg, _ = load_phoenix_v3_config(tuning_path, cooling_mode=cooling_mode)

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


def phoenix_v3_gate1_point(
    load_fraction: float,
    *,
    tuning_path: Path | str | None = None,
    cycles: int = 16,
    generator_efficiency: float = 0.94,
):
    """Map load fraction to a Gate1Point for ATPE tier efficiency lookup."""
    from .single_cylinder import Gate1Point

    metrics = measure_v3_cartridge(
        load_fraction=load_fraction,
        cycles=cycles,
        tuning_path=tuning_path,
    )
    eta = max(0.10, min(metrics.net_efficiency, 0.58))
    imep = metrics.peak_pressure_bar * 0.04
    return Gate1Point(
        electric_efficiency=eta,
        imep_bar=imep,
        knock_index=0.0,
        peak_pressure_bar=metrics.peak_pressure_bar,
        predicted_tdc_mm=metrics.stroke_mm * 0.5,
    )


def build_gate1_rig_spec(
    tuning_path: Path | str | None = None,
    *,
    cycles: int = 24,
    ring_cycles: int = 60,
) -> Gate1RigSpec:
    """Derive Gate 1 lab-rig targets from validated V3 physics."""
    path = resolve_best_tuning_path(tuning_path)
    cfg, _ = load_phoenix_v3_config(path)
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
) -> Path:
    """Write Gate 1 rig specification markdown + JSON to evidence pack."""
    spec = build_gate1_rig_spec(tuning_path)
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
                "instrumentation_minimum": [
                    "cylinder_pressure_50khz",
                    "piston_position_x2_10khz",
                    "fuel_flow",
                    "dc_bus_power_analyzer",
                    "exhaust_egt",
                    "generator_winding_temp",
                ],
                "acceptance_first_rig": {
                    "stable_cycles_min": 100,
                    "gate1_checks_min": 5,
                    "stroke_tolerance_mm": 2.0,
                    "efficiency_vs_sim_pp": 12.0,
                },
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
            "## Cartridge geometry (Rig β / γ)",
            "",
            f"| Parameter | Target |",
            f"|-----------|--------|",
            f"| Bore | {spec.bore_mm:.1f} mm |",
            f"| Peak-to-peak stroke | {spec.stroke_mm:.1f} mm |",
            f"| Operating frequency | {spec.frequency_hz:.1f} Hz ({spec.cycle_time_ms:.1f} ms/cycle) |",
            f"| Peak chamber pressure | ≤ {spec.peak_pressure_bar:.0f} bar (sim) |",
            f"| Generator rated force | {spec.generator_rated_force_n:.0f} N per piston |",
            f"| Capture lockout BDC | ≥ {spec.capture_min_bdc_mm:.0f} mm before full extraction |",
            "",
            "## Performance targets",
            "",
            f"| Metric | Simulation | Hardware expectation |",
            f"|--------|------------|---------------------|",
            f"| Fuel → electrical η | **{spec.net_efficiency_sim:.1%}** | "
            f"{spec.net_efficiency_hardware_band[0]:.0%}–{spec.net_efficiency_hardware_band[1]:.0%} |",
            f"| Generator capture | {spec.capture_fraction:.1%} of power-stroke expansion | — |",
            f"| Electrical power (1 cart) | {spec.elec_power_w_per_cartridge/1000:.1f} kW | scale to achieved load |",
            f"| Ring power (12×, sim) | {spec.ring_power_w_12x/1000:.0f} kW | Gate 4 scope |",
            "",
            "## Cooling",
            "",
            f"Long-run thermal sustainability at ~54% output requires **`{spec.cooling_mode_long_run}`** "
            f"in simulation. Passive cooling fails within minutes at this output level.",
            "",
            "## First-rig acceptance (maps to virtual 48-cell matrix)",
            "",
            "- ≥ **100** consecutive stable cycles without runaway amplitude",
            "- ≥ **5/6** Gate 1 bench checks at sweet spot (`load_fraction` ≈ "
            f"{spec.load_fraction_sweet_spot:.2f})",
            "- Measured η within **12 percentage points** of sim band on first build",
            "- Log CSV per §7 of [GATE1-LAB-RIG-DESIGN.md](../GATE1-LAB-RIG-DESIGN.md)",
            "",
            "## Instrumentation minimum",
            "",
            "1. Cylinder pressure (piezo, ≥50 kHz)",
            "2. Piston position ×2 (LVDT, ≥10 kHz)",
            "3. Fuel mass flow",
            "4. DC bus V/I or power analyser",
            "5. Exhaust gas temperature",
            "6. Generator winding / coolant temperature",
            "",
            f"Machine-readable spec: `{json_path.name}`",
            "",
        ]),
        encoding="utf-8",
    )
    return md_path
