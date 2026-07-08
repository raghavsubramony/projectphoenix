"""Virtual Gate 1 bench matrix — systematic cartridge tests without a lab rig.

Runs load/speed sweeps across all ATPE tiers, evaluates PHOENIX-X12 acceptance
bands at every operating point, and rolls up uncertainty bands (Move G style)
for the sweet-spot cartridge. Also compares vehicle-level fuel when the twin
uses physics-derived tier efficiencies vs fixed efficiency tables.
"""

from __future__ import annotations

import csv
import random
import statistics
from dataclasses import dataclass
from pathlib import Path

from .config import phase1_config, with_gate1
from .drive_cycles import DriveCycles
from .montecarlo import Distribution
from .powertrain import Powertrain
from .simulation import run
from .single_cylinder import (
    Gate1BenchResult,
    SingleCylinderInputs,
    gate1_bench_at_load,
    simulate_1d_combustion,
    simulate_free_piston,
    tier_physics_profile,
    measure_gate1_bench,
    evaluate_gate1_bench,
)

# Seed-phase virtual bench matrix (matches ERS §Gate 1 bench acceptance grid).
DEFAULT_SPEED_RPM: tuple[float, ...] = (1800.0, 2200.0, 2600.0, 3000.0)
DEFAULT_LOAD_FRACTIONS: tuple[float, ...] = (0.25, 0.50, 0.75, 1.00)
TIER_LABELS: tuple[str, ...] = ("Tier 1 (micro)", "Tier 2 (medium)", "Tier 3 (large)")
TIER_DISPLACEMENT_CC: tuple[float, ...] = (100.0, 300.0, 750.0)

# Sweet-spot reference for uncertainty bands and investor snapshot.
SWEET_SPOT_SPEED_RPM = 2600.0
SWEET_SPOT_LOAD = 0.75
SWEET_SPOT_TIER_INDEX = 1


@dataclass(frozen=True)
class Gate1MatrixCell:
    """One virtual bench operating point."""

    tier_index: int
    tier_label: str
    speed_rpm: float
    load_fraction: float
    bench: Gate1BenchResult

    @property
    def passed(self) -> bool:
        return self.bench.passed

    @property
    def checks_passed(self) -> int:
        return sum(1 for c in self.bench.checks if c.passed)


@dataclass(frozen=True)
class Gate1MatrixSummary:
    """Rollup of a full virtual bench matrix."""

    cells: tuple[Gate1MatrixCell, ...]
    speed_rpm_grid: tuple[float, ...]
    load_fraction_grid: tuple[float, ...]

    @property
    def total_cells(self) -> int:
        return len(self.cells)

    @property
    def cells_passed(self) -> int:
        return sum(1 for c in self.cells if c.passed)

    @property
    def pass_rate_pct(self) -> float:
        if not self.cells:
            return 0.0
        return 100.0 * self.cells_passed / self.total_cells

    def cells_for_tier(self, tier_index: int) -> tuple[Gate1MatrixCell, ...]:
        return tuple(c for c in self.cells if c.tier_index == tier_index)

    def sweet_spot(self) -> Gate1MatrixCell:
        for cell in self.cells:
            if (cell.tier_index == SWEET_SPOT_TIER_INDEX
                    and abs(cell.speed_rpm - SWEET_SPOT_SPEED_RPM) < 1.0
                    and abs(cell.load_fraction - SWEET_SPOT_LOAD) < 0.01):
                return cell
        return self.cells[0]


@dataclass(frozen=True)
class Gate1VehicleComparison:
    """Fuel economy on the same cycle: fixed tier tables vs Gate 1 physics."""

    cycle_name: str
    fuel_l_per_100km_tables: float
    fuel_l_per_100km_gate1: float

    @property
    def delta_l_per_100km(self) -> float:
        return self.fuel_l_per_100km_gate1 - self.fuel_l_per_100km_tables

    @property
    def delta_pct(self) -> float:
        if self.fuel_l_per_100km_tables <= 0.0:
            return 0.0
        return 100.0 * self.delta_l_per_100km / self.fuel_l_per_100km_tables


def run_gate1_matrix(
    speed_rpm_grid: tuple[float, ...] = DEFAULT_SPEED_RPM,
    load_fraction_grid: tuple[float, ...] = DEFAULT_LOAD_FRACTIONS,
    *,
    prefer_cantera: bool = False,
    generator_efficiency: float = 0.96,
    tier_displacements_cc: tuple[float, ...] = TIER_DISPLACEMENT_CC,
) -> Gate1MatrixSummary:
    """Sweep speed × load × tier and evaluate Gate 1 acceptance at each point."""
    cells: list[Gate1MatrixCell] = []
    for tier_index, disp_cc in enumerate(tier_displacements_cc):
        label = TIER_LABELS[tier_index] if tier_index < len(TIER_LABELS) else f"Tier {tier_index + 1}"
        for speed in speed_rpm_grid:
            for load in load_fraction_grid:
                bench = gate1_bench_at_load(
                    speed_rpm=speed,
                    load_fraction=load,
                    displacement_cc=disp_cc,
                    generator_efficiency=generator_efficiency,
                    prefer_cantera=prefer_cantera,
                    tier_index=tier_index,
                )
                cells.append(Gate1MatrixCell(
                    tier_index=tier_index,
                    tier_label=label,
                    speed_rpm=speed,
                    load_fraction=load,
                    bench=bench,
                ))
    return Gate1MatrixSummary(
        cells=tuple(cells),
        speed_rpm_grid=speed_rpm_grid,
        load_fraction_grid=load_fraction_grid,
    )


def gate1_bench_uncertainty(
    trials: int = 64,
    seed: int = 0,
    *,
    speed_rpm: float = SWEET_SPOT_SPEED_RPM,
    load_fraction: float = SWEET_SPOT_LOAD,
    tier_index: int = SWEET_SPOT_TIER_INDEX,
    displacement_cc: float = TIER_DISPLACEMENT_CC[SWEET_SPOT_TIER_INDEX],
    prefer_cantera: bool = False,
    generator_efficiency: float = 0.96,
) -> dict[str, Distribution]:
    """Monte-Carlo bands on sweet-spot bench metrics (input perturbation)."""
    rng = random.Random(seed)
    nominal = gate1_bench_at_load(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_cc=displacement_cc,
        generator_efficiency=generator_efficiency,
        prefer_cantera=prefer_cantera,
        tier_index=tier_index,
    )
    nm = nominal.measurement

    def _trial() -> Gate1BenchResult:
        spd = speed_rpm * rng.lognormvariate(0.0, 0.03)
        load = max(0.05, min(1.0, load_fraction * rng.lognormvariate(0.0, 0.05)))
        gen_eta = max(0.90, min(0.99, generator_efficiency * rng.lognormvariate(0.0, 0.02)))
        mech = max(0.90, min(0.99, 0.95 * rng.lognormvariate(0.0, 0.02)))
        compression_ratio, fp_cfg = tier_physics_profile(tier_index)
        inp = SingleCylinderInputs(
            speed_rpm=spd,
            load_fraction=load,
            displacement_m3=max(1e-7, displacement_cc * 1e-6),
            compression_ratio=compression_ratio,
            generator_efficiency=gen_eta,
            mechanical_efficiency=mech,
        )
        comb = simulate_1d_combustion(inp, prefer_cantera=prefer_cantera)
        fp = simulate_free_piston(comb, fp_cfg, speed_rpm=spd)
        meas = measure_gate1_bench(comb, fp, fp_cfg, inp, tier_index, gen_eta)
        return evaluate_gate1_bench(
            meas, load_fraction=load, tier_index=tier_index,
        )

    samples: list[Gate1BenchResult] = [_trial() for _ in range(trials)]

    def _dist(name: str, unit: str, nominal_val: float,
              values: list[float]) -> Distribution:
        values_sorted = sorted(values)
        n = len(values_sorted)
        p05 = values_sorted[max(0, int(0.05 * (n - 1)))]
        p50 = values_sorted[n // 2]
        p95 = values_sorted[min(n - 1, int(0.95 * (n - 1)))]
        return Distribution(
            name=name,
            unit=unit,
            n=n,
            nominal=nominal_val,
            mean=statistics.mean(values),
            std=statistics.pstdev(values) if n > 1 else 0.0,
            p05=p05,
            p50=p50,
            p95=p95,
            minimum=values_sorted[0],
            maximum=values_sorted[-1],
        )

    return {
        "electric_efficiency": _dist(
            "Gate 1 electric efficiency (fuel→electrical)",
            "",
            nm.electric_efficiency,
            [s.measurement.electric_efficiency for s in samples],
        ),
        "peak_power_kw": _dist(
            "Gate 1 peak cartridge power",
            "kW",
            nm.peak_power_kw,
            [s.measurement.peak_power_kw for s in samples],
        ),
        "imep_bar": _dist(
            "Gate 1 IMEP",
            "bar",
            nm.imep_bar,
            [s.measurement.imep_bar for s in samples],
        ),
    }


def gate1_vehicle_fuel_comparison(
    cycle_name: str = "highway",
    duration_s: float = 1200.0,
    *,
    prefer_cantera: bool = False,
) -> Gate1VehicleComparison:
    """Compare charge-sustaining fuel: fixed tier η vs Gate 1 physics path."""
    if cycle_name == "highway":
        cycle = DriveCycles.highway(duration_s=duration_s)
    elif cycle_name == "mixed":
        cycle = DriveCycles.mixed(duration_s=duration_s)
    else:
        cycle = DriveCycles.urban(duration_s=duration_s)

    base_cfg = phase1_config()
    tables_twin = Powertrain(base_cfg)
    gate1_twin = Powertrain(with_gate1(base_cfg, prefer_cantera=prefer_cantera))

    r_tables = run(tables_twin, cycle)
    r_gate1 = run(gate1_twin, cycle)

    return Gate1VehicleComparison(
        cycle_name=cycle_name,
        fuel_l_per_100km_tables=r_tables.fuel_l_per_100km,
        fuel_l_per_100km_gate1=r_gate1.fuel_l_per_100km,
    )


def gate1_matrix_table(summary: Gate1MatrixSummary) -> str:
    """Compact pass/fail grid for one tier (medium tier by default)."""
    tier = SWEET_SPOT_TIER_INDEX
    cells = summary.cells_for_tier(tier)
    if not cells:
        return "=== Gate 1 virtual bench matrix (no cells) ==="

    loads = summary.load_fraction_grid
    speeds = summary.speed_rpm_grid
    lookup = {(c.speed_rpm, c.load_fraction): c for c in cells}

    lines = [
        f"=== Gate 1 virtual bench — {TIER_LABELS[tier]} "
        f"({summary.cells_passed}/{summary.total_cells} cells pass) ===",
        "  load \\ speed | " + " | ".join(f"{int(s):>4}" for s in speeds),
        "  " + "-" * (14 + 7 * len(speeds)),
    ]
    for load in loads:
        row = [f"  {load:4.0%}       |"]
        for speed in speeds:
            cell = lookup.get((speed, load))
            if cell is None:
                row.append("  ? ")
            else:
                mark = "PASS" if cell.passed else f"{cell.checks_passed}/{len(cell.bench.checks)}"
                row.append(f"{mark:>4}")
        lines.append("".join(row))
    return "\n".join(lines)


def gate1_matrix_report(
    summary: Gate1MatrixSummary | None = None,
    *,
    prefer_cantera: bool = False,
    uncertainty_trials: int = 64,
    seed: int = 0,
) -> str:
    """Full virtual bench report for demos, verify, and evidence pack."""
    if summary is None:
        summary = run_gate1_matrix(prefer_cantera=prefer_cantera)

    sweet = summary.sweet_spot()
    m = sweet.bench.measurement
    bands = gate1_bench_uncertainty(
        trials=uncertainty_trials,
        seed=seed,
        prefer_cantera=prefer_cantera,
    )
    vehicle = gate1_vehicle_fuel_comparison(prefer_cantera=prefer_cantera)

    lines = [
        "=== Virtual Gate 1 bench matrix (simulation — not measured) ===",
        f"  Grid: {len(summary.speed_rpm_grid)} speeds × "
        f"{len(summary.load_fraction_grid)} loads × "
        f"{len(TIER_DISPLACEMENT_CC)} tiers = {summary.total_cells} cells",
        f"  Overall pass rate: {summary.pass_rate_pct:.0f}% "
        f"({summary.cells_passed}/{summary.total_cells})",
        "",
        gate1_matrix_table(summary),
        "",
        "=== Sweet-spot snapshot (medium tier, 2600 rpm, 75% load) ===",
        f"  Stroke (mm)         : {m.stroke_mm:.1f}",
        f"  Peak power (kW)     : {m.peak_power_kw:.1f}",
        f"  Electric efficiency : {m.electric_efficiency:.3f}",
        f"  IMEP (bar)          : {m.imep_bar:.1f}",
        f"  Bench checks        : "
        f"{sum(1 for c in sweet.bench.checks if c.passed)}/{len(sweet.bench.checks)} pass",
        "",
        "=== Sweet-spot uncertainty bands (perturbed inputs) ===",
    ]
    for band in bands.values():
        lines.append(f"  {band.band()}")
    lines += [
        "",
        "=== Vehicle twin: fixed tier tables vs Gate 1 physics (AWD SUV, highway CS) ===",
        f"  Fixed tables      : {vehicle.fuel_l_per_100km_tables:.2f} L/100 km",
        f"  Gate 1 physics    : {vehicle.fuel_l_per_100km_gate1:.2f} L/100 km",
        f"  Delta             : {vehicle.delta_l_per_100km:+.2f} L/100 km "
        f"({vehicle.delta_pct:+.1f}%)",
        "",
        "  DISCLAIMER: virtual bench only — hardware measurement required before",
        "  commercial claims. Prefix external statements with 'simulation shows…'.",
    ]
    return "\n".join(lines)


GATE1_MATRIX_CSV_COLUMNS: tuple[str, ...] = (
    "tier_index",
    "tier_label",
    "speed_rpm",
    "load_fraction",
    "passed",
    "checks_passed",
    "checks_total",
    "stroke_mm",
    "peak_power_kw",
    "core_temp_c",
    "bearing_runout_mm",
    "exhaust_temp_c",
    "electric_efficiency",
    "imep_bar",
    "peak_pressure_bar",
    "knock_index",
    "peak_piston_speed_ms",
)


def gate1_matrix_csv_rows(summary: Gate1MatrixSummary) -> list[dict[str, str | float | int | bool]]:
    """Flatten matrix cells into rows suitable for CSV export."""
    rows: list[dict[str, str | float | int | bool]] = []
    for cell in summary.cells:
        m = cell.bench.measurement
        rows.append({
            "tier_index": cell.tier_index,
            "tier_label": cell.tier_label,
            "speed_rpm": cell.speed_rpm,
            "load_fraction": cell.load_fraction,
            "passed": cell.passed,
            "checks_passed": cell.checks_passed,
            "checks_total": len(cell.bench.checks),
            "stroke_mm": m.stroke_mm,
            "peak_power_kw": m.peak_power_kw,
            "core_temp_c": m.core_temp_c,
            "bearing_runout_mm": m.bearing_runout_mm,
            "exhaust_temp_c": m.exhaust_temp_c,
            "electric_efficiency": m.electric_efficiency,
            "imep_bar": m.imep_bar,
            "peak_pressure_bar": m.peak_pressure_bar,
            "knock_index": m.knock_index,
            "peak_piston_speed_ms": m.peak_piston_speed_ms,
        })
    return rows


def write_gate1_matrix_csv(
    path: Path | str,
    summary: Gate1MatrixSummary | None = None,
    *,
    prefer_cantera: bool = False,
) -> Path:
    """Write the virtual bench matrix to a CSV file; return the resolved path."""
    if summary is None:
        summary = run_gate1_matrix(prefer_cantera=prefer_cantera)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = gate1_matrix_csv_rows(summary)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GATE1_MATRIX_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out
