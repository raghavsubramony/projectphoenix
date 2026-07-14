#!/usr/bin/env python3
"""Validate exported best-tuning JSON for every ATPE cartridge tier."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.tier_profiles import TIER_PROFILES
from digital_twin.phoenix_v3_bridge import measure_v3_cartridge, measure_v3_ring


def main() -> int:
    print("=" * 72)
    print("Phoenix V3 best-tuning validation (all tiers)")
    print("=" * 72)
    for profile in TIER_PROFILES:
        metrics = measure_v3_cartridge(cycles=24, tier_index=profile.tier_index)
        ring = measure_v3_ring(cycles=60, tier_index=profile.tier_index)
        print(f"\n{profile.name} ({profile.total_displacement_cc:.0f} cc total)")
        print(f"  Tuning file:     {profile.best_tuning_filename}")
        print(f"  Frequency:       {metrics.frequency_hz:.1f} Hz")
        print(f"  Load:            {metrics.load_fraction * 100:.0f}%")
        print(f"  Net efficiency:  {metrics.net_efficiency * 100:.2f}%")
        print(f"  Capture:         {metrics.capture_fraction * 100:.1f}%")
        print(f"  Elec/cycle:      {metrics.elec_energy_j:.1f} J")
        print(f"  Elec power:      {metrics.elec_power_w / 1000:.2f} kW")
        print(f"  Peak pressure:   {metrics.peak_pressure_bar:.0f} bar")
        print(f"  Stroke:          {metrics.stroke_mm:.1f} mm")
        print(f"  Spring recovery: {metrics.spring_recovery * 100:.1f}%")
        print(
            f"  Energy balance:  {metrics.energy_balance_valid}"
            f"  |  Boundary OK: {metrics.raw_boundary_valid}"
        )
        print(f"  Ring efficiency: {ring.ring_efficiency * 100:.2f}% (12 cartridges)")
        print(f"  Ring power:      {ring.ring_power_w / 1000:.1f} kW")
    print("\n" + "=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
