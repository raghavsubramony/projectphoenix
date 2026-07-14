#!/usr/bin/env python3
"""Gate-6 long-duration endurance validation (accelerated multi-cycle).

Repeats mixed + highway legs, tracking thermal soak, buffer SoC, R/A, health.

Usage::

    py -3 scripts/run_gate6_endurance_study.py --quick
    py -3 scripts/run_gate6_endurance_study.py --hours 2
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import DriveCycles, run
from digital_twin.config import phase1_config, with_dynamic_ring
from digital_twin.gate4_scaling import charge_sustaining_fuel_config
from digital_twin.powertrain import Powertrain


def _make_twin(*, probe_cycles: int) -> Powertrain:
    base = with_dynamic_ring(
        phase1_config(rotor_coupled=True),
        probe_cycles=probe_cycles,
        fast_probe=True,
        pcmritms_brain_enabled=True,
        closed_loop_surge=True,
    )
    return Powertrain(charge_sustaining_fuel_config(base))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="one mixed+highway pass")
    ap.add_argument("--hours", type=float, default=1.0, help="target sim hours")
    ap.add_argument("--probe-cycles", type=int, default=8)
    args = ap.parse_args()

    twin = _make_twin(probe_cycles=args.probe_cycles)
    if hasattr(twin, "ring_atpe") and twin.ring_atpe is not None:
        twin.ring_atpe.supervisor.config = replace(
            twin.ring_atpe.supervisor.config,
            twin_fidelity="physics",
            enable_rotor_phasing=True,
            enable_multi_horizon=True,
            fleet_mission_mode="endurance",
            enable_fleet_learning=True,
        )

    mixed = DriveCycles.mixed(duration_s=600.0 if args.quick else 1200.0)
    hwy = DriveCycles.highway(duration_s=400.0 if args.quick else 900.0)
    passes = 1 if args.quick else max(1, int(args.hours * 3600 / (mixed.duration_s + hwy.duration_s)))

    soc_trace: list[float] = []
    health_trace: list[float] = []
    thermal_trace: list[float] = []
    ra_trace: list[float] = []
    fuel_l = 0.0
    dist_m = 0.0

    print(f"GATE6-ENDURANCE passes={passes} probe={args.probe_cycles}")
    for p in range(passes):
        for name, cycle in (("mixed", mixed), ("hwy", hwy)):
            result = run(twin, cycle)
            fuel_l += result.fuel_l
            dist_m += result.distance_km * 1000.0
            buf = twin.buffer
            if hasattr(buf, "state_of_charge"):
                soc = float(buf.state_of_charge)
            elif hasattr(buf, "energy_j"):
                soc = buf.energy_j / max(1.0, twin.cfg.buffer.max_energy_j)
            else:
                soc = 0.0
            soc_trace.append(soc)
            if hasattr(twin, "ring_atpe") and twin.ring_atpe is not None:
                brain = twin.ring_atpe.supervisor
                states = brain.ring.states
                health_trace.append(
                    sum(s.health_pct for s in states) / max(1, len(states))
                )
                thermal_trace.append(brain.ring.coolant.temp_k)
                ra_trace.append(brain.pcmritms.recharge_to_assist_ratio)
            print(
                f"  pass {p + 1}/{passes} {name}: "
                f"fuel={result.fuel_l:.3f}L soc={soc_trace[-1]:.3f} "
                f"health={health_trace[-1] if health_trace else 0:.1f} "
                f"coolantK={thermal_trace[-1] if thermal_trace else 0:.1f} "
                f"R/A={ra_trace[-1] if ra_trace else 0:.3f}"
            )

    dist_km = dist_m / 1000.0
    l100 = 100.0 * fuel_l / max(dist_km, 1e-6)
    print("---")
    print(f"fuel_l_per_100km={l100:.3f}")
    print(f"soc_start={soc_trace[0]:.3f} soc_end={soc_trace[-1]:.3f} "
          f"soc_min={min(soc_trace):.3f} soc_max={max(soc_trace):.3f}")
    if health_trace:
        print(f"health_start={health_trace[0]:.2f} health_end={health_trace[-1]:.2f} "
              f"health_min={min(health_trace):.2f}")
    if thermal_trace:
        print(f"coolant_peak_k={max(thermal_trace):.1f}")
    if ra_trace:
        print(f"ra_mean={sum(ra_trace) / len(ra_trace):.3f} ra_max={max(ra_trace):.3f}")
    print("STATUS=ENDURANCE_HARNESS_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
