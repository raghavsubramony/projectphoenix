#!/usr/bin/env python3
"""Compare tier-lump V3 twin vs Gate-5 dynamic ring dispatch on drive cycles.

Usage::

    py -3 scripts/run_dynamic_ring_twin_study.py --quick
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import DriveCycles, build_dynamic_ring_twin, build_phoenix_v3_twin, run
from digital_twin.config import with_dynamic_ring, with_phoenix_v3, phase1_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true")
    parser.add_argument(
        "--export",
        type=Path,
        default=_REPO / "docs" / "evidence-pack" / "DYNAMIC-RING-TWIN-STUDY.txt",
    )
    args = parser.parse_args()

    duration = 300.0 if args.quick else 1200.0
    cycles = [
        ("mixed", DriveCycles.mixed(duration_s=duration)),
        ("highway", DriveCycles.highway(duration_s=duration)),
    ]

    print()
    print("=" * 90)
    print("DYNAMIC RING TWIN STUDY — tier-lump vs Gate-5 demand dispatch")
    print("=" * 90)
    print()

    lines: list[str] = [
        "DYNAMIC RING TWIN STUDY",
        f"Duration per cycle: {duration:.0f} s",
        "",
    ]

    configs = [
        ("Tier-lump V3", build_phoenix_v3_twin(v3_cycles=8)),
        ("Gate-5 dynamic ring", build_dynamic_ring_twin(probe_cycles=4, fast_probe=True)),
    ]

    for label, twin in configs:
        print(f"--- {label} ---")
        lines.append(f"--- {label} ---")
        for name, cycle in cycles:
            result = run(twin, cycle)
            modes: dict[str, int] = {}
            cartridges: list[int] = []
            for step in result.records:
                if step.dispatch_mode:
                    modes[step.dispatch_mode] = modes.get(step.dispatch_mode, 0) + 1
                if step.active_cartridges:
                    cartridges.append(step.active_cartridges)
            mean_active = sum(cartridges) / len(cartridges) if cartridges else 0.0
            line = (
                f"  {name:<10}  fuel {result.equiv_fuel_l_per_100km:5.2f} L/100km  "
                f"eta {result.mean_efficiency*100:4.1f}%  "
                f"shortfalls {result.shortfall_events}  "
                f"mean active carts {mean_active:4.1f}"
            )
            print(line)
            lines.append(line.strip())
            if modes:
                mode_str = ", ".join(f"{k}:{v}" for k, v in sorted(modes.items()))
                print(f"    dispatch modes: {mode_str}")
                lines.append(f"    dispatch modes: {mode_str}")
        print()
        lines.append("")

    args.export.parent.mkdir(parents=True, exist_ok=True)
    args.export.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report written: {args.export}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
