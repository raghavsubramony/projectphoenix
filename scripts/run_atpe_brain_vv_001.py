#!/usr/bin/env python3
"""ATPE-BRAIN-VV-001 — HIL stub verification (no hardware).

Checks BrainCommands -> DeterministicEcuStub latency, index closure, watchdog.

Usage::

    py -3 scripts/run_atpe_brain_vv_001.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from atpe_brain.hil import (
    DeterministicEcuStub,
    HilHarness,
    VehicleEcuAdapter,
    synthetic_commands,
)
from atpe_brain.optimizer import BrainCommands, OptimizationScore
from designs.phoenix_v3.cartridge_scheduler import DispatchMode


def _run_cases(label: str, harness: HilHarness) -> int:
    cases = [
        ("idle", synthetic_commands(10_000.0)),
        ("track", synthetic_commands(95_000.0)),
        ("step_down", BrainCommands(
            demand_w=40_000.0,
            mode=DispatchMode.IDLE,
            enabled_indices=(0, 1),
            load_scales={0: 0.5, 1: 0.4},
            target_power_w=40_000.0,
            score=OptimizationScore(0.75, 0.7, 0.8, 0.7, 0.7, 0.8),
        )),
    ]
    fails = 0
    print(label)
    for name, cmd in cases:
        r = harness.step(cmd)
        status = "PASS" if r.pass_ else "FAIL"
        flash = r.ack.flash_id or "?"
        print(f"  {name}: {status} flash={flash} latency={r.ack.latency_s * 1e3:.1f}ms "
              f"indices={r.ack.applied_indices} reasons={r.reasons}")
        if not r.pass_:
            fails += 1

    last = harness.step(synthetic_commands(80_000.0))
    harness.ecu.watchdog_ok = False
    trip = harness.step(synthetic_commands(120_000.0))
    if trip.pass_:
        print("  watchdog: FAIL (expected fail)")
        fails += 1
    else:
        hold_ok = (
            trip.ack.applied_indices == last.ack.applied_indices
            and trip.ack.applied_mode == last.ack.applied_mode
        )
        print(f"  watchdog: PASS hold={hold_ok} reasons={trip.reasons}")
        if not hold_ok:
            fails += 1
    return fails


def main() -> int:
    fails = 0
    fails += _run_cases(
        "ATPE-BRAIN-VV-001 vehicle ECU runtime",
        HilHarness(VehicleEcuAdapter()),
    )
    fails += _run_cases(
        "ATPE-BRAIN-VV-001 legacy stub",
        HilHarness(DeterministicEcuStub(latency_s=0.02, scale_lag=0.5)),
    )

    slow = HilHarness(DeterministicEcuStub(latency_s=0.08))
    bad = slow.step(synthetic_commands(50_000.0))
    print(f"  latency_budget: {'PASS' if not bad.pass_ else 'FAIL'} "
          f"(expect reject) reasons={bad.reasons}")
    if bad.pass_:
        fails += 1

    overall = "PASS" if fails == 0 else "FAIL"
    print(f"OVERALL={overall} fails={fails}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
