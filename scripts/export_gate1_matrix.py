"""Export the virtual Gate 1 bench matrix to CSV for spreadsheets.

Run from repo root:

    .venv\\Scripts\\python.exe scripts\\export_gate1_matrix.py

Writes docs/evidence-pack/GATE1-VIRTUAL-BENCH-MATRIX.csv
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import gate1_matrix_report, run_gate1_matrix, write_gate1_matrix_csv


def main() -> None:
    out_dir = _REPO / "docs" / "evidence-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "GATE1-VIRTUAL-BENCH-MATRIX.csv"
    summary = run_gate1_matrix(prefer_cantera=False)
    write_gate1_matrix_csv(csv_path, summary)
    print(f"Wrote {csv_path} ({summary.total_cells} rows)")
    print()
    print(gate1_matrix_report(summary, uncertainty_trials=16))


if __name__ == "__main__":
    main()
