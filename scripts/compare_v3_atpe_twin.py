#!/usr/bin/env python3
"""Compare default ATPE tables vs per-tier Phoenix V3 calibration on the vehicle twin.

Runs cartridge physics once per tier/load sample, then drive cycles using interpolated
maps (fast). Prints tier calibration tables, fuel economy, and ERS acceptance.

Usage (repo root)::

    .venv-design\\Scripts\\python.exe scripts/compare_v3_atpe_twin.py
    .venv-design\\Scripts\\python.exe scripts/compare_v3_atpe_twin.py --quick
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import (
    DriveCycles,
    Powertrain,
    build_default_twin,
    build_phoenix_v3_twin,
    phase1_config,
    run,
)
from digital_twin.acceptance import evaluate, phase1_targets_for
from digital_twin.config import with_phoenix_v3
from digital_twin.phoenix_v3_bridge import (
    calibrate_all_v3_tiers,
    resolve_best_tuning_path,
)


def _print_calibration_table() -> None:
    print("=== Phoenix V3 per-tier cartridge calibration ===")
    print("Tuning files: micro / medium (v3) / large\n")
    curves = calibrate_all_v3_tiers(cycles=12)
    for curve in curves:
        deriv = getattr(curve, "derivation", "phoenix_v3_sim")
        print(f"  {curve.tier_name} ({curve.displacement_cc:.0f} cc/cyl) [{deriv}]")
        print(f"    {'Load':>6}  {'eta':>7}  {'kW/cart':>8}  {'P peak':>8}")
        for pt in curve.points:
            print(
                f"    {pt.load_frac*100:5.0f}%  {pt.net_efficiency*100:6.1f}%  "
                f"{pt.elec_power_w/1000:7.2f}  {pt.peak_pressure_bar:7.0f} bar"
            )
        print(f"    Sweet spot (75% load): eta = {curve.efficiency_at(0.75)*100:.1f}%\n")


def _run_cycles(twin: Powertrain, quick: bool) -> list:
    if quick:
        return [
            run(twin, DriveCycles.mixed(duration_s=600)),
            run(twin, DriveCycles.highway(duration_s=600)),
        ]
    return [
        run(twin, DriveCycles.mixed()),
        run(twin, DriveCycles.highway()),
        run(twin, DriveCycles.urban()),
    ]


def _summarize(label: str, results: list) -> None:
    print(f"--- {label} ---")
    for res in results:
        print(
            f"  {res.cycle_name:<10}  fuel {res.equiv_fuel_l_per_100km:5.2f} L/100km  "
            f"CO2 {res.co2_g_per_km:5.0f} g/km  "
            f"gen_eff {res.mean_efficiency*100:4.1f}%  "
            f"shortfalls {res.shortfall_events}"
        )


def _ers_summary(build_twin, label: str) -> tuple[int, int]:
    targets = phase1_targets_for("AWD SUV")
    checks = evaluate(build_twin, targets)
    passed = sum(1 for c in checks if c.passed)
    print(f"\n=== ERS acceptance ({label}) — {passed}/{len(checks)} PASS ===")
    for chk in checks:
        print(chk.line())
    return passed, len(checks)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true", help="Shorter cycles for CI")
    parser.add_argument("--skip-calibration-print", action="store_true")
    args = parser.parse_args()

    tuning = resolve_best_tuning_path()
    if tuning is None:
        print("ERROR: no designs/phoenix_v3_best_tuning*.json", file=sys.stderr)
        return 1

    if not args.skip_calibration_print:
        _print_calibration_table()

    base_cfg = phase1_config()
    print("=== ATPE tier efficiencies (table vs V3-calibrated) ===")
    v3_cfg = with_phoenix_v3(base_cfg, tuning_path=str(tuning), v3_cycles=12)
    for i, (base_t, v3_t) in enumerate(zip(base_cfg.atpe.tiers, v3_cfg.atpe.tiers)):
        print(
            f"  Tier {i+1}: table {base_t.thermal_efficiency*100:.1f}%  ->  "
            f"V3 {v3_t.thermal_efficiency*100:.1f}%  "
            f"({v3_t.displacement_cc:.0f} cc, {v3_t.max_electric_w/1000:.0f} kW cap)"
        )
    print()

    default_twin = build_default_twin()
    v3_twin = build_phoenix_v3_twin(tuning_path=str(tuning), v3_cycles=12)

    default_results = _run_cycles(default_twin, args.quick)
    v3_results = _run_cycles(v3_twin, args.quick)

    print("=== Drive-cycle comparison (AWD SUV) ===")
    _summarize("Default ATPE (fixed efficiency tables)", default_results)
    print()
    _summarize("V3-calibrated ATPE (per-tier cartridge physics)", v3_results)

    mixed_def = next(r for r in default_results if "mixed" in r.cycle_name.lower())
    mixed_v3 = next(r for r in v3_results if "mixed" in r.cycle_name.lower())
    delta = mixed_v3.equiv_fuel_l_per_100km - mixed_def.equiv_fuel_l_per_100km
    pct = 100.0 * delta / mixed_def.equiv_fuel_l_per_100km if mixed_def.equiv_fuel_l_per_100km else 0.0
    print(
        f"\nMixed-cycle delta (V3 - default): {delta:+.2f} L/100km ({pct:+.1f}%)"
    )

    def _build_default():
        return Powertrain(phase1_config())

    def _build_v3():
        return build_phoenix_v3_twin(tuning_path=str(tuning), v3_cycles=12)

    p0, n0 = _ers_summary(_build_default, "default tables")
    p1, n1 = _ers_summary(_build_v3, "V3-calibrated")

    print("\n=== Verdict ===")
    if p1 == n1 and mixed_v3.shortfall_events == 0:
        print(
            "V3-derived per-tier efficiencies support the full ATPE vehicle twin: "
            "ERS targets pass and no capability shortfalls on tested cycles."
        )
    elif p1 >= p0:
        print(
            "V3 calibration is viable but shifts fuel/performance vs table defaults; "
            "review tier efficiency and power maps before hardware sign-off."
        )
    else:
        print(
            "V3 calibration reduces ERS margin vs table defaults — "
            "tier scaling or cartridge tuning may need refinement."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
