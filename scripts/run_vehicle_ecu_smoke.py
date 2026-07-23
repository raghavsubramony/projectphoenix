#!/usr/bin/env python3
"""Smoke-prove the vehicle ECU runtime that ships to the car.

This does NOT run the digital twin. It only exercises Layer-2 ECU code:
Brain setpoints → VehicleEcuRuntime (10 ms) → ActuatorCommand.

Usage::

    py -3 scripts/run_vehicle_ecu_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ecu import ECU_FLASH_ID, ModeCode, SensorFrame, VehicleEcuRuntime
from ecu.bus import BrainSetpointFrame, CartridgeSetpoint
from ecu.identity import EcuBuildManifest


def _city_frame(seq: int = 1) -> BrainSetpointFrame:
    carts = tuple(
        CartridgeSetpoint(
            slot_index=i,
            enabled=i < 4,
            load_scale=0.85 if i < 4 else 0.0,
            ignition_scale=1.0 if i < 4 else 0.0,
            generator_force_scale=1.0 if i < 4 else 0.0,
        )
        for i in range(12)
    )
    return BrainSetpointFrame(
        sequence=seq,
        mode=ModeCode.CITY,
        demand_w=90_000.0,
        target_power_w=90_000.0,
        cartridges=carts,
        buffer_assist_w=8_000.0,
        buffer_burst_w=25_000.0,
        brain_alive=True,
    )


def main() -> int:
    manifest = EcuBuildManifest()
    print("VEHICLE ECU SMOKE")
    print(f"  flash_id={manifest.flash_id}")
    print(f"  fingerprint={manifest.fingerprint()}")
    print(f"  cycle={manifest.cycle_ms} ms  slots={manifest.slot_count}")
    print()

    ecu = VehicleEcuRuntime()
    assert ecu.flash_id == ECU_FLASH_ID

    ecu.accept_brain(_city_frame())
    sensors = SensorFrame(buffer_soc=0.72)
    last = None
    for i in range(25):
        last = ecu.tick(sensors)
    assert last is not None

    enabled = ecu.enabled_indices(last)
    print(f"  mode={last.command.mode.name}")
    print(f"  enabled_slots={enabled}")
    print(f"  assist_w={last.command.buffer_assist_w:.0f}")
    print(f"  burst_w={last.command.buffer_burst_w:.0f}")
    print(f"  watchdog_ok={last.command.watchdog_ok}")
    print(f"  safe_state={last.command.safe_state}")
    print(f"  notes={last.command.notes}")

    # Watchdog hold path
    ecu.watchdog.tripped = True
    ecu.watchdog.reason = "smoke_trip"
    hold = ecu.tick(sensors)
    print(f"  after_trip safe_state={hold.command.safe_state} assist={hold.command.buffer_assist_w:.0f}")

    ok = (
        last.command.watchdog_ok
        and set(enabled) == {0, 1, 2, 3}
        and hold.command.safe_state
        and hold.command.buffer_assist_w == 0.0
    )
    print()
    print(f"OVERALL={'PASS' if ok else 'FAIL'}")
    print(f"RUNNING_ON_ECU_FLASH={ECU_FLASH_ID}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
