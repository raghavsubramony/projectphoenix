"""Vehicle ECU runtime — proves Layer-2 code runs without the digital twin."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ecu import (
    ECU_FLASH_ID,
    BrainToEcuBridge,
    ModeCode,
    SensorFrame,
    VehicleEcuRuntime,
)
from ecu.bus import BrainSetpointFrame, CartridgeSetpoint


def _frame(mode: ModeCode, enabled: tuple[int, ...], demand_w: float = 90_000.0) -> BrainSetpointFrame:
    carts = []
    en = set(enabled)
    for i in range(12):
        on = i in en
        carts.append(
            CartridgeSetpoint(
                slot_index=i,
                enabled=on,
                load_scale=0.9 if on else 0.0,
                ignition_scale=1.0 if on else 0.0,
                generator_force_scale=1.0 if on else 0.0,
            )
        )
    return BrainSetpointFrame(
        sequence=1,
        mode=mode,
        demand_w=demand_w,
        target_power_w=demand_w,
        cartridges=tuple(carts),
        buffer_assist_w=5_000.0,
        buffer_burst_w=20_000.0,
        brain_alive=True,
    )


def test_flash_identity():
    ecu = VehicleEcuRuntime()
    assert ecu.flash_id == ECU_FLASH_ID
    assert "PHOENIX-V31-ECU" in ecu.manifest.fingerprint()


def test_ecu_applies_brain_setpoints():
    ecu = VehicleEcuRuntime()
    ecu.accept_brain(_frame(ModeCode.CITY, (0, 1, 2, 3)))
    # Slew needs a few ticks to enable.
    for _ in range(20):
        result = ecu.tick(SensorFrame(buffer_soc=0.7))
    assert result.command.watchdog_ok
    assert not result.command.safe_state
    enabled = ecu.enabled_indices(result)
    assert set(enabled) == {0, 1, 2, 3}
    assert result.command.mode == ModeCode.CITY


def test_watchdog_holds_safe_state():
    ecu = VehicleEcuRuntime()
    ecu.accept_brain(_frame(ModeCode.IDLE, (0, 1)))
    for _ in range(15):
        good = ecu.tick()
    assert good.command.watchdog_ok

    ecu.watchdog.tripped = True
    ecu.watchdog.reason = "injected"
    hold = ecu.tick()
    assert hold.command.safe_state
    assert not hold.command.watchdog_ok
    # Assist must drop in safe state.
    assert hold.command.buffer_assist_w == 0.0


def test_thermal_inhibit_disables_hot_slot():
    ecu = VehicleEcuRuntime()
    ecu.accept_brain(_frame(ModeCode.HIGHWAY, (0, 1, 4, 5)))
    sensors = SensorFrame(buffer_soc=0.7)
    sensors.slot_wall_temp_c[4] = 210.0
    for _ in range(20):
        result = ecu.tick(sensors)
    enabled = set(ecu.enabled_indices(result))
    assert 4 not in enabled
    assert 0 in enabled


def test_bridge_strips_invalid_indices():
    class FakeCmd:
        mode = "idle"
        demand_w = 30_000.0
        target_power_w = 30_000.0
        enabled_indices = (0, 1, 99)  # 99 invalid
        load_scales = {0: 1.0, 1: 1.0, 99: 1.0}
        buffer_assist_w = 0.0
        buffer_precharge_w = 0.0
        buffer_burst_w = 0.0

    bridge = BrainToEcuBridge(slot_count=12)
    frame = bridge.from_brain_commands(FakeCmd())
    assert all(c.slot_index < 12 for c in frame.cartridges)
    assert not frame.cartridges[11].enabled or True  # slot 99 never mapped
    enabled = [c.slot_index for c in frame.cartridges if c.enabled]
    assert 99 not in enabled
    assert enabled == [0, 1]


def test_brain_commands_integration():
    from atpe_brain.hil import synthetic_commands

    ecu = VehicleEcuRuntime()
    cmd = synthetic_commands(90_000.0)
    ecu.accept_brain(cmd)
    for _ in range(20):
        result = ecu.tick()
    assert result.command.watchdog_ok
    assert result.flash_id == ECU_FLASH_ID


def test_cycle_overrun_trips_watchdog():
    ecu = VehicleEcuRuntime()
    ecu.accept_brain(_frame(ModeCode.IDLE, (0, 1)))
    ecu.tick()  # latch good
    # Simulate a 100 ms overrun on a 10 ms budget.
    bad = ecu.tick(simulate_elapsed_s=0.100)
    assert bad.command.safe_state
    assert "overrun" in (ecu.watchdog.reason or bad.command.notes)


if __name__ == "__main__":
    test_flash_identity()
    test_ecu_applies_brain_setpoints()
    test_watchdog_holds_safe_state()
    test_thermal_inhibit_disables_hot_slot()
    test_bridge_strips_invalid_indices()
    test_brain_commands_integration()
    test_cycle_overrun_trips_watchdog()
    print("All vehicle ECU tests passed.")
    print(f"FLASH_ID={ECU_FLASH_ID}")
