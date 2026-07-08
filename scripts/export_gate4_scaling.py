"""Export virtual Gate 4 cylinder-layout sweep to CSV.

Run from repo root:

    .venv\\Scripts\\python.exe scripts\\export_gate4_scaling.py

Writes docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv (full X-ring tier-mix study).
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import (
    DEFAULT_RING_SIZES,
    gate4_scaling_report,
    run_full_gate4_study,
    write_gate4_scaling_csv,
    write_gate4_tier_advantage_csv,
)


def main() -> None:
    out_dir = _REPO / "docs" / "evidence-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries = run_full_gate4_study(ring_sizes=DEFAULT_RING_SIZES)
    csv_path = write_gate4_scaling_csv(out_dir / "GATE4-VIRTUAL-SCALING.csv", *summaries)
    phase1_open = next(s for s in summaries if s.mode == "phase1_open")
    storyboard = next(s for s in summaries if s.mode == "storyboard_ring")
    tier_path = write_gate4_tier_advantage_csv(
        out_dir / "GATE4-TIER-ADVANTAGE.csv",
        phase1_open=phase1_open,
        storyboard=storyboard,
        ring_sizes=DEFAULT_RING_SIZES,
    )
    total_rows = sum(len(s.results) for s in summaries)
    print(f"Wrote {csv_path} ({total_rows} rows)")
    print(f"Wrote {tier_path}")
    print()
    print(gate4_scaling_report(ring_sizes=DEFAULT_RING_SIZES))


if __name__ == "__main__":
    main()
