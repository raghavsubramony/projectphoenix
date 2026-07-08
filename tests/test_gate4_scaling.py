"""Virtual Gate 4 multi-cylinder layout search tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from digital_twin import (
    CylinderLayout,
    DEFAULT_RING_SIZES,
    REFERENCE_LAYOUT,
    build_atpe_from_layout,
    build_x12_homogeneous_atpe,
    charge_sustaining_fuel_config,
    build_tier_architecture_comparison,
    count_active_tiers,
    enumerate_ring_layouts,
    evaluate_layout,
    gate4_scaling_report,
    gate4_tier_architecture_table,
    is_all_three_tiers,
    is_design_aligned,
    layout_tier_mix_kind,
    ring_label,
    run_full_gate4_study,
    run_gate4_sweep,
    run_ring_size_study,
    write_gate4_scaling_csv,
    write_gate4_tier_advantage_csv,
)
from digital_twin.gate4_scaling import enumerate_layouts


class Gate4ScalingTest(unittest.TestCase):
    def test_reference_layout_matches_phase1(self) -> None:
        self.assertEqual(REFERENCE_LAYOUT, (4, 2, 2))

    def test_ring_label(self) -> None:
        self.assertEqual(ring_label(12), "X12")

    def test_enumerate_ring_layouts_x4(self) -> None:
        layouts = enumerate_ring_layouts(4)
        self.assertEqual(len(layouts), 15)
        self.assertTrue(all(l.total_cylinders == 4 for l in layouts))

    def test_enumerate_ring_layouts_x8(self) -> None:
        self.assertEqual(len(enumerate_ring_layouts(8)), 45)

    def test_layout_tier_mix_kind(self) -> None:
        self.assertEqual(layout_tier_mix_kind(CylinderLayout(0, 8, 0)), "homogeneous_medium")
        self.assertEqual(layout_tier_mix_kind(CylinderLayout(2, 3, 3)), "mixed")

    def test_build_atpe_total_power(self) -> None:
        layout = CylinderLayout(4, 2, 2)
        atpe = build_atpe_from_layout(layout)
        self.assertAlmostEqual(atpe.max_electric_w / 1000.0, 230.0, delta=1.0)

    def test_reference_layout_passes_ers(self) -> None:
        r = evaluate_layout(CylinderLayout(*REFERENCE_LAYOUT))
        self.assertTrue(r.all_ers_pass)
        self.assertTrue(r.is_reference)
        self.assertAlmostEqual(r.highway_fuel_l_per_100km, 4.46, delta=0.05)

    def test_sweep_finds_passing_layouts(self) -> None:
        summary = run_gate4_sweep(max_total=10, max_per_tier=5)
        self.assertGreater(len(summary.passing), 0)

    def test_ring_study_covers_all_sizes(self) -> None:
        summary = run_ring_size_study((4, 6), rating_profile="storyboard")
        self.assertEqual(len(summary.for_ring(4)), 15)
        self.assertEqual(len(summary.for_ring(6)), 28)

    def test_best_per_ring_design_aligned(self) -> None:
        summary = run_ring_size_study((8, 12), rating_profile="storyboard")
        best = summary.best_per_ring(design_aligned_only=True)
        self.assertIn(8, best)
        self.assertGreaterEqual(best[8].layout.n_medium, 1)
        self.assertNotEqual(best[8].tier_mix_kind, "homogeneous_micro")

    def test_all_micro_not_design_aligned_storyboard(self) -> None:
        self.assertFalse(is_design_aligned(
            CylinderLayout(12, 0, 0), ring_size=12, rating_profile="storyboard",
        ))
        self.assertTrue(is_design_aligned(
            CylinderLayout(0, 12, 0), ring_size=12, rating_profile="storyboard",
        ))

    def test_phase1_requires_multi_tier(self) -> None:
        self.assertFalse(is_design_aligned(
            CylinderLayout(4, 0, 0), rating_profile="phase1",
        ))
        self.assertTrue(is_design_aligned(
            CylinderLayout(4, 2, 2), rating_profile="phase1",
        ))

    def test_count_active_tiers(self) -> None:
        self.assertEqual(count_active_tiers(CylinderLayout(4, 2, 2)), 3)
        self.assertEqual(count_active_tiers(CylinderLayout(7, 1, 0)), 2)
        self.assertTrue(is_all_three_tiers(CylinderLayout(4, 2, 2)))
        self.assertFalse(is_all_three_tiers(CylinderLayout(7, 1, 0)))

    def test_no_single_tier_passes_full_ers(self) -> None:
        summary = run_gate4_sweep(max_total=10, max_per_tier=5)
        singles = [r for r in summary.passing if count_active_tiers(r.layout) == 1]
        self.assertEqual(len(singles), 0)

    def test_three_tier_passes_and_reference_in_comparison(self) -> None:
        summary = run_gate4_sweep(max_total=10, max_per_tier=5)
        three = summary.best_with_tier_depth(3, design_aligned_only=True)
        self.assertIsNotNone(three)
        assert three is not None
        self.assertTrue(three.all_ers_pass)
        rows = build_tier_architecture_comparison(summary)
        categories = {r.category for r in rows}
        self.assertIn("reference_4_2_2", categories)
        self.assertIn("three_tier", categories)

    def test_storyboard_has_all_three_tier_pick_per_ring(self) -> None:
        summary = run_ring_size_study((8, 12), rating_profile="storyboard")
        best_3 = summary.best_per_ring(
            design_aligned_only=True, all_three_tiers_only=True,
        )
        self.assertIn(8, best_3)
        self.assertIn(12, best_3)
        self.assertTrue(is_all_three_tiers(best_3[12].layout))

    def test_tier_advantage_csv_export(self) -> None:
        summaries = run_full_gate4_study(ring_sizes=(4, 6), include_phase1_open=True)
        phase1_open = next(s for s in summaries if s.mode == "phase1_open")
        storyboard = next(s for s in summaries if s.mode == "storyboard_ring")
        with tempfile.TemporaryDirectory() as tmp:
            path = write_gate4_tier_advantage_csv(
                Path(tmp) / "tier_adv.csv",
                phase1_open=phase1_open,
                storyboard=storyboard,
                ring_sizes=(4, 6),
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("reference_4_2_2", text)
            self.assertIn("all_three_tier_design_aligned", text)

    def test_report_includes_three_tier_thesis(self) -> None:
        text = gate4_scaling_report(ring_sizes=(4, 6), include_phase1_open=True)
        self.assertIn("Three-tier thesis", text)
        self.assertIn("3-tier", text)
        self.assertIn("4/2/2", text)

    def test_x12_ring_has_expected_power(self) -> None:
        atpe = build_x12_homogeneous_atpe(12)
        self.assertAlmostEqual(atpe.max_electric_w / 1000.0, 936.0, delta=1.0)

    def test_csv_export_full_study(self) -> None:
        summaries = run_full_gate4_study(ring_sizes=(4, 6), include_phase1_open=False)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_gate4_scaling_csv(Path(tmp) / "gate4.csv", *summaries)
            lines = path.read_text(encoding="utf-8").strip().splitlines()
            expected = sum(len(s.results) for s in summaries)
            self.assertEqual(len(lines), expected + 1)
            self.assertIn("active_tier_count", lines[0])
            self.assertIn("all_three_tiers", lines[0])

    def test_report_lists_all_ring_sizes(self) -> None:
        text = gate4_scaling_report(ring_sizes=(4, 6), include_phase1_open=False)
        self.assertIn("X4", text)
        self.assertIn("X6", text)
        self.assertIn("DISCLAIMER", text)

    def test_default_ring_sizes(self) -> None:
        self.assertEqual(DEFAULT_RING_SIZES, (4, 6, 8, 10, 12, 14, 16))


if __name__ == "__main__":
    unittest.main()
