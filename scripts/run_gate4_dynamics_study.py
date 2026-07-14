#!/usr/bin/env python3
"""Gate-4 dynamics study — phase NVH, load sharing, ring layout (32.9 Hz large frozen).

Verdict from V3.1: do NOT force 2:1 harmonic. Optimize dynamics at tuned frequencies.

Experiments (observer options 1–3):
  1. Phase-only NVH search (large Hz fixed at tuned 32.9)
  2. Medium-heavy load policy (micro lighter, medium at ceiling)
  3. micro_medium_ring schedule vs tier_blocks

Usage::

    py -3 scripts/run_gate4_dynamics_study.py
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_mixed_ring import (
    GATE4_FROZEN_LOAD_POLICY,
    GATE4_LAYOUT,
    GATE4_MEDIUM_HEAVY_POLICY,
    MixedRingLayout,
    RingBuildOptions,
    RingSchedule,
    V31_TARGET_PEAK_FORCE_N,
    V31_TARGET_RING_EFFICIENCY,
    build_mixed_ring_slots,
    optimize_nvh_phase_layout,
    print_mixed_ring_report,
    simulate_mixed_ring,
)


def _phase_only_options(
    layout: MixedRingLayout,
    schedule: RingSchedule,
    *,
    load_policy=GATE4_FROZEN_LOAD_POLICY,
    steps: int = 80,
) -> RingBuildOptions:
    base = RingBuildOptions(load_policy=load_policy)
    slots = build_mixed_ring_slots(layout, schedule=schedule, build_options=base)
    offsets = optimize_nvh_phase_layout(
        slots,
        schedule=schedule,
        layout_label=layout.label,
        tiers=(2, 1, 0),
        steps=steps,
    )
    return replace(base, tier_phase_offsets_ms=offsets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "GATE4-DYNAMICS-STUDY.txt",
    )
    args = parser.parse_args()

    layout = MixedRingLayout(*GATE4_LAYOUT)
    frozen_opts = RingBuildOptions(load_policy=GATE4_FROZEN_LOAD_POLICY)

    scenarios: list[tuple[str, RingSchedule, RingBuildOptions]] = [
        (
            "Gate-4 frozen (baseline)",
            RingSchedule.TIER_BLOCKS,
            frozen_opts,
        ),
        (
            "Option 1: phase NVH only",
            RingSchedule.TIER_BLOCKS,
            _phase_only_options(layout, RingSchedule.TIER_BLOCKS, steps=60),
        ),
        (
            "Option 2: medium-heavy load",
            RingSchedule.TIER_BLOCKS,
            RingBuildOptions(load_policy=GATE4_MEDIUM_HEAVY_POLICY),
        ),
        (
            "Option 2+1: medium-heavy + phase",
            RingSchedule.TIER_BLOCKS,
            _phase_only_options(
                layout,
                RingSchedule.TIER_BLOCKS,
                load_policy=GATE4_MEDIUM_HEAVY_POLICY,
                steps=60,
            ),
        ),
        (
            "Option 3: micro_medium_ring",
            RingSchedule.MICRO_MEDIUM_RING,
            frozen_opts,
        ),
        (
            "Option 3+1: micro_medium_ring + phase",
            RingSchedule.MICRO_MEDIUM_RING,
            _phase_only_options(layout, RingSchedule.MICRO_MEDIUM_RING, steps=60),
        ),
    ]

    print()
    print("=" * 105)
    print("GATE-4 DYNAMICS STUDY — 4/6/2, large tier 32.9 Hz (no harmonic lock)")
    print("=" * 105)
    print(
        f"  Frozen load: micro x{GATE4_FROZEN_LOAD_POLICY.micro_multiplier:.2f}  "
        f"medium x{GATE4_FROZEN_LOAD_POLICY.medium_multiplier:.2f}  "
        f"large x{GATE4_FROZEN_LOAD_POLICY.large_multiplier:.2f}"
    )
    print(
        f"  Targets: eta >={V31_TARGET_RING_EFFICIENCY:.0%}  "
        f"peak force <{V31_TARGET_PEAK_FORCE_N:.0f} N  "
        f"within-tier RBI <5%"
    )
    print()
    print(
        f"  {'Scenario':<32}  {'sched':<18}  {'kW':>6}  {'eta':>6}  "
        f"{'peak N':>7}  {'beat':>6}  {'uRBI':>5}  {'mRBI':>5}  {'LRBI':>5}"
    )
    print("-" * 105)

    lines: list[str] = [
        "GATE-4 DYNAMICS STUDY",
        f"Layout: {layout.label}",
        f"Large tier: 32.9 Hz (tuned JSON — not harmonic-locked)",
        "",
    ]
    best = None
    best_name = ""
    best_score = float("inf")

    for name, schedule, options in scenarios:
        result = simulate_mixed_ring(
            layout,
            schedule=schedule,
            cycles=args.cycles,
            build_options=options,
        )
        n = result.nvh
        per = {c.tier_index: c.total_pct for c in result.rbi.per_tier}
        micro_r = per.get(0, 0.0)
        med_r = per.get(1, 0.0)
        large_r = per.get(2, 0.0)
        line = (
            f"  {name:<32}  {schedule.value:<18}  "
            f"{result.total_elec_power_w/1000:6.1f}  {result.ring_efficiency:5.1%}  "
            f"{n.peak_resultant_n:7.0f}  {n.dominant_beat_hz:5.1f}  "
            f"{micro_r:5.2f}  {med_r:5.2f}  {large_r:5.2f}"
        )
        print(line)
        lines.append(line.strip())
        score = n.peak_resultant_n - 150.0 * max(
            0.0, result.ring_efficiency - V31_TARGET_RING_EFFICIENCY,
        )
        if score < best_score:
            best_score = score
            best = result
            best_name = name

    print("=" * 105)
    print()
    print("Gate-4 production freeze recommendation:")
    print("  Layout 4/6/2  |  tier_blocks  |  32.9 Hz large  |  GATE4_FROZEN_LOAD_POLICY")
    print("  Next optimization: phase NVH at fixed frequencies (not harmonic retune)")
    print()

    if best is not None:
        print(f"Best dynamics tradeoff this sweep: {best_name}")
        print_mixed_ring_report(best)

    lines.extend([
        "",
        "Production freeze: tier_blocks + GATE4_FROZEN_LOAD_POLICY + tuned 32.9 Hz large.",
        "Harmonic lock to 2:1 rejected — unfavorable power/efficiency trade.",
    ])
    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
