#!/usr/bin/env python3
"""Import Gate 1 lab CSV and report twin residuals.

Schema: docs/GATE1-LAB-RIG-DESIGN.md §7

Usage::

    # Prove the pipeline with a synthetic lab CSV (no hardware required)
    py -3 scripts/import_gate1_rig_csv.py --synthesize data/rig/demo_synthetic.csv

    # Compare a real DAQ export
    py -3 scripts/import_gate1_rig_csv.py data/rig/run_001.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin.gate1_residuals import (
    run_gate1_residual_report,
    synthesize_gate1_rig_csv,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "csv_path",
        nargs="?",
        default="data/rig/latest.csv",
        help="path to Gate 1 summary CSV (default data/rig/latest.csv)",
    )
    parser.add_argument(
        "--synthesize",
        metavar="OUT",
        help="write a synthetic twin+noise CSV to OUT, then residual-report it",
    )
    parser.add_argument(
        "--prefer-cantera",
        action="store_true",
        help="use Cantera path when available for twin side",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=0,
        help="RNG seed for --synthesize noise",
    )
    args = parser.parse_args(argv)

    path = Path(args.synthesize) if args.synthesize else Path(args.csv_path)
    if args.synthesize:
        synthesize_gate1_rig_csv(
            path,
            seed=args.seed,
            prefer_cantera=args.prefer_cantera,
        )
        print(f"Wrote synthetic Gate 1 CSV: {path}")

    if not path.exists():
        print(f"CSV not found: {path}", file=sys.stderr)
        print("Use --synthesize data/rig/demo_synthetic.csv to create a demo file.")
        return 1

    report = run_gate1_residual_report(
        path,
        prefer_cantera=args.prefer_cantera,
    )
    print(report.summary_text())
    # Synthetic noise should usually stay within default tolerances.
    return 0 if report.pass_rate_pct >= 50.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
