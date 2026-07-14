#!/usr/bin/env python3
"""Demo: fleet weight adaptation under poor thermal scores."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from atpe_brain.fleet_learning import FleetSample, FleetWeightAdapter
from atpe_brain.optimizer import OptimizationScore, OptimizerWeights


def main() -> int:
    adapter = FleetWeightAdapter(OptimizerWeights(), learning_rate=0.08)
    print("GATE6-FLEET-LEARNING base", adapter.weights)
    for i in range(8):
        # Simulated thermal-stressed mission.
        sc = OptimizationScore(
            total=0.55,
            efficiency=0.75,
            health=0.80,
            thermal_margin=0.40,
            balance=0.70,
            fault_tolerance=0.85,
        )
        w = adapter.observe(FleetSample(score=sc, mode="thermal"))
        print(
            f"  obs {i + 1}: thermal_w={w.thermal_margin:.3f} "
            f"eff={w.efficiency:.3f} health={w.health:.3f}"
        )
    assert adapter.weights.thermal_margin > OptimizerWeights().normalized().thermal_margin
    print("STATUS=FLEET_LEARNING_OK thermal weight rose under stress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
