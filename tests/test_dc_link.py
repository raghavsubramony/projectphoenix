"""HV DC-link / inverter limit tests (opt-in path)."""

from __future__ import annotations

import unittest

from digital_twin import DriveCycles, phase1_config, run, with_dc_link
from digital_twin.dc_link import DcLink, DcLinkConfig
from digital_twin.powertrain import Powertrain


class DcLinkLimiterTest(unittest.TestCase):
    def test_disabled_is_noop(self) -> None:
        link = DcLink(DcLinkConfig(enabled=False))
        g, clip = link.limit_generation(500_000.0, 0.01)
        self.assertEqual(g, 500_000.0)
        self.assertEqual(clip, 0.0)

    def test_generation_clip_at_peak(self) -> None:
        link = DcLink(
            DcLinkConfig(
                enabled=True,
                generator_continuous_w=100_000.0,
                generator_peak_w=120_000.0,
                peak_hold_s=2.0,
            )
        )
        g, clip = link.limit_generation(200_000.0, 0.01)
        self.assertAlmostEqual(g, 120_000.0)
        self.assertAlmostEqual(clip, 80_000.0)

    def test_precharge_blocks_discharge(self) -> None:
        link = DcLink(
            DcLinkConfig(
                enabled=True,
                initial_voltage_v=200.0,
                precharge_ready_v=360.0,
            )
        )
        self.assertFalse(link.precharge_ready)
        allowed, clip = link.limit_storage_discharge(
            50_000.0, 0.01, generation_on_bus_w=0.0,
        )
        self.assertEqual(allowed, 0.0)
        self.assertEqual(clip, 50_000.0)


class DcLinkPowertrainTest(unittest.TestCase):
    def test_default_config_unchanged_without_dc_link(self) -> None:
        twin = Powertrain(phase1_config())
        r = run(twin, DriveCycles.highway(duration_s=60.0, dt_s=1.0))
        self.assertEqual(r.shortfall_events, 0)
        self.assertFalse(any(rec.dc_link_limited for rec in r.records))

    def test_tight_bus_causes_clip_or_shortfall(self) -> None:
        cfg = with_dc_link(
            phase1_config(),
            DcLinkConfig(
                enabled=True,
                continuous_bus_w=40_000.0,
                peak_bus_w=45_000.0,
                peak_hold_s=0.05,
                generator_continuous_w=40_000.0,
                generator_peak_w=45_000.0,
                initial_voltage_v=400.0,
                precharge_ready_v=360.0,
            ),
        )
        twin = Powertrain(cfg)
        r = run(twin, DriveCycles.mixed(duration_s=90.0, dt_s=1.0))
        clipped = sum(1 for rec in r.records if rec.inverter_clip_w > 1.0)
        shortfalls = sum(1 for rec in r.records if rec.shortfall_w > 1.0)
        self.assertTrue(
            clipped > 0 or shortfalls > 0,
            "tight inverter should clip or shortfall under mixed load",
        )

    def test_precharge_voltage_blocks_launch(self) -> None:
        cfg = with_dc_link(
            phase1_config(),
            DcLinkConfig(
                enabled=True,
                initial_voltage_v=100.0,
                precharge_ready_v=360.0,
                continuous_bus_w=160_000.0,
                peak_bus_w=240_000.0,
                generator_continuous_w=150_000.0,
                generator_peak_w=200_000.0,
            ),
        )
        twin = Powertrain(cfg)
        self.assertFalse(twin.dc_link.precharge_ready)
        # One hard demand step while bus is not ready.
        rec = twin.step(20.0, 2.0, 0.0, 0.1)
        self.assertFalse(twin.dc_link.precharge_ready)
        self.assertGreater(rec.shortfall_w, 1_000.0)
        self.assertLess(twin.dc_link.voltage_v, 360.0)


if __name__ == "__main__":
    unittest.main()
