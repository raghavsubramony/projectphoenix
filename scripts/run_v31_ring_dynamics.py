#!/usr/bin/env python3
"""V3.1 mixed-ring dynamics study — harmonic tune, phase NVH, load sharing.

Implements observer improvement paths without changing 4/6/2 architecture:

  1. Lock large tier to fast/2 half-harmonic (~30.45 Hz)
  2. Optimize per-tier ignition phases for minimum peak frame force
  3. Weighted load policy (micro×0.98, medium×1.03, large×0.95)

Usage::

    py -3 scripts/run_v31_ring_dynamics.py
    py -3 scripts/run_v31_ring_dynamics.py --cycles 16
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# V3.1 study — includes deprecated harmonic-lock scenarios for comparison only.
HARMONIC_RATIO_TARGET_LO = 1.95
HARMONIC_RATIO_TARGET_HI = 2.05

from designs.phoenix_v3_mixed_ring import (
    DEFAULT_RING_SCHEDULE,
    GATE4_FROZEN_LOAD_POLICY,
    GATE4_LAYOUT,
    HARMONIC_RATIO_TARGET,
    MixedRingLayout,
    RingBuildOptions,
    RingSchedule,
    UNITY_LOAD_POLICY,
    V31_LOAD_POLICY,
    V31_TARGET_PEAK_FORCE_N,
    V31_TARGET_RING_EFFICIENCY,
    V31_TARGET_TIER_RBI_PCT,
    analyze_nvh_proxy,
    build_mixed_ring_slots,
    harmonic_target_large_hz,
    optimize_nvh_phase_layout,
    print_mixed_ring_report,
    simulate_mixed_ring,
    _load_tier_configs,
)


def _harmonic_ratio(layout: MixedRingLayout, options: RingBuildOptions) -> float:
    tier_cfgs, _ = _load_tier_configs(
        MixedRingLayout(*GATE4_LAYOUT),
        cooling_mode="water_jacket",
        build_options=options,
    )
    fast = sum(
        tier_cfgs[t].frequency_hz * c
        for t, c in ((0, layout.n_micro), (1, layout.n_medium))
        if c > 0 and t in tier_cfgs
    ) / max(layout.n_micro + layout.n_medium, 1)
    large = tier_cfgs[2].frequency_hz if 2 in tier_cfgs else 0.0
    return fast / large if large > 1e-9 else 0.0


def _label_result(name: str, result, *, harmonic: float) -> str:
    n = result.nvh
    rbi = result.rbi
    eta_ok = "PASS" if result.ring_efficiency >= V31_TARGET_RING_EFFICIENCY else "WARN"
    t_ok = "PASS" if rbi.tier_total_pct < V31_TARGET_TIER_RBI_PCT else "WARN"
    p_ok = "PASS" if n.peak_resultant_n < V31_TARGET_PEAK_FORCE_N else "WARN"
    h_ok = (
        "PASS"
        if HARMONIC_RATIO_TARGET_LO <= harmonic <= HARMONIC_RATIO_TARGET_HI
        else "WARN"
    )
    return (
        f"  {name:<22}  {result.total_elec_power_w/1000:6.1f} kW  "
        f"eta {result.ring_efficiency:5.1%} [{eta_ok}]  "
        f"tRBI {rbi.tier_total_pct:4.2f}% [{t_ok}]  "
        f"peak {n.peak_resultant_n:5.0f} N [{p_ok}]  "
        f"beat {n.dominant_beat_hz:4.1f} Hz  "
        f"harm {harmonic:4.3f} [{h_ok}]"
    )


def build_v31_options(
    layout: MixedRingLayout,
    *,
    harmonic_lock: bool = False,
    phase_optimize: bool = True,
    load_policy=V31_LOAD_POLICY,
    phase_steps: int = 80,
) -> RingBuildOptions:
    """Compose V3.1 options: V31 load sharing + optional NVH phase search.

    ``harmonic_lock_large`` is experimental — retuning ``frequency_hz`` alone
    without a large-tier corpus pass can destabilize harvest (see study report).
    """
    base = RingBuildOptions(
        load_policy=load_policy,
        harmonic_lock_large=harmonic_lock,
    )
    slots = build_mixed_ring_slots(
        layout,
        schedule=DEFAULT_RING_SCHEDULE,
        build_options=base,
    )
    if not phase_optimize:
        return base
    offsets = optimize_nvh_phase_layout(
        slots,
        schedule=DEFAULT_RING_SCHEDULE,
        layout_label=layout.label,
        tiers=(2, 1, 0),
        steps=phase_steps,
    )
    return replace(base, tier_phase_offsets_ms=offsets)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycles", type=int, default=12)
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "V31-RING-DYNAMICS-STUDY.txt",
    )
    args = parser.parse_args()

    layout = MixedRingLayout(*GATE4_LAYOUT)
    tier_cfgs, _ = _load_tier_configs(
        layout, cooling_mode="water_jacket",
        build_options=RingBuildOptions(load_policy=UNITY_LOAD_POLICY),
    )
    target_hz = harmonic_target_large_hz(tier_cfgs, layout)

    scenarios: list[tuple[str, RingBuildOptions]] = [
        ("baseline (unity)", RingBuildOptions(load_policy=UNITY_LOAD_POLICY)),
        (
            "harmonic large Hz",
            RingBuildOptions(load_policy=UNITY_LOAD_POLICY, harmonic_lock_large=True),
        ),
        ("V31 load policy", RingBuildOptions(load_policy=V31_LOAD_POLICY)),
        (
            "harmonic + V31 load",
            RingBuildOptions(load_policy=V31_LOAD_POLICY, harmonic_lock_large=True),
        ),
        (
            "V3.1 recommended",
            build_v31_options(layout, harmonic_lock=False, phase_steps=60),
        ),
        (
            "V3.1 + harmonic (experimental)",
            build_v31_options(layout, harmonic_lock=True, phase_steps=40),
        ),
    ]

    print()
    print("=" * 100)
    print("V3.1 MIXED RING DYNAMICS — Gate-4 layout 4/6/2")
    print("=" * 100)
    print(f"  Fast-bank target large Hz (fast/2): {target_hz:.2f} Hz")
    print(f"  Targets: eta >{V31_TARGET_RING_EFFICIENCY:.0%}  "
          f"tRBI <{V31_TARGET_TIER_RBI_PCT:.0f}%  "
          f"peak <{V31_TARGET_PEAK_FORCE_N:.0f} N  "
          f"harm {HARMONIC_RATIO_TARGET_LO:.2f}–{HARMONIC_RATIO_TARGET_HI:.2f}")
    print()
    print(f"  {'Scenario':<22}  {'kW':>6}  {'eta':>7}       "
          f"{'tRBI':>8}       {'peak N':>10}  {'beat':>8}  {'harm':>8}")
    print("-" * 100)

    lines = [
        "V3.1 MIXED RING DYNAMICS STUDY",
        f"Layout: {layout.label}",
        f"Target large Hz (fast/{HARMONIC_RATIO_TARGET:.0f}): {target_hz:.2f}",
        f"Cycles per cartridge: {args.cycles}",
        "",
    ]
    best = None
    best_name = ""
    for name, options in scenarios:
        result = simulate_mixed_ring(
            layout,
            schedule=DEFAULT_RING_SCHEDULE,
            cycles=args.cycles,
            build_options=options,
        )
        harm = _harmonic_ratio(layout, options)
        line = _label_result(name, result, harmonic=harm)
        print(line)
        lines.append(line.strip())
        score = (
            result.nvh.peak_resultant_n
            - 200.0 * max(0.0, result.ring_efficiency - V31_TARGET_RING_EFFICIENCY)
        )
        if best is None or score < (
            best.nvh.peak_resultant_n
            - 200.0 * max(0.0, best.ring_efficiency - V31_TARGET_RING_EFFICIENCY)
        ):
            best = result
            best_name = name

    print("=" * 100)
    if best is not None:
        print(f"\nBest NVH scenario: {best_name}")
        print_mixed_ring_report(best)

    lines.extend([
        "",
        f"Best NVH scenario: {best_name}",
        "",
        "Note: harmonic lock adjusts large tier frequency_hz within tier bounds.",
        "Changing Hz alone (without large-tier corpus re-tune) can reduce harvest.",
        "Phase optimization minimizes lumped peak resultant force (NVH proxy).",
        "Recommended production path: V31 load policy on tuned 32.9 Hz large JSON.",
    ])
    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
