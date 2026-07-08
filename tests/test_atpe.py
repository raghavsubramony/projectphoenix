"""ATPE tier allocation and blended-efficiency tests."""

from __future__ import annotations

import unittest

from digital_twin import phase1_config
from digital_twin.atpe import ATPE


class AtpeWeightedTierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.atpe = ATPE(phase1_config().atpe)

    def test_single_tier_uses_tier_efficiency(self) -> None:
        rec = self.atpe.generate(20_000.0, 1.0)
        self.assertAlmostEqual(rec.efficiency, 0.44, places=4)
        self.assertEqual(rec.active_index, 0)

    def test_no_efficiency_cliff_at_tier_boundary(self) -> None:
        at_110 = self.atpe.generate(110_000.0, 1.0)
        at_111 = self.atpe.generate(111_000.0, 1.0)
        self.assertAlmostEqual(at_110.efficiency, at_111.efficiency, delta=0.005)
        self.assertGreater(at_111.efficiency, 0.41)

    def test_full_stack_blended_efficiency(self) -> None:
        rec = self.atpe.generate(230_000.0, 1.0)
        # 30@44% + 80@41% + 120@38% => 39.72% fuel->electrical
        self.assertAlmostEqual(rec.efficiency, 0.3972, places=3)
        self.assertEqual(rec.active_index, 2)

    def test_mid_load_uses_weighted_not_governing_only(self) -> None:
        rec = self.atpe.generate(150_000.0, 1.0)
        self.assertAlmostEqual(rec.efficiency, 0.4070, places=3)
        self.assertGreater(rec.efficiency, 0.38)

    def test_partial_second_tier_beats_flat_governing_efficiency(self) -> None:
        rec = self.atpe.generate(50_000.0, 1.0)
        # 30@44% + 20@41% => 42.74%, not flat Tier 2 @ 41%
        self.assertAlmostEqual(rec.efficiency, 0.4274, places=3)
        self.assertEqual(rec.active_index, 1)

    def test_weighted_beats_legacy_governing_penalty(self) -> None:
        rec = self.atpe.generate(150_000.0, 1.0)
        legacy_governing_fuel_w = rec.electric_w / 0.38
        self.assertLess(rec.fuel_power_w, legacy_governing_fuel_w)


if __name__ == "__main__":
    unittest.main()
