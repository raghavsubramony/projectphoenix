#!/usr/bin/env python3
"""A/B: Gate-5 dynamic ring with brain-PCMRITMS coordination ON vs OFF.

Physical ``InertialBuffer`` remains in the plant for both arms. Only the ATPE
Brain's ``PcmritmsCoordinator`` is toggled. Instruments buffer energy, events,
and SoC statistics for Gate-6 calibration.

Usage::

    py -3 scripts/run_pcmritms_brain_study.py --quick
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import DriveCycles, run
from digital_twin.config import phase1_config, with_dynamic_ring
from digital_twin.gate4_scaling import charge_sustaining_fuel_config
from digital_twin.powertrain import Powertrain
from digital_twin.simulation import Result


@dataclass
class BufferCycleMetrics:
    fuel_l_per_100km: float
    mean_efficiency: float
    shortfalls: int
    mean_active_carts: float
    buffer_out_wh: float
    buffer_in_wh: float
    net_buffer_wh: float
    mean_soc: float
    soc_stddev: float
    time_below_target_s: float
    time_above_target_s: float
    assist_events: int
    precharge_events: int
    surge_events: int


def _make_twin(*, pcmritms_brain_enabled: bool, probe_cycles: int) -> Powertrain:
    cfg = charge_sustaining_fuel_config(
        with_dynamic_ring(
            phase1_config(rotor_coupled=True),
            probe_cycles=probe_cycles,
            fast_probe=True,
            pcmritms_brain_enabled=pcmritms_brain_enabled,
            closed_loop_surge=pcmritms_brain_enabled,
        )
    )
    return Powertrain(cfg)


def _metrics(
    twin: Powertrain,
    result: Result,
    *,
    soc_target: float,
    dt_s: float,
) -> BufferCycleMetrics:
    out_j = 0.0
    in_j = 0.0
    below = 0.0
    above = 0.0
    socs: list[float] = []
    carts: list[int] = []
    for step in result.records:
        # buffer_w: + discharge to bus, - charge from bus
        if step.buffer_w > 0.0:
            out_j += step.buffer_w * dt_s
        elif step.buffer_w < 0.0:
            in_j += (-step.buffer_w) * dt_s
        socs.append(step.buffer_soc)
        if step.buffer_soc < soc_target:
            below += dt_s
        elif step.buffer_soc > soc_target:
            above += dt_s
        if step.active_cartridges:
            carts.append(step.active_cartridges)

    mean_soc = sum(socs) / len(socs) if socs else 0.0
    var = (
        sum((s - mean_soc) ** 2 for s in socs) / len(socs) if socs else 0.0
    )
    mean_active = sum(carts) / len(carts) if carts else 0.0
    assist = pre = surge = 0
    if hasattr(twin, "ring_atpe"):
        assist = twin.ring_atpe.assist_event_count
        pre = twin.ring_atpe.precharge_event_count
        surge = twin.ring_atpe.surge_event_count

    out_wh = out_j / 3600.0
    in_wh = in_j / 3600.0
    return BufferCycleMetrics(
        fuel_l_per_100km=result.equiv_fuel_l_per_100km,
        mean_efficiency=result.mean_efficiency,
        shortfalls=result.shortfall_events,
        mean_active_carts=mean_active,
        buffer_out_wh=out_wh,
        buffer_in_wh=in_wh,
        net_buffer_wh=out_wh - in_wh,
        mean_soc=mean_soc,
        soc_stddev=math.sqrt(var),
        time_below_target_s=below,
        time_above_target_s=above,
        assist_events=assist,
        precharge_events=pre,
        surge_events=surge,
    )


def _format_metrics(name: str, m: BufferCycleMetrics) -> list[str]:
    return [
        (
            f"  {name:<10}  fuel {m.fuel_l_per_100km:5.2f} L/100km  "
            f"eta {m.mean_efficiency * 100:4.1f}%  "
            f"shortfalls {m.shortfalls}  "
            f"mean active carts {m.mean_active_carts:4.1f}"
        ),
        (
            f"    buffer Wh  out {m.buffer_out_wh:7.1f}  in {m.buffer_in_wh:7.1f}  "
            f"net {m.net_buffer_wh:+7.1f}"
        ),
        (
            f"    SoC mean {m.mean_soc:5.3f}  std {m.soc_stddev:5.3f}  "
            f"t_below {m.time_below_target_s:5.0f}s  t_above {m.time_above_target_s:5.0f}s"
        ),
        (
            f"    events  assist {m.assist_events}  "
            f"precharge {m.precharge_events}  surge {m.surge_events}"
        ),
    ]


def _run_arm(
    *,
    label: str,
    pcmritms_brain_enabled: bool,
    duration_s: float,
    probe_cycles: int,
    soc_target: float,
    dt_s: float,
) -> tuple[list[str], dict[str, BufferCycleMetrics]]:
    lines: list[str] = [f"--- {label} ---"]
    print(f"--- {label} ---")
    metrics: dict[str, BufferCycleMetrics] = {}
    for name, cycle in (
        ("mixed", DriveCycles.mixed(duration_s=duration_s)),
        ("highway", DriveCycles.highway(duration_s=duration_s)),
    ):
        twin = _make_twin(
            pcmritms_brain_enabled=pcmritms_brain_enabled,
            probe_cycles=probe_cycles,
        )
        result = run(twin, cycle)
        m = _metrics(twin, result, soc_target=soc_target, dt_s=cycle.dt_s)
        metrics[name] = m
        for line in _format_metrics(name, m):
            print(line)
            lines.append(line.strip() if not line.startswith("  ") else line.rstrip())
    lines.append("")
    print()
    return lines, metrics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "PCMRITMS-BRAIN-AB-STUDY.txt",
    )
    args = parser.parse_args()

    duration = 300.0 if args.quick else 1200.0
    probe = 3 if args.quick else 4
    # DriveCycles default dt
    dt_s = 1.0
    # Plant refill target (ControlConfig); brain precharge floor is separate (0.40).
    plant_soc_target = 0.7

    print()
    print("=" * 90)
    print("PCMRITMS BRAIN A/B — benefit-seeking coordinator ON vs OFF")
    print("=" * 90)
    print(f"Duration per cycle: {duration:.0f} s | rotor_coupled peak_transient=on")
    print()

    lines: list[str] = [
        "PCMRITMS BRAIN A/B STUDY",
        "Policy: benefit-seeking (assist only on residual spike; precharge only",
        "        when SoC < 0.40 toward 0.55; otherwise pass-through)",
        "Physical InertialBuffer: ALWAYS ON",
        "Toggle: ATPE Brain PcmritmsCoordinator",
        f"Duration per cycle: {duration:.0f} s",
        "Config: Phase-1 AWD SUV, Gate-5 dynamic ring, rotor_coupled=True,",
        "        charge-sustaining SoC (0.55)",
        "",
        "NOTE: Gate-5 dynamic-vs-tier-lump 5.45→5.17 is NOT this study.",
        "",
    ]

    off_lines, off_m = _run_arm(
        label="Brain-PCMRITMS OFF (schedule raw setpoint)",
        pcmritms_brain_enabled=False,
        duration_s=duration,
        probe_cycles=probe,
        soc_target=plant_soc_target,
        dt_s=dt_s,
    )
    on_lines, on_m = _run_arm(
        label="Brain-PCMRITMS ON (benefit-seeking + closed-loop)",
        pcmritms_brain_enabled=True,
        duration_s=duration,
        probe_cycles=probe,
        soc_target=plant_soc_target,
        dt_s=dt_s,
    )
    lines.extend(off_lines)
    lines.extend(on_lines)

    lines.append("--- Delta (OFF - ON); positive = brain reduced fuel ---")
    print("--- Delta (OFF - ON); positive = brain reduced fuel ---")
    for name in ("mixed", "highway"):
        delta = off_m[name].fuel_l_per_100km - on_m[name].fuel_l_per_100km
        pct = (
            100.0 * delta / off_m[name].fuel_l_per_100km
            if off_m[name].fuel_l_per_100km > 0
            else 0.0
        )
        line = f"  {name:<10}  {delta:+.3f} L/100km  ({pct:+.2f}%)"
        print(line)
        lines.append(line.strip())
        net_delta = on_m[name].net_buffer_wh - off_m[name].net_buffer_wh
        lines.append(
            f"    net buffer Wh delta (ON-OFF) {net_delta:+.1f}  "
            f"assist {on_m[name].assist_events}  "
            f"precharge {on_m[name].precharge_events}  "
            f"surge {on_m[name].surge_events}"
        )
        print(lines[-1])
    lines.append("")
    lines.append("INTERPRETATION")
    lines.append(
        "  Instrument net_buffer_wh and event counts before attributing fuel deltas."
    )
    lines.append(
        "  Benefit-seeking should keep highway near pass-through (few events)."
    )
    lines.append("")

    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
