"""Gate 1 single-cylinder model tests.

Pure-stdlib unittest; Cantera is optional and not required by these checks.
"""

from __future__ import annotations

from dataclasses import replace
import unittest

from digital_twin import phase1_config
from digital_twin.atpe import ATPE
from digital_twin.config import SingleCylinderGate1Config
from digital_twin.single_cylinder import (
    FreePistonConfig,
    SingleCylinderInputs,
    gate1_point_from_load,
    simulate_1d_combustion,
    simulate_free_piston,
)


class SingleCylinderPhysicsTest(unittest.TestCase):
    def test_surrogate_cycle_produces_trace_and_metrics(self) -> None:
        inp = SingleCylinderInputs(
            speed_rpm=2600.0,
            load_fraction=0.65,
            displacement_m3=300e-6,
        )
        r = simulate_1d_combustion(inp, prefer_cantera=False)
        self.assertGreater(r.indicated_efficiency, 0.10)
        self.assertGreater(r.electric_efficiency, 0.05)
        self.assertGreater(r.imep_pa, 0.0)
        self.assertGreater(r.peak_pressure_pa, 100_000.0)
        self.assertEqual(len(r.trace.crank_deg), len(r.trace.pressure_pa))
        self.assertEqual(len(r.trace.volume_m3), len(r.trace.heat_release_j))

    def test_free_piston_predicts_tdc_within_stroke(self) -> None:
        inp = SingleCylinderInputs(
            speed_rpm=2400.0,
            load_fraction=0.8,
            displacement_m3=750e-6,
        )
        comb = simulate_1d_combustion(inp, prefer_cantera=False)
        fp = simulate_free_piston(comb, FreePistonConfig())
        self.assertGreaterEqual(fp.predicted_tdc_m, 0.0)
        self.assertLessEqual(fp.predicted_tdc_m, FreePistonConfig().stroke_m)
        self.assertEqual(len(fp.t_s), len(fp.x_m))

    def test_gate1_point_adapter_fields_are_finite(self) -> None:
        p = gate1_point_from_load(
            speed_rpm=2600.0,
            load_fraction=0.7,
            displacement_cc=300.0,
            generator_efficiency=0.96,
            prefer_cantera=False,
        )
        self.assertGreater(p.electric_efficiency, 0.05)
        self.assertGreater(p.imep_bar, 0.0)
        self.assertGreaterEqual(p.knock_index, 0.0)


class AtpeGate1IntegrationTest(unittest.TestCase):
    def test_gate1_path_populates_combustion_telemetry(self) -> None:
        base = phase1_config().atpe
        cfg = replace(
            base,
            gate1=SingleCylinderGate1Config(
                enabled=True,
                prefer_cantera=False,
                reference_speed_rpm=2500.0,
            ),
        )
        atpe = ATPE(cfg)
        rec = atpe.generate(40_000.0, 1.0)
        self.assertGreater(rec.efficiency, 0.0)
        self.assertGreater(rec.imep_bar, 0.0)
        self.assertGreater(rec.peak_pressure_bar, 0.0)

    def test_tier_profiles_produce_distinct_peaks(self) -> None:
        from digital_twin.single_cylinder import gate1_cycle_trace
        results = [
            gate1_cycle_trace(2600.0, 0.8, cc, 0.96, prefer_cantera=False,
                              tier_index=i)
            for i, cc in enumerate((100.0, 300.0, 750.0))
        ]
        peaks = [r.peak_pressure_pa for r in results]
        self.assertNotEqual(peaks[0], peaks[2])


class Gate1BenchTest(unittest.TestCase):
    def test_bench_stroke_matches_phoenix_x12(self) -> None:
        from digital_twin import gate1_bench_at_load, PHOENIX_X12_STROKE_MM
        r = gate1_bench_at_load(prefer_cantera=False, tier_index=1)
        self.assertAlmostEqual(r.measurement.stroke_mm, PHOENIX_X12_STROKE_MM,
                               delta=0.1)
        self.assertGreater(len(r.checks), 0)

    def test_bench_measurement_fields_are_finite(self) -> None:
        from digital_twin import gate1_bench_at_load
        m = gate1_bench_at_load(prefer_cantera=False).measurement
        self.assertGreater(m.peak_power_kw, 0.0)
        self.assertGreater(m.core_temp_c, 0.0)
        self.assertGreater(m.electric_efficiency, 0.0)


if __name__ == "__main__":
    unittest.main()
