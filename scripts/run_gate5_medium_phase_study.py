#!/usr/bin/env python3
"""Gate-5 medium-phase study — uniform medium capture on frozen Gate-4 stack.

Builds on approved production freeze (Option 2+1):
  4/6/2 tier_blocks, 32.9 Hz large, medium-heavy load, NVH phase layout

Gate-5 adds medium-only phase refinement to reduce within-tier capture dispersion.

Targets:
  Peak force <1200 N
  Medium RBI <3%
  eta >54.5%
  P >310 kW

Usage::

    py -3 scripts/run_gate5_medium_phase_study.py
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_mixed_ring import (
    GATE4_LAYOUT,
    GATE4_PRODUCTION_LOAD_POLICY,
    GATE5_TARGET_MEDIUM_RBI_PCT,
    GATE5_TARGET_MIN_POWER_KW,
    GATE5_TARGET_PEAK_FORCE_N,
    GATE5_TARGET_RING_EFFICIENCY,
    MixedRingLayout,
    RingSchedule,
    build_gate4_production_options,
    optimize_gate5_medium_phases,
    print_mixed_ring_report,
    simulate_mixed_ring,
)


def _medium_cohort_rbi(result) -> float:
    for cohort in result.rbi.per_tier:
        if cohort.tier_index == 1:
            return cohort.total_pct
    return float("nan")


def _medium_captures(result) -> list[float]:
    return [
        c.mean_capture_fraction * 100.0
        for c in result.cartridges
        if c.slot.tier_index == 1
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument("--capture-steps", type=int, default=30)
    parser.add_argument("--capture-passes", type=int, default=2)
    parser.add_argument("--phase-steps", type=int, default=40)
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "GATE5-MEDIUM-PHASE-STUDY.txt",
    )
    args = parser.parse_args()

    layout = MixedRingLayout(*GATE4_LAYOUT)

    print()
    print("=" * 105)
    print("GATE-5 MEDIUM-PHASE STUDY — frozen Gate-4 stack, medium capture uniformity")
    print("=" * 105)
    print(
        f"  Production load: micro x{GATE4_PRODUCTION_LOAD_POLICY.micro_multiplier:.2f}  "
        f"medium x{GATE4_PRODUCTION_LOAD_POLICY.medium_multiplier:.2f}  "
        f"large x{GATE4_PRODUCTION_LOAD_POLICY.large_multiplier:.2f}"
    )
    print(
        f"  Targets: P >{GATE5_TARGET_MIN_POWER_KW:.0f} kW  "
        f"eta >={GATE5_TARGET_RING_EFFICIENCY:.1%}  "
        f"medium RBI <{GATE5_TARGET_MEDIUM_RBI_PCT:.0f}%  "
        f"peak <{GATE5_TARGET_PEAK_FORCE_N:.0f} N"
    )
    print()

    production = build_gate4_production_options(
        layout, phase_steps=args.phase_steps,
    )
    gate5 = optimize_gate5_medium_phases(
        layout,
        cycles=args.cycles,
        capture_steps=args.capture_steps,
        capture_passes=args.capture_passes,
        phase_steps=args.phase_steps,
    )

    scenarios = [
        ("Gate-4 production freeze (Option 2+1)", production),
        ("Gate-5 medium-phase refinement", gate5),
    ]

    print(
        f"  {'Scenario':<40}  {'kW':>6}  {'eta':>6}  {'peak N':>7}  "
        f"{'mRBI':>6}  {'mCap CV':>7}  captures"
    )
    print("-" * 105)

    lines: list[str] = [
        "GATE-5 MEDIUM-PHASE STUDY",
        f"Layout: {layout.label}",
        "",
    ]
    best_name = ""
    best = None

    for name, options in scenarios:
        result = simulate_mixed_ring(
            layout,
            schedule=RingSchedule.TIER_BLOCKS,
            cycles=args.cycles,
            build_options=options,
        )
        medium = next(c for c in result.rbi.per_tier if c.tier_index == 1)
        caps = _medium_captures(result)
        cap_str = ", ".join(f"{c:.1f}" for c in caps)
        line = (
            f"  {name:<40}  {result.total_elec_power_w/1000:6.1f}  "
            f"{result.ring_efficiency:5.1%}  {result.nvh.peak_resultant_n:7.0f}  "
            f"{medium.total_pct:6.2f}  {medium.capture_cv_pct:6.2f}  {cap_str}"
        )
        print(line)
        lines.append(line.strip())
        if best is None or medium.total_pct < _medium_cohort_rbi(best):
            best = result
            best_name = name

    print("=" * 105)
    print()

    if best is not None:
        prod_result = simulate_mixed_ring(
            layout, cycles=args.cycles, build_options=production,
        )
        gate5_result = simulate_mixed_ring(
            layout, cycles=args.cycles, build_options=gate5,
        )
        prod_medium = next(c for c in prod_result.rbi.per_tier if c.tier_index == 1)
        gate5_medium = next(c for c in gate5_result.rbi.per_tier if c.tier_index == 1)

        checks = [
            (
                "Power",
                gate5_result.total_elec_power_w / 1000 >= GATE5_TARGET_MIN_POWER_KW,
                f"{gate5_result.total_elec_power_w/1000:.1f} kW",
            ),
            (
                "Efficiency",
                gate5_result.ring_efficiency >= GATE5_TARGET_RING_EFFICIENCY,
                f"{gate5_result.ring_efficiency:.1%}",
            ),
            (
                "Medium RBI",
                gate5_medium.total_pct < GATE5_TARGET_MEDIUM_RBI_PCT,
                f"{gate5_medium.total_pct:.2f}% (was {prod_medium.total_pct:.2f}%)",
            ),
            (
                "Peak force",
                gate5_result.nvh.peak_resultant_n < GATE5_TARGET_PEAK_FORCE_N,
                f"{gate5_result.nvh.peak_resultant_n:.0f} N",
            ),
        ]
        print("Gate-5 target check:")
        for label, ok, value in checks:
            print(f"  [{('PASS' if ok else 'OPEN'):4}] {label}: {value}")
        print()
        print(f"Detailed report: {best_name}")
        print_mixed_ring_report(best)

        lines.extend(["", "Gate-5 target check:"])
        for label, ok, value in checks:
            lines.append(f"  [{('PASS' if ok else 'OPEN')}] {label}: {value}")

    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
