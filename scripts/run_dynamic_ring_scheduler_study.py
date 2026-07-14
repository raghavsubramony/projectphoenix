#!/usr/bin/env python3
"""Dynamic ring scheduler study — demand-based dispatch on Gate-5 production freeze.

Compares static all-on Gate-5 ring vs demand-driven cartridge enablement.

Usage::

    py -3 scripts/run_dynamic_ring_scheduler_study.py
    py -3 scripts/run_dynamic_ring_scheduler_study.py --cycles 6 --fast
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.cartridge_scheduler import (
    DispatchMode,
    build_gate5_production_options,
    default_demand_profile_w,
    inject_cartridge_fault,
    simulate_dynamic_ring_profile,
    simulate_dynamic_ring_step,
    simulate_static_gate5_ring,
)
from designs.phoenix_v3.cartridge_state import initial_ring_states
from designs.phoenix_v3_mixed_ring import (
    GATE4_LAYOUT,
    GATE5_PRODUCTION_LOAD_POLICY,
    MixedRingLayout,
    build_mixed_ring_slots,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=6)
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use reduced Gate-5 phase search steps",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "DYNAMIC-RING-SCHEDULER-STUDY.txt",
    )
    args = parser.parse_args()

    layout = MixedRingLayout(*GATE4_LAYOUT)
    if args.fast:
        build_opts = build_gate5_production_options(
            layout, capture_steps=15, capture_passes=1, phase_steps=20, cycles=6,
        )
    else:
        build_opts = build_gate5_production_options(
            layout, capture_steps=25, capture_passes=2, phase_steps=30, cycles=12,
        )

    print()
    print("=" * 105)
    print("DYNAMIC RING SCHEDULER STUDY — Gate-5 production freeze")
    print("=" * 105)
    print(
        f"  Load policy: micro x{GATE5_PRODUCTION_LOAD_POLICY.micro_multiplier:.2f}  "
        f"medium x{GATE5_PRODUCTION_LOAD_POLICY.medium_multiplier:.2f}  "
        f"large x{GATE5_PRODUCTION_LOAD_POLICY.large_multiplier:.2f}"
    )
    print()

    static = simulate_static_gate5_ring(
        layout, build_options=build_opts, cycles=args.cycles,
    )
    print("Static Gate-5 (all 12 on):")
    print(
        f"  {static.total_elec_power_w/1000:.1f} kW  "
        f"eta {static.ring_efficiency:.1%}  "
        f"tRBI {static.rbi.tier_total_pct:.2f}%  "
        f"peak {static.nvh.peak_resultant_n:.0f} N"
    )
    print()

    profile = default_demand_profile_w()
    dynamic = simulate_dynamic_ring_profile(
        layout,
        profile,
        build_options=build_opts,
        cycles_per_step=args.cycles,
    )

    print("Demand ladder:")
    for mode in DispatchMode:
        if mode == DispatchMode.OFF:
            continue
        print(f"  {mode.value}")
    print()

    print(
        f"  {'Step':>4}  {'demand kW':>9}  {'mode':<10}  {'active':>6}  "
        f"{'actual kW':>9}  {'eta':>6}  {'peak N':>7}"
    )
    print("-" * 70)

    lines: list[str] = [
        "DYNAMIC RING SCHEDULER STUDY",
        f"Layout: {layout.label}",
        "Production freeze: Gate-5 (medium-heavy load + NVH + medium capture phases)",
        "",
        f"Static all-on: {static.total_elec_power_w/1000:.1f} kW  "
        f"eta {static.ring_efficiency:.1%}  peak {static.nvh.peak_resultant_n:.0f} N",
        "",
    ]

    for i, step in enumerate(dynamic.steps):
        d = step.decision
        line = (
            f"  {i+1:4d}  {d.demand_w/1000:9.1f}  {d.mode.value:<10}  "
            f"{step.active_count:6d}  {step.total_elec_power_w/1000:9.1f}  "
            f"{step.ring_efficiency:5.1%}  {step.nvh_peak_n:7.0f}"
        )
        print(line)
        lines.append(line.strip())

    print("-" * 70)
    print(
        f"  Profile mean eta: {dynamic.mean_efficiency:.1%}  "
        f"peak NVH: {dynamic.peak_nvh_n:.0f} N"
    )
    print()

    slots = build_mixed_ring_slots(layout, build_options=build_opts)
    states = initial_ring_states(slots)
    fault_states = inject_cartridge_fault(states, 5, "cartridge_isolation")
    fault_step = simulate_dynamic_ring_step(
        slots, fault_states, 250_000.0, cycles=args.cycles,
    )
    print("Fault tolerance probe (medium slot 5 isolated, 250 kW demand):")
    print(
        f"  Active: {fault_step.active_count} cartridges  "
        f"Output: {fault_step.total_elec_power_w/1000:.1f} kW  "
        f"mode: {fault_step.decision.mode.value}"
    )
    print()

    print("Health scores (last dynamic step):")
    last = dynamic.steps[-2] if len(dynamic.steps) >= 2 else dynamic.steps[-1]
    for h in sorted(last.health, key=lambda x: x.slot_index):
        print(f"  Slot {h.slot_index:2d} ({h.tier_name[:6]}): {h.score_pct:5.1f}%")
    print()

    lines.extend([
        "",
        f"Profile mean eta: {dynamic.mean_efficiency:.1%}",
        f"Fault probe: slot 5 isolated -> {fault_step.active_count} active, "
        f"{fault_step.total_elec_power_w/1000:.1f} kW",
    ])

    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
