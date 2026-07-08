"""Export a text evidence pack for pitches, grants, and data rooms.

Run from repo root:

    .venv\\Scripts\\python.exe scripts\\export_evidence_pack.py

Writes docs/evidence-pack/EVIDENCE-BASELINE.txt with executive summary,
ICE benchmark headline, fault tolerance, virtual Gate 1 bench matrix, and
verify hint. Pure stdlib except digital_twin imports.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin import (
    build_executive_summary,
    benchmark_summary,
    graceful_degradation_table,
    fleet_graceful_degradation,
    gate1_matrix_report,
    gate4_scaling_report,
    write_gate1_matrix_csv,
    run_gate1_matrix,
    run_full_gate4_study,
    DEFAULT_RING_SIZES,
    write_gate4_scaling_csv,
    write_gate4_tier_advantage_csv,
    wltp_benchmark,
)


def _repo_root() -> Path:
    return _REPO


def _build_report() -> str:
    lines: list[str] = [
        "PROJECT PHOENIX — EVIDENCE BASELINE (auto-generated)",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "DISCLAIMER: All figures below are simulation outputs unless marked measured.",
        "Prefix external claims with 'simulation shows…'. Hardware gates not cleared.",
        "",
        "=" * 72,
        "EXECUTIVE SUMMARY",
        "=" * 72,
        build_executive_summary(trials=16).report(),
        "",
        "=" * 72,
        "MOVE N — ATPE vs CONVENTIONAL 2.0L TURBO (identical vehicle stack)",
        "=" * 72,
        benchmark_summary(),
        "",
        wltp_benchmark(),
        "",
        "=" * 72,
        "FAULT TOLERANCE (ERS §4.8)",
        "=" * 72,
        graceful_degradation_table(fleet_graceful_degradation()),
        "",
        "=" * 72,
        "VIRTUAL GATE 1 BENCH (digital prototype — not measured)",
        "=" * 72,
        gate1_matrix_report(uncertainty_trials=32),
        "",
        "=" * 72,
        "VIRTUAL GATE 4 SCALING (multi-cylinder layout search)",
        "=" * 72,
        gate4_scaling_report(),
        "",
        "=" * 72,
        "GATE 1 CSV (spreadsheet)",
        "=" * 72,
        "  Regenerate:",
        "    .venv\\Scripts\\python.exe scripts\\export_gate1_matrix.py",
        "  File:",
        "    docs/evidence-pack/GATE1-VIRTUAL-BENCH-MATRIX.csv",
        "",
        "=" * 72,
        "GATE 4 CSV (layout sweep)",
        "=" * 72,
        "  Regenerate:",
        "    .venv\\Scripts\\python.exe scripts\\export_gate4_scaling.py",
        "  File:",
        "    docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv",
        "",
        "=" * 72,
        "INTEGRITY CHECK",
        "=" * 72,
        "  Run full verification:",
        "    .venv\\Scripts\\python.exe verify.py",
        "  Quick smoke (~10 s):",
        "    .venv\\Scripts\\python.exe verify.py --quick",
        "",
        "  See docs/DEVELOPMENT-PLAYBOOK.md for pitch, IP, and Gate 1 rig planning.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    out_dir = _repo_root() / "docs" / "evidence-pack"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "EVIDENCE-BASELINE.txt"
    text = _build_report()
    out_path.write_text(text, encoding="utf-8")
    summary = run_gate1_matrix(prefer_cantera=False)
    csv_path = write_gate1_matrix_csv(out_dir / "GATE1-VIRTUAL-BENCH-MATRIX.csv", summary)
    gate4_summaries = run_full_gate4_study(ring_sizes=DEFAULT_RING_SIZES)
    gate4_csv = write_gate4_scaling_csv(
        out_dir / "GATE4-VIRTUAL-SCALING.csv", *gate4_summaries,
    )
    phase1_open = next(s for s in gate4_summaries if s.mode == "phase1_open")
    storyboard = next(s for s in gate4_summaries if s.mode == "storyboard_ring")
    tier_csv = write_gate4_tier_advantage_csv(
        out_dir / "GATE4-TIER-ADVANTAGE.csv",
        phase1_open=phase1_open,
        storyboard=storyboard,
        ring_sizes=DEFAULT_RING_SIZES,
    )
    print(f"Wrote {out_path} ({len(text)} bytes)")
    print(f"Wrote {csv_path} ({summary.total_cells} rows)")
    print(f"Wrote {gate4_csv} ({sum(len(s.results) for s in gate4_summaries)} rows)")
    print(f"Wrote {tier_csv}")
    print()
    print(text[:1200])
    if len(text) > 1200:
        print("...")
        print(f"(full report in {out_path})")


if __name__ == "__main__":
    main()
