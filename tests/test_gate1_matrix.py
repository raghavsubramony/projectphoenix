"""Virtual Gate 1 bench matrix tests."""

from __future__ import annotations

import unittest

from digital_twin import (
    build_gate1_twin,
    gate1_bench_uncertainty,
    gate1_matrix_report,
    gate1_vehicle_fuel_comparison,
    run_gate1_matrix,
    DEFAULT_LOAD_FRACTIONS,
    DEFAULT_SPEED_RPM,
    DriveCycles,
    run,
)
from digital_twin.gate1_matrix import TIER_DISPLACEMENT_CC


class Gate1MatrixTest(unittest.TestCase):
    def test_matrix_grid_size(self) -> None:
        summary = run_gate1_matrix(prefer_cantera=False)
        expected = len(DEFAULT_SPEED_RPM) * len(DEFAULT_LOAD_FRACTIONS) * len(TIER_DISPLACEMENT_CC)
        self.assertEqual(summary.total_cells, expected)

    def test_every_cell_has_finite_measurement(self) -> None:
        summary = run_gate1_matrix(prefer_cantera=False)
        for cell in summary.cells:
            m = cell.bench.measurement
            self.assertGreater(m.peak_power_kw, 0.0)
            self.assertGreater(m.electric_efficiency, 0.0)

    def test_sweet_spot_is_medium_tier(self) -> None:
        summary = run_gate1_matrix(prefer_cantera=False)
        sweet = summary.sweet_spot()
        self.assertEqual(sweet.tier_index, 1)
        self.assertAlmostEqual(sweet.speed_rpm, 2600.0)
        self.assertAlmostEqual(sweet.load_fraction, 0.75)

    def test_uncertainty_band_ordering(self) -> None:
        bands = gate1_bench_uncertainty(trials=32, seed=0, prefer_cantera=False)
        eta = bands["electric_efficiency"]
        self.assertLessEqual(eta.p05, eta.p50)
        self.assertLessEqual(eta.p50, eta.p95)

    def test_vehicle_comparison_differs_from_tables(self) -> None:
        cmp = gate1_vehicle_fuel_comparison(prefer_cantera=False)
        self.assertGreater(cmp.fuel_l_per_100km_tables, 0.0)
        self.assertGreater(cmp.fuel_l_per_100km_gate1, 0.0)
        self.assertNotAlmostEqual(
            cmp.fuel_l_per_100km_tables,
            cmp.fuel_l_per_100km_gate1,
            places=3,
        )

    def test_build_gate1_twin_runs_cycle(self) -> None:
        twin = build_gate1_twin()
        result = run(twin, DriveCycles.highway(duration_s=300.0))
        self.assertGreater(result.fuel_l_per_100km, 0.0)

    def test_report_contains_disclaimer(self) -> None:
        text = gate1_matrix_report(uncertainty_trials=8)
        self.assertIn("DISCLAIMER", text)
        self.assertIn("simulation", text.lower())

    def test_csv_export_row_count(self) -> None:
        import tempfile
        from pathlib import Path
        from digital_twin import write_gate1_matrix_csv, gate1_matrix_csv_rows

        summary = run_gate1_matrix(prefer_cantera=False)
        rows = gate1_matrix_csv_rows(summary)
        self.assertEqual(len(rows), summary.total_cells)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_gate1_matrix_csv(Path(tmp) / "matrix.csv", summary)
            self.assertTrue(path.exists())
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), summary.total_cells + 1)

    def test_matrix_majority_pass(self) -> None:
        summary = run_gate1_matrix(prefer_cantera=False)
        self.assertGreaterEqual(summary.pass_rate_pct, 80.0)


if __name__ == "__main__":
    unittest.main()
