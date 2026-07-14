#!/usr/bin/env python3
"""Run ATPE mixed-tier ring study (Gate-4 4/6/2 + Phase-1 4/2/2 layouts).

Usage::

    py -3 scripts/run_mixed_ring_study.py
    py -3 scripts/run_mixed_ring_study.py --layout gate4 --cycles 16
    py -3 scripts/run_mixed_ring_study.py --layout phase1 --no-load-policy
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
    DEFAULT_LOAD_POLICY,
    DEFAULT_RING_SCHEDULE,
    GATE4_LAYOUT,
    PHASE1_LAYOUT,
    RBI_TARGET_PCT,
    UNITY_LOAD_POLICY,
    MixedRingLayout,
    RingSchedule,
    audit_ring_tier_configs,
    compare_schedules,
    isolated_banks_nvh,
    print_mixed_ring_report,
    print_schedule_comparison,
    print_tier_audit_report,
    simulate_mixed_ring,
)
from designs.phoenix_v3.ring_supervisor import (
    DEFAULT_SUPERVISOR_CONFIG,
    print_supervisor_report,
    simulate_supervised_mixed_ring,
)
from designs.phoenix_v3_ring import RingConfig, simulate_ring
from designs.phoenix_v3.tier_presets import load_tier_tuning_config
from designs.phoenix_v3_simulation import apply_generator_cooling


def _homogeneous_medium_baseline(cycles: int) -> None:
    """12× medium ring from legacy V3 sim (single-tier reference)."""
    cfg, path = load_tier_tuning_config(1)
    cfg = apply_generator_cooling(cfg, "water_jacket")
    result = simulate_ring(cfg, RingConfig(cartridge_count=12), cycles=cycles)
    print()
    print("=" * 72)
    print("BASELINE — homogeneous V3 ring (12 x 300 cc medium, one frequency)")
    print("=" * 72)
    print(f"  Tuning: {path.name if path else 'default'}")
    print(f"  Frequency: {cfg.frequency_hz:.1f} Hz")
    print(f"  Ring electrical power: {result.total_elec_power_w/1000:.1f} kW")
    print(f"  Ring efficiency:       {result.ring_efficiency:.1%}")
    print("=" * 72)
    print()


def _resolve_layout(name: str) -> MixedRingLayout:
    if name == "gate4":
        return MixedRingLayout(*GATE4_LAYOUT)
    if name == "phase1":
        return MixedRingLayout(*PHASE1_LAYOUT)
    raise ValueError(f"Unknown layout {name!r}; use gate4 or phase1")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=12, help="Cycles per cartridge")
    parser.add_argument(
        "--layout",
        choices=("gate4", "phase1"),
        default="gate4",
        help="Ring layout (default: gate4 = 4/6/2)",
    )
    parser.add_argument(
        "--no-load-policy",
        action="store_true",
        help="Use tuned per-tier load_fraction without ring multipliers",
    )
    parser.add_argument(
        "--supervisor",
        action="store_true",
        help="Run closed-loop ring supervisor (unity load, tier_blocks)",
    )
    parser.add_argument(
        "--supervisor-iterations",
        type=int,
        default=DEFAULT_SUPERVISOR_CONFIG.iterations,
        help="Supervisor outer-loop iterations",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "MIXED-TIER-RING-STUDY.txt",
        help="Write text report here",
    )
    args = parser.parse_args()

    layout = _resolve_layout(args.layout)
    load_policy = UNITY_LOAD_POLICY if args.no_load_policy else DEFAULT_LOAD_POLICY

    _homogeneous_medium_baseline(args.cycles)

    audits = audit_ring_tier_configs(layout, load_policy=load_policy)
    print()
    print("=" * 72)
    print(f"TIER PARAMETER AUDIT — layout {layout.label}")
    print("=" * 72)
    print_tier_audit_report(audits)
    if not all(a.valid for a in audits):
        print("  WARNING: tier audit violations detected — review tuning JSON.")
    print("=" * 72)

    if args.supervisor:
        sup_cfg = replace(
            DEFAULT_SUPERVISOR_CONFIG,
            iterations=args.supervisor_iterations,
        )
        supervised = simulate_supervised_mixed_ring(
            layout,
            schedule=DEFAULT_RING_SCHEDULE,
            supervisor=sup_cfg,
        )
        print_supervisor_report(supervised)
        return 0

    results = compare_schedules(layout, cycles=args.cycles, load_policy=load_policy)
    print_schedule_comparison(results, layout_label=layout.label)

    mixed = [r for r in results if r.schedule != RingSchedule.UNIFORM_MEDIUM]
    best_nvh = min(mixed, key=lambda r: r.nvh.peak_resultant_n)
    best_tier_rbi_val = min(r.rbi.tier_total_pct for r in mixed)
    tied_tier_rbi = [
        r.schedule.value for r in mixed
        if abs(r.rbi.tier_total_pct - best_tier_rbi_val) < 0.05
    ]
    default = simulate_mixed_ring(
        layout,
        schedule=DEFAULT_RING_SCHEDULE,
        cycles=args.cycles,
        load_policy=load_policy,
    )
    print_mixed_ring_report(default)

    iso = isolated_banks_nvh(layout)
    lines = [
        "ATPE MIXED-TIER RING STUDY (simulation)",
        f"Layout: {layout.label} (micro/medium/large)",
        f"Cycles per cartridge: {args.cycles}",
        f"Load policy: {'unity' if args.no_load_policy else 'adaptive (micro×1.00 medium×1.05 large×0.85)'}",
        "",
        "Tier parameter audit:",
    ]
    for audit in audits:
        status = "PASS" if audit.valid else "FAIL"
        lines.append(
            f"  {audit.tier_name}: {audit.displacement_cc:.0f} cc  "
            f"{audit.tuning_path or 'native'}  [{status}]"
        )
        if audit.harmonic_ratio is not None:
            lines.append(f"    harmonic ratio fast/large: {audit.harmonic_ratio:.3f}")
    lines.extend([
        "",
        "Scheduling comparison (peak frame force proxy, lower is better):",
    ])
    for r in results:
        n = r.nvh
        lines.append(
            f"  {r.schedule.value:18s}  {r.total_elec_power_w/1000:6.1f} kW  "
            f"eta {r.ring_efficiency:.1%}  tRBI {r.rbi.tier_total_pct:.1f}%  "
            f"cRBI {r.rbi.total_pct:.1f}%  "
            f"peak {n.peak_resultant_n:.0f} N  beat {n.dominant_beat_hz:.1f} Hz"
        )
    lines.append(
        f"  {'isolated_banks':18s}  {'(NVH)':>6}  "
        f"peak {iso.peak_resultant_n:.0f} N  (tier subframes, no cross-beat)"
    )
    lines.extend([
        "",
        "Recommendation:",
        f"  - Default schedule: {DEFAULT_RING_SCHEDULE.value}",
        f"  - Lowest peak frame force: {best_nvh.schedule.value} "
        f"({best_nvh.nvh.peak_resultant_n:.0f} N).",
        f"  - Lowest tier RBI: {', '.join(tied_tier_rbi)} ({best_tier_rbi_val:.1f}%, "
        f"target < {RBI_TARGET_PCT:.0f}%).",
        "  - Harmonic paired: fast bank (micro+medium interleaved) + large subharmonic bank.",
        "  - Adaptive load policy lets medium carry slightly more bus load.",
        "  - Homogeneous 12x medium ring remains simplest NVH path for production.",
        "",
        "Note: NVH figures are a lumped unbalanced-force proxy, not measured hardware.",
    ])
    text = "\n".join(lines) + "\n"
    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text(text, encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
