#!/usr/bin/env python3
"""Stress A/B for brain-PCMRITMS with energy-normalized + performance metrics.

Raw fuel alone is misleading when end-of-cycle buffer SoC differs. This harness
reports buffer-equalized fuel and launch tracking metrics.

Usage::

    py -3 scripts/run_pcmritms_stress_study.py --quick
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass, replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.cartridge_scheduler import inject_cartridge_fault
from digital_twin import DriveCycles, run
from digital_twin.config import (
    GASOLINE_DENSITY_KG_PER_L,
    GASOLINE_LHV_MJ_PER_KG,
    phase1_config,
    with_dynamic_ring,
)
from digital_twin.gate4_scaling import charge_sustaining_fuel_config
from digital_twin.powertrain import Powertrain
from digital_twin.simulation import Result

_LHV_J_PER_L = GASOLINE_LHV_MJ_PER_KG * 1e6 * GASOLINE_DENSITY_KG_PER_L


@dataclass
class StressMetrics:
    fuel_l_per_100km: float
    fuel_eq_buf_l_per_100km: float
    mean_efficiency: float
    shortfalls: int
    mean_active_carts: float
    buffer_out_wh: float
    buffer_in_wh: float
    net_buffer_wh: float
    soc_start: float
    soc_end: float
    mean_soc: float
    assist_events: int
    precharge_events: int
    surge_events: int
    assist_energy_wh: float
    precharge_energy_wh: float
    recharge_to_assist: float
    peak_shortfall_w: float
    mean_abs_track_err_w: float
    mean_cover_frac: float
    peak_uncovered_w: float


def _make_twin(
    *,
    pcmritms_brain_enabled: bool,
    probe_cycles: int,
    buffer_initial_fraction: float = 1.0,
    isolate_slot: int | None = None,
) -> Powertrain:
    base = with_dynamic_ring(
        phase1_config(rotor_coupled=True),
        probe_cycles=probe_cycles,
        fast_probe=True,
        pcmritms_brain_enabled=pcmritms_brain_enabled,
        closed_loop_surge=pcmritms_brain_enabled,
    )
    cfg = charge_sustaining_fuel_config(base)
    cfg = replace(
        cfg,
        buffer=replace(cfg.buffer, initial_fraction=buffer_initial_fraction),
    )
    twin = Powertrain(cfg)
    if isolate_slot is not None and hasattr(twin, "ring_atpe"):
        twin.ring_atpe._ring.states = inject_cartridge_fault(
            twin.ring_atpe._ring.states, isolate_slot, "cartridge_isolation",
        )
    return twin


def _buffer_equalized_fuel_l_per_100km(
    twin: Powertrain,
    result: Result,
    *,
    soc_start: float,
    soc_end: float,
) -> float:
    """Fuel after restoring buffer SoC to start (same idea as battery CS correction)."""
    best_eff = max(t.thermal_efficiency for t in twin.cfg.atpe.tiers)
    emax = twin.cfg.buffer.max_energy_j
    drained_j = (soc_start - soc_end) * emax  # +ve when buffer was depleted
    equiv_fuel_l = (drained_j / max(best_eff, 1e-6)) / _LHV_J_PER_L
    distance_km = result.distance_km
    if distance_km <= 0.0:
        return 0.0
    return (result.fuel_l + equiv_fuel_l) / distance_km * 100.0


def _metrics(twin: Powertrain, result: Result, *, dt_s: float) -> StressMetrics:
    out_j = in_j = 0.0
    socs: list[float] = []
    carts: list[int] = []
    track_abs = 0.0
    cover_sum = 0.0
    peak_short = 0.0
    peak_uncovered = 0.0
    n = 0
    for step in result.records:
        n += 1
        if step.buffer_w > 0.0:
            out_j += step.buffer_w * dt_s
        elif step.buffer_w < 0.0:
            in_j += (-step.buffer_w) * dt_s
        socs.append(step.buffer_soc)
        if step.active_cartridges:
            carts.append(step.active_cartridges)
        peak_short = max(peak_short, step.shortfall_w)
        # Engine tracking error vs bus demand (buffer/battery cover the rest).
        track_abs += abs(step.demand_w - step.generation_w)
        bus_cover = (
            step.generation_w
            + max(0.0, step.buffer_w)
            + max(0.0, step.battery_w)
        )
        demand = max(step.demand_w, 1.0)
        cover_sum += min(1.0, bus_cover / demand)
        peak_uncovered = max(peak_uncovered, max(0.0, step.demand_w - bus_cover))

    mean_soc = sum(socs) / len(socs) if socs else 0.0
    soc_start = socs[0] if socs else twin.buffer.state_of_charge
    soc_end = socs[-1] if socs else twin.buffer.state_of_charge
    assist = pre = surge = 0
    assist_j = pre_j = 0.0
    ratio = 0.0
    if hasattr(twin, "ring_atpe"):
        assist = twin.ring_atpe.assist_event_count
        pre = twin.ring_atpe.precharge_event_count
        surge = twin.ring_atpe.surge_event_count
        assist_j = twin.ring_atpe.assist_energy_j
        pre_j = twin.ring_atpe.precharge_energy_j
        ratio = twin.ring_atpe.recharge_to_assist_ratio
    out_wh, in_wh = out_j / 3600.0, in_j / 3600.0
    fuel_eq = _buffer_equalized_fuel_l_per_100km(
        twin, result, soc_start=soc_start, soc_end=soc_end,
    )
    return StressMetrics(
        fuel_l_per_100km=result.equiv_fuel_l_per_100km,
        fuel_eq_buf_l_per_100km=fuel_eq,
        mean_efficiency=result.mean_efficiency,
        shortfalls=result.shortfall_events,
        mean_active_carts=(sum(carts) / len(carts) if carts else 0.0),
        buffer_out_wh=out_wh,
        buffer_in_wh=in_wh,
        net_buffer_wh=out_wh - in_wh,
        soc_start=soc_start,
        soc_end=soc_end,
        mean_soc=mean_soc,
        assist_events=assist,
        precharge_events=pre,
        surge_events=surge,
        assist_energy_wh=assist_j / 3600.0,
        precharge_energy_wh=pre_j / 3600.0,
        recharge_to_assist=ratio,
        peak_shortfall_w=peak_short,
        mean_abs_track_err_w=(track_abs / n if n else 0.0),
        mean_cover_frac=(cover_sum / n if n else 0.0),
        peak_uncovered_w=peak_uncovered,
    )


def _scenarios(duration_s: float):
    return [
        ("transient", DriveCycles.transient_stress(repeats=6 if duration_s <= 300 else 12), 1.0, None),
        ("tow_grade", DriveCycles.towing_grade(duration_s=duration_s), 1.0, None),
        ("low_buf_launch", DriveCycles.transient_stress(repeats=4 if duration_s <= 300 else 8), 0.22, None),
        ("n1_transient", DriveCycles.transient_stress(repeats=4 if duration_s <= 300 else 8), 1.0, 5),
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "PCMRITMS-BRAIN-STRESS-AB.txt",
    )
    args = parser.parse_args()

    duration = 240.0 if args.quick else 600.0
    probe = 3 if args.quick else 4

    print()
    print("=" * 90)
    print("PCMRITMS BRAIN STRESS A/B — energy-normalized + performance metrics")
    print("=" * 90)
    print(f"Duration scale: {duration:.0f} s")
    print()

    lines: list[str] = [
        "PCMRITMS BRAIN STRESS A/B STUDY",
        "Policy: benefit-seeking + hysteresis + post-assist cooldown +",
        "        steady-load precharge suppress",
        "Metrics: raw CS fuel, buffer-equalized fuel (restore SoC to start),",
        "         R/A, carts, cover frac, peak shortfall / uncovered W",
        f"Duration scale: {duration:.0f} s",
        "",
    ]

    summary: list[tuple] = []
    for case_name, cycle, buf_frac, isolate in _scenarios(duration):
        tag = f"buf={buf_frac:.2f}"
        if isolate is not None:
            tag += f" isolate={isolate}"
        print(f"=== {case_name} ({tag}) ===")
        lines.append(f"=== {case_name} ({tag}) ===")
        offs = ons = None
        for label, enabled in (("OFF", False), ("ON", True)):
            twin = _make_twin(
                pcmritms_brain_enabled=enabled,
                probe_cycles=probe,
                buffer_initial_fraction=buf_frac,
                isolate_slot=isolate,
            )
            result = run(twin, cycle)
            m = _metrics(twin, result, dt_s=cycle.dt_s)
            if not enabled:
                offs = m
            else:
                ons = m
            block = (
                f"  {label:<3}  fuel {m.fuel_l_per_100km:5.2f}  "
                f"fuelEqBuf {m.fuel_eq_buf_l_per_100km:5.2f}  "
                f"SoC {m.soc_start:.2f}->{m.soc_end:.2f}  "
                f"carts {m.mean_active_carts:4.1f}  "
                f"a/s/p {m.assist_events}/{m.surge_events}/{m.precharge_events}  "
                f"R/A {m.recharge_to_assist:5.2f}  "
                f"cover {m.mean_cover_frac*100:5.1f}%  "
                f"pkShort {m.peak_shortfall_w/1e3:5.1f}kW"
            )
            print(block)
            lines.append(block)
        assert offs is not None and ons is not None
        d_raw = offs.fuel_l_per_100km - ons.fuel_l_per_100km
        d_eq = offs.fuel_eq_buf_l_per_100km - ons.fuel_eq_buf_l_per_100km
        pct_raw = (
            100.0 * d_raw / offs.fuel_l_per_100km if offs.fuel_l_per_100km > 0 else 0.0
        )
        pct_eq = (
            100.0 * d_eq / offs.fuel_eq_buf_l_per_100km
            if offs.fuel_eq_buf_l_per_100km > 0
            else 0.0
        )
        delta = (
            f"  dfuel raw {d_raw:+.3f} ({pct_raw:+.2f}%)  "
            f"dfuelEqBuf {d_eq:+.3f} ({pct_eq:+.2f}%)  "
            f"dcarts {ons.mean_active_carts - offs.mean_active_carts:+.2f}  "
            f"dcover {(ons.mean_cover_frac - offs.mean_cover_frac)*100:+.2f}pt  "
            f"assistWh {ons.assist_energy_wh:.1f}  prechWh {ons.precharge_energy_wh:.1f}"
        )
        print(delta)
        lines.append(delta)
        lines.append("")
        print()
        summary.append(
            (
                case_name, d_raw, pct_raw, d_eq, pct_eq,
                ons.assist_events, ons.surge_events, ons.precharge_events,
                ons.recharge_to_assist, ons.shortfalls, offs.shortfalls,
                ons.mean_cover_frac, offs.mean_cover_frac,
            )
        )

    lines.append("--- Summary (positive dfuelEqBuf = brain better after buffer restore) ---")
    print("--- Summary ---")
    for row in summary:
        (
            name, d_raw, pct_raw, d_eq, pct_eq, a, s, p, ra, sh_on, sh_off,
            cov_on, cov_off,
        ) = row
        line = (
            f"  {name:<14}  raw {d_raw:+.3f} ({pct_raw:+.2f}%)  "
            f"eqBuf {d_eq:+.3f} ({pct_eq:+.2f}%)  "
            f"a/s/p {a}/{s}/{p}  R/A {ra:.2f}  "
            f"cover {cov_off*100:.1f}->{cov_on*100:.1f}%  "
            f"short {sh_off}/{sh_on}"
        )
        print(line)
        lines.append(line)
    lines.append("")
    lines.append("INTERPRETATION")
    lines.append(
        "  fuelEqBuf restores end buffer SoC to start via equivalent engine fuel."
    )
    lines.append(
        "  Prefer dfuelEqBuf and cover/carts over raw fuel when SoC end differs."
    )
    lines.append("")

    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
