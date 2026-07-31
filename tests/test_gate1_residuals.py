"""Gate 1 rig CSV schema + twin residual report tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from digital_twin.gate1_residuals import (
    GATE1_RIG_CSV_COLUMNS,
    read_gate1_rig_csv,
    run_gate1_residual_report,
    synthesize_gate1_rig_csv,
)


class Gate1ResidualsTest(unittest.TestCase):
    def test_synthesize_schema_and_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synth.csv"
            synthesize_gate1_rig_csv(path, seed=1, prefer_cantera=False)
            rows = read_gate1_rig_csv(path)
            self.assertGreaterEqual(len(rows), 3)
            text = path.read_text(encoding="utf-8").splitlines()[0]
            for col in GATE1_RIG_CSV_COLUMNS:
                self.assertIn(col, text)

    def test_residual_report_passes_low_noise_synthetic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "synth.csv"
            synthesize_gate1_rig_csv(
                path, noise_frac=0.01, seed=2, prefer_cantera=False,
            )
            report = run_gate1_residual_report(path, prefer_cantera=False)
            self.assertGreaterEqual(report.pass_rate_pct, 75.0)
            self.assertIn("residual report", report.summary_text().lower())

    def test_missing_column_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.csv"
            path.write_text("timestamp_utc,tier_index\n2026-01-01T00:00:00Z,1\n",
                            encoding="utf-8")
            with self.assertRaises(ValueError):
                read_gate1_rig_csv(path)


if __name__ == "__main__":
    unittest.main()
