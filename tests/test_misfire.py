"""Gate 1 misfire injection — lab-visible combustion hole."""

from __future__ import annotations

import unittest

from digital_twin import MisfireSpec, apply_misfire, run_misfire_coverage
from digital_twin.single_cylinder import SingleCylinderInputs, simulate_1d_combustion


class MisfireInjectionTest(unittest.TestCase):
    def test_full_misfire_collapses_imep(self) -> None:
        inp = SingleCylinderInputs(
            speed_rpm=2600.0,
            load_fraction=0.75,
            displacement_m3=300e-6,
        )
        healthy = simulate_1d_combustion(inp, prefer_cantera=False)
        dead = apply_misfire(healthy, MisfireSpec(severity=1.0))
        self.assertLess(dead.imep_pa, 0.15 * healthy.imep_pa)
        self.assertLess(dead.electric_efficiency, 0.15 * healthy.electric_efficiency)
        self.assertIn("misfire", dead.physics_backend)

    def test_coverage_shows_power_drop_and_fail(self) -> None:
        cov = run_misfire_coverage(severity=1.0, prefer_cantera=False)
        self.assertGreater(cov.healthy_peak_power_kw, 0.0)
        self.assertLess(cov.power_ratio, 0.25)
        self.assertGreater(cov.power_drop_pct, 70.0)
        self.assertTrue(cov.healthy_passed)
        self.assertFalse(cov.misfire_passed)

    def test_partial_burn_is_between_healthy_and_full(self) -> None:
        full = run_misfire_coverage(severity=1.0, prefer_cantera=False)
        partial = run_misfire_coverage(severity=0.4, prefer_cantera=False)
        self.assertGreater(partial.misfire_peak_power_kw, full.misfire_peak_power_kw)
        self.assertLess(partial.misfire_peak_power_kw, partial.healthy_peak_power_kw)


if __name__ == "__main__":
    unittest.main()
