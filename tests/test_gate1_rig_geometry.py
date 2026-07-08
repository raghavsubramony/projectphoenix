"""Tests for Gate 1 lab-rig CAD geometry (no matplotlib required)."""

from __future__ import annotations

import unittest

from designs.gate1_rig_geometry import build_rig_geometry, piston_centers_mm


class Gate1RigGeometryTest(unittest.TestCase):
    def test_medium_tier_geometry_matches_twin_stroke(self) -> None:
        geom = build_rig_geometry(tier_index=1)
        self.assertEqual(geom.tier_index, 1)
        self.assertEqual(geom.displacement_cc, 300.0)
        self.assertAlmostEqual(geom.stroke_mm, 50.0, places=2)
        self.assertGreater(geom.bore_mm, 80.0)
        self.assertLess(geom.bore_mm, 90.0)
        self.assertEqual(geom.compression_ratio, 12.5)

    def test_piston_travel_stays_within_stroke(self) -> None:
        geom = build_rig_geometry(tier_index=1)
        for travel in (0.0, geom.stroke_mm * 0.25, geom.stroke_mm * 0.5, geom.stroke_mm):
            left_x, right_x, _ = piston_centers_mm(geom, travel)
            self.assertLess(left_x, right_x)
            separation = right_x - left_x
            self.assertGreater(separation, geom.chamber_gap_mm * 0.5)

    def test_micro_and_large_tiers_build(self) -> None:
        micro = build_rig_geometry(0)
        large = build_rig_geometry(2)
        self.assertLess(micro.stroke_mm, large.stroke_mm)
        self.assertLess(micro.bore_mm, large.bore_mm)


if __name__ == "__main__":
    unittest.main()
