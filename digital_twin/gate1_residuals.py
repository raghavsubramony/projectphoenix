"""Gate 1 rig CSV schema + twin residual report.

Matches ``docs/GATE1-LAB-RIG-DESIGN.md`` §7. Measured rows are compared to a
fresh twin evaluation at the same (tier, speed, load) point. Residuals are
``measured - twin`` so a positive efficiency residual means the lab beat the
model.
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .single_cylinder import gate1_bench_at_load

# Canonical lab DAQ summary schema (one row per steady-state point).
GATE1_RIG_CSV_COLUMNS: tuple[str, ...] = (
    "timestamp_utc",
    "tier_index",
    "speed_rpm",
    "load_fraction",
    "stroke_mm",
    "peak_power_kw",
    "electric_efficiency",
    "imep_bar",
    "peak_pressure_bar",
    "bearing_runout_mm",
    "core_temp_c",
    "exhaust_temp_c",
    "generator_efficiency",
    "fuel_rate_g_s",
    "notes",
)

# Metrics compared in the residual report.
RESIDUAL_METRICS: tuple[str, ...] = (
    "stroke_mm",
    "peak_power_kw",
    "electric_efficiency",
    "imep_bar",
    "peak_pressure_bar",
    "bearing_runout_mm",
    "core_temp_c",
    "exhaust_temp_c",
)

# Default acceptance bands for twin-vs-lab (engineering, not homologation).
DEFAULT_TOLERANCES: dict[str, float] = {
    "stroke_mm": 2.0,
    "peak_power_kw": 8.0,
    "electric_efficiency": 0.03,
    "imep_bar": 1.5,
    "peak_pressure_bar": 10.0,
    "bearing_runout_mm": 0.02,
    "core_temp_c": 80.0,
    "exhaust_temp_c": 60.0,
}


@dataclass(frozen=True)
class Gate1RigRow:
    """One steady-state lab (or synthetic) operating point."""

    timestamp_utc: str
    tier_index: int
    speed_rpm: float
    load_fraction: float
    stroke_mm: float
    peak_power_kw: float
    electric_efficiency: float
    imep_bar: float
    peak_pressure_bar: float
    bearing_runout_mm: float
    core_temp_c: float
    exhaust_temp_c: float
    generator_efficiency: float
    fuel_rate_g_s: float
    notes: str = ""

    def as_dict(self) -> dict[str, str | float | int]:
        return {c: getattr(self, c) for c in GATE1_RIG_CSV_COLUMNS}


@dataclass(frozen=True)
class MetricResidual:
    metric: str
    measured: float
    twin: float
    residual: float
    tolerance: float

    @property
    def within_tolerance(self) -> bool:
        return abs(self.residual) <= self.tolerance


@dataclass(frozen=True)
class PointResidual:
    """Residuals for one rig row vs twin at the same setpoint."""

    row: Gate1RigRow
    metrics: tuple[MetricResidual, ...]
    twin_backend: str

    @property
    def passed(self) -> bool:
        return all(m.within_tolerance for m in self.metrics)


@dataclass(frozen=True)
class ResidualReport:
    """Rollup of twin-vs-measured residuals for a CSV batch."""

    points: tuple[PointResidual, ...]
    source_path: str
    twin_path_label: str

    @property
    def points_passed(self) -> int:
        return sum(1 for p in self.points if p.passed)

    @property
    def pass_rate_pct(self) -> float:
        if not self.points:
            return 0.0
        return 100.0 * self.points_passed / len(self.points)

    def summary_text(self) -> str:
        lines = [
            "=== Gate 1 twin residual report ===",
            f"  Source     : {self.source_path}",
            f"  Twin path  : {self.twin_path_label}",
            f"  Points     : {self.points_passed}/{len(self.points)} within tolerance "
            f"({self.pass_rate_pct:.0f}%)",
            "",
        ]
        for p in self.points:
            mark = "PASS" if p.passed else "FAIL"
            lines.append(
                f"  [{mark}] tier={p.row.tier_index} "
                f"{p.row.speed_rpm:.0f} rpm @ {p.row.load_fraction:.0%} "
                f"({p.twin_backend})"
            )
            for m in p.metrics:
                if m.within_tolerance:
                    continue
                lines.append(
                    f"         {m.metric}: meas={m.measured:.4g} twin={m.twin:.4g} "
                    f"res={m.residual:+.4g} tol=±{m.tolerance:g}"
                )
        lines += [
            "",
            "  Residual = measured - twin. Update surrogates only after reviewing FAIL rows.",
            "  DISCLAIMER: synthetic CSV is for pipeline prove-out, not lab evidence.",
        ]
        return "\n".join(lines)


def _parse_row(raw: dict[str, str]) -> Gate1RigRow:
    missing = [c for c in GATE1_RIG_CSV_COLUMNS if c not in raw or raw[c] == ""]
    # notes may be empty
    missing = [c for c in missing if c != "notes"]
    if missing:
        raise ValueError(f"rig CSV row missing columns: {missing}")
    return Gate1RigRow(
        timestamp_utc=str(raw["timestamp_utc"]),
        tier_index=int(float(raw["tier_index"])),
        speed_rpm=float(raw["speed_rpm"]),
        load_fraction=float(raw["load_fraction"]),
        stroke_mm=float(raw["stroke_mm"]),
        peak_power_kw=float(raw["peak_power_kw"]),
        electric_efficiency=float(raw["electric_efficiency"]),
        imep_bar=float(raw["imep_bar"]),
        peak_pressure_bar=float(raw["peak_pressure_bar"]),
        bearing_runout_mm=float(raw["bearing_runout_mm"]),
        core_temp_c=float(raw["core_temp_c"]),
        exhaust_temp_c=float(raw["exhaust_temp_c"]),
        generator_efficiency=float(raw["generator_efficiency"]),
        fuel_rate_g_s=float(raw["fuel_rate_g_s"]),
        notes=str(raw.get("notes", "")),
    )


def read_gate1_rig_csv(path: Path | str) -> tuple[Gate1RigRow, ...]:
    """Load measured (or synthetic) Gate 1 summary rows."""
    out = Path(path)
    with out.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise ValueError(f"empty CSV: {out}")
        cols = list(reader.fieldnames)
        for required in GATE1_RIG_CSV_COLUMNS:
            if required not in cols:
                raise ValueError(
                    f"CSV {out} missing required column {required!r}; "
                    f"expected schema {GATE1_RIG_CSV_COLUMNS}"
                )
        return tuple(_parse_row(row) for row in reader)


def write_gate1_rig_csv(path: Path | str, rows: tuple[Gate1RigRow, ...] | list[Gate1RigRow]) -> Path:
    """Write rows in the canonical Gate 1 lab schema."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GATE1_RIG_CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.as_dict())
    return out


def synthesize_gate1_rig_csv(
    path: Path | str,
    *,
    points: tuple[tuple[int, float, float], ...] | None = None,
    noise_frac: float = 0.02,
    seed: int = 0,
    prefer_cantera: bool = False,
) -> Path:
    """Create a demo lab CSV from the twin + small noise (pipeline prove-out)."""
    rng = random.Random(seed)
    if points is None:
        points = (
            (1, 2600.0, 0.75),
            (1, 2200.0, 0.50),
            (0, 2600.0, 0.75),
            (2, 2600.0, 0.75),
        )
    rows: list[Gate1RigRow] = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for tier, speed, load in points:
        bench = gate1_bench_at_load(
            speed_rpm=speed,
            load_fraction=load,
            tier_index=tier,
            prefer_cantera=prefer_cantera,
        )
        m = bench.measurement
        disp_cc = (100.0, 300.0, 750.0)[max(0, min(2, tier))]

        def _n(x: float) -> float:
            return x * (1.0 + rng.uniform(-noise_frac, noise_frac))

        # Fuel rate surrogate: electrical kW / (η * LHV[MJ/kg]) → g/s.
        lhv_mj_per_kg = 43.4
        eta = max(m.electric_efficiency, 1e-3)
        fuel_rate_g_s = (m.peak_power_kw / eta) / lhv_mj_per_kg

        rows.append(
            Gate1RigRow(
                timestamp_utc=ts,
                tier_index=tier,
                speed_rpm=speed,
                load_fraction=load,
                stroke_mm=_n(m.stroke_mm),
                peak_power_kw=_n(m.peak_power_kw),
                electric_efficiency=_n(m.electric_efficiency),
                imep_bar=_n(m.imep_bar),
                peak_pressure_bar=_n(m.peak_pressure_bar),
                bearing_runout_mm=max(0.0, _n(m.bearing_runout_mm)),
                core_temp_c=_n(m.core_temp_c),
                exhaust_temp_c=_n(m.exhaust_temp_c),
                generator_efficiency=0.96,
                fuel_rate_g_s=max(0.0, _n(fuel_rate_g_s)),
                notes=f"synthetic twin+noise tier={tier} {disp_cc:.0f}cc",
            )
        )
    return write_gate1_rig_csv(path, rows)


def compare_row_to_twin(
    row: Gate1RigRow,
    *,
    prefer_cantera: bool = False,
    tolerances: dict[str, float] | None = None,
) -> PointResidual:
    """Evaluate twin at the row's setpoint and compute per-metric residuals."""
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    bench = gate1_bench_at_load(
        speed_rpm=row.speed_rpm,
        load_fraction=row.load_fraction,
        tier_index=row.tier_index,
        prefer_cantera=prefer_cantera,
        generator_efficiency=row.generator_efficiency or 0.96,
    )
    m = bench.measurement
    twin_vals = {
        "stroke_mm": m.stroke_mm,
        "peak_power_kw": m.peak_power_kw,
        "electric_efficiency": m.electric_efficiency,
        "imep_bar": m.imep_bar,
        "peak_pressure_bar": m.peak_pressure_bar,
        "bearing_runout_mm": m.bearing_runout_mm,
        "core_temp_c": m.core_temp_c,
        "exhaust_temp_c": m.exhaust_temp_c,
    }
    metrics = tuple(
        MetricResidual(
            metric=name,
            measured=float(getattr(row, name)),
            twin=twin_vals[name],
            residual=float(getattr(row, name)) - twin_vals[name],
            tolerance=tol[name],
        )
        for name in RESIDUAL_METRICS
    )
    backend = "gate1_surrogate" if not prefer_cantera else "gate1_prefer_cantera"
    return PointResidual(row=row, metrics=metrics, twin_backend=backend)


def run_gate1_residual_report(
    path: Path | str,
    *,
    prefer_cantera: bool = False,
    tolerances: dict[str, float] | None = None,
) -> ResidualReport:
    """Load a Gate 1 rig CSV and compare every row to the live twin."""
    rows = read_gate1_rig_csv(path)
    points = tuple(
        compare_row_to_twin(r, prefer_cantera=prefer_cantera, tolerances=tolerances)
        for r in rows
    )
    label = "prefer_cantera" if prefer_cantera else "surrogate"
    return ResidualReport(
        points=points,
        source_path=str(path),
        twin_path_label=label,
    )
