"""Vehicle ECU runtime — proves Layer-2 code runs without the digital twin."""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ecu import (
    ECU_FLASH_ID,
    BrainToEcuBridge,
    DtcCode,
    ModeCode,
    SafeMode,
    SensorFrame,
    VehicleEcuRuntime,
)
from ecu.bus import BrainSetpointFrame, CartridgeSetpoint
from ecu.domains import BufferEcu


def _frame(
    mode: ModeCode,
    enabled: tuple[int, ...],
    demand_w: float = 90_000.0,
) -> BrainSetpointFrame:
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
        buffer_precharge_w=4_000.0,
        brain_alive=True,
    )


class VehicleEcuTest(unittest.TestCase):
    def test_flash_identity(self) -> None:
        ecu = VehicleEcuRuntime()
        self.assertEqual(ecu.flash_id, ECU_FLASH_ID)
        self.assertIn("PHOENIX-V31-ECU", ecu.manifest.fingerprint())

    def test_ecu_applies_brain_setpoints(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.CITY, (0, 1, 2, 3)))
        for _ in range(20):
            result = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(result.command.watchdog_ok)
        self.assertFalse(result.command.safe_state)
        self.assertEqual(set(ecu.enabled_indices(result)), {0, 1, 2, 3})
        self.assertEqual(result.command.mode, ModeCode.CITY)

    def test_watchdog_fails_off_safe_state(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.IDLE, (0, 1)))
        for _ in range(15):
            good = ecu.tick()
        self.assertTrue(good.command.watchdog_ok)
        self.assertEqual(set(ecu.enabled_indices(good)), {0, 1})

        ecu.watchdog.tripped = True
        ecu.watchdog.reason = "injected"
        hold = ecu.tick()
        self.assertTrue(hold.command.safe_state)
        self.assertFalse(hold.command.watchdog_ok)
        self.assertEqual(hold.command.mode, ModeCode.OFF)
        self.assertEqual(hold.command.safe_mode, SafeMode.EMERGENCY_OFF)
        self.assertEqual(hold.command.buffer_assist_w, 0.0)
        self.assertEqual(hold.command.buffer_burst_w, 0.0)
        self.assertEqual(hold.command.buffer_precharge_w, 0.0)
        self.assertEqual(ecu.enabled_indices(hold), ())
        self.assertTrue(all(s.ignition_scale == 0.0 for s in hold.command.slots))
        self.assertTrue(ecu.dtc.has(DtcCode.EMERGENCY_OFF))
        self.assertTrue(hold.command.notes.startswith("emergency_off:"))

    def test_brain_timeout_without_fresh_accept(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.CITY, (0, 1)))
        ecu.tick(SensorFrame(buffer_soc=0.7))
        ticks = int(ecu.watchdog.brain_timeout_s / (ecu.cycle_ms / 1000.0)) + 5
        last = None
        for _ in range(ticks):
            last = ecu.tick(SensorFrame(buffer_soc=0.7))
        assert last is not None
        self.assertTrue(last.command.safe_state)
        self.assertEqual(ecu.watchdog.reason, "brain_timeout")
        self.assertEqual(ecu.enabled_indices(last), ())

    def test_brain_not_alive_trips_immediately(self) -> None:
        ecu = VehicleEcuRuntime()
        frame = _frame(ModeCode.CITY, (0, 1))
        ecu.accept_brain(replace(frame, brain_alive=False))
        result = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(result.command.safe_state)
        self.assertEqual(ecu.watchdog.reason, "brain_not_alive")

    def test_thermal_inhibit_disables_hot_slot(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.HIGHWAY, (0, 1, 4, 5)))
        sensors = SensorFrame(buffer_soc=0.7)
        sensors.slot_wall_temp_c[4] = 210.0
        for _ in range(20):
            result = ecu.tick(sensors)
        enabled = set(ecu.enabled_indices(result))
        self.assertNotIn(4, enabled)
        self.assertIn(0, enabled)

    def test_nan_wall_temp_inhibits_slot(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.HIGHWAY, (0, 1, 2)))
        sensors = SensorFrame(buffer_soc=0.7)
        sensors.slot_wall_temp_c[1] = float("nan")
        for _ in range(20):
            result = ecu.tick(sensors)
        enabled = set(ecu.enabled_indices(result))
        self.assertNotIn(1, enabled)
        self.assertIn(0, enabled)
        self.assertEqual(result.command.slots[1].ignition_scale, 0.0)

    def test_nonfinite_soc_cuts_buffer(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.CITY, (0, 1)))
        for _ in range(15):
            ecu.tick(SensorFrame(buffer_soc=0.7))
        bad = ecu.tick(SensorFrame(buffer_soc=float("nan")))
        self.assertEqual(bad.command.buffer_assist_w, 0.0)
        self.assertEqual(bad.command.buffer_burst_w, 0.0)
        self.assertEqual(bad.command.buffer_precharge_w, 0.0)

    def test_buffer_brain_safe_false_cuts_precharge(self) -> None:
        buf = BufferEcu()
        a, b, p = buf.step(
            assist_w=10_000.0,
            burst_w=50_000.0,
            precharge_w=5_000.0,
            soc=0.7,
            brain_safe=False,
        )
        self.assertEqual((a, b, p), (0.0, 0.0, 0.0))

    def test_bridge_strips_invalid_indices(self) -> None:
        class FakeCmd:
            mode = "idle"
            demand_w = 30_000.0
            target_power_w = 30_000.0
            enabled_indices = (0, 1, 99)
            load_scales = {0: 1.0, 1: 1.0, 99: 1.0}
            buffer_assist_w = 0.0
            buffer_precharge_w = 0.0
            buffer_burst_w = 0.0

        bridge = BrainToEcuBridge(slot_count=12)
        frame = bridge.from_brain_commands(FakeCmd())
        self.assertTrue(all(c.slot_index < 12 for c in frame.cartridges))
        enabled = [c.slot_index for c in frame.cartridges if c.enabled]
        self.assertNotIn(99, enabled)
        self.assertEqual(enabled, [0, 1])

    def test_brain_commands_integration(self) -> None:
        try:
            from atpe_brain.hil import synthetic_commands
        except ImportError as exc:
            self.skipTest(f"atpe_brain unavailable: {exc}")

        ecu = VehicleEcuRuntime()
        cmd = synthetic_commands(90_000.0)
        ecu.accept_brain(cmd)
        for _ in range(20):
            result = ecu.tick()
        self.assertTrue(result.command.watchdog_ok)
        self.assertEqual(result.flash_id, ECU_FLASH_ID)

    def test_cycle_overrun_trips_watchdog(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.IDLE, (0, 1)))
        ecu.tick()
        bad = ecu.tick(simulate_elapsed_s=0.100)
        self.assertTrue(bad.command.safe_state)
        self.assertIn("overrun", ecu.watchdog.reason or bad.command.notes)
        self.assertEqual(ecu.enabled_indices(bad), ())

    def test_watchdog_reset_required_after_trip(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.CITY, (0,)))
        for _ in range(15):
            ecu.tick(SensorFrame(buffer_soc=0.7))
        ecu.watchdog.tripped = True
        hold = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(hold.command.safe_state)
        ecu.accept_brain(_frame(ModeCode.CITY, (0,)))
        still = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(still.command.safe_state)
        ecu.clear_faults()
        ecu.accept_brain(_frame(ModeCode.CITY, (0,)))
        for _ in range(20):
            ok = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(ok.command.watchdog_ok)
        self.assertIn(0, ecu.enabled_indices(ok))

    def test_stale_sensor_inhibits_and_raises_dtc(self) -> None:
        ecu = VehicleEcuRuntime()
        for _ in range(15):
            ecu.accept_brain(_frame(ModeCode.CITY, (0, 1)))
            ecu.tick(SensorFrame(buffer_soc=0.7))
        stale = SensorFrame(
            buffer_soc=0.7,
            stamped_t_s=max(0.0, ecu._t_s - 0.080),
        )
        result = ecu.tick(stale)
        self.assertEqual(ecu.enabled_indices(result), ())
        self.assertTrue(ecu.dtc.has(DtcCode.SENSOR_STALE))
        self.assertTrue(any("SENSOR_STALE" in d for d in result.command.active_dtcs))

    def test_controlled_shutdown_ramps_then_off(self) -> None:
        ecu = VehicleEcuRuntime()
        for _ in range(20):
            ecu.accept_brain(_frame(ModeCode.CITY, (0, 1)))
            last = ecu.tick(SensorFrame(buffer_soc=0.7))
        self.assertTrue(ecu.enabled_indices(last))
        ecu.request_controlled_shutdown("unit_test")
        saw_ramp = False
        final = None
        for _ in range(25):
            final = ecu.tick(SensorFrame(buffer_soc=0.7))
            self.assertEqual(final.command.safe_mode, SafeMode.CONTROLLED_SHUTDOWN)
            self.assertTrue(final.command.watchdog_ok)
            self.assertTrue(all(s.ignition_scale == 0.0 for s in final.command.slots))
            if "ramp" in final.command.notes:
                saw_ramp = True
        assert final is not None
        self.assertTrue(saw_ramp or "complete" in final.command.notes)
        self.assertEqual(ecu.enabled_indices(final), ())
        self.assertNotEqual(final.command.safe_mode, SafeMode.EMERGENCY_OFF)

    def test_low_bus_voltage_cuts_assist(self) -> None:
        ecu = VehicleEcuRuntime()
        for _ in range(15):
            ecu.accept_brain(_frame(ModeCode.CITY, (0, 1)))
            good = ecu.tick(SensorFrame(buffer_soc=0.7, bus_voltage_v=400.0))
        self.assertGreater(good.command.buffer_assist_w, 0.0)
        low = ecu.tick(SensorFrame(buffer_soc=0.7, bus_voltage_v=200.0))
        self.assertEqual(low.command.buffer_assist_w, 0.0)
        self.assertEqual(low.command.buffer_burst_w, 0.0)
        self.assertTrue(ecu.dtc.has(DtcCode.DC_PRECHARGE_NOT_READY))

    def test_invalid_position_inhibits_slot(self) -> None:
        ecu = VehicleEcuRuntime()
        ecu.accept_brain(_frame(ModeCode.CITY, (0, 1, 2)))
        sensors = SensorFrame(buffer_soc=0.7)
        sensors.position_valid[1] = False
        for _ in range(20):
            ecu.accept_brain(_frame(ModeCode.CITY, (0, 1, 2)))
            result = ecu.tick(sensors)
        enabled = set(ecu.enabled_indices(result))
        self.assertNotIn(1, enabled)
        self.assertIn(0, enabled)
        self.assertTrue(ecu.dtc.has(DtcCode.SENSOR_INVALID_POSITION))


if __name__ == "__main__":
    unittest.main()
