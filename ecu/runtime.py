"""Vehicle ECU runtime — the code that runs on the car.

Call ``tick()`` every ``cycle_ms`` (default 10 ms). The ATPE Brain feeds
setpoints via ``accept_brain()`` on a slower cadence; between brain frames
the ECU holds and slews.

Watchdog / hard faults → EMERGENCY_OFF.
Operator / ModeCode.OFF → CONTROLLED_SHUTDOWN ramp, then OFF.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace

from .brain_bridge import BrainToEcuBridge
from .bus import (
    ActuatorCommand,
    BrainSetpointFrame,
    ModeCode,
    SafeMode,
    SensorFrame,
    SlotActuatorOut,
)
from .domains import BufferEcu, CombustionEcu, GeneratorEcu, MotionEcu
from .dtc import DtcCode, DtcStore
from .identity import ECU_CYCLE_MS, EcuBuildManifest
from .limits import ECU_DC_PRECHARGE_READY_V, ECU_LATENCY_BUDGET_S
from .safety import SafeState
from .sensors import apply_assessment_dtcs, assess_sensors
from .watchdog import Watchdog


@dataclass(frozen=True)
class EcuTickResult:
    """One 10 ms ECU cycle output."""

    command: ActuatorCommand
    flash_id: str
    cycle_ms: int
    elapsed_s: float
    brain_sequence: int


class VehicleEcuRuntime:
    """Integrated Motion + Combustion + Generator + Buffer + Ring arbitration."""

    def __init__(
        self,
        *,
        cycle_ms: int = ECU_CYCLE_MS,
        latency_budget_s: float = ECU_LATENCY_BUDGET_S,
    ) -> None:
        self.manifest = EcuBuildManifest(cycle_ms=cycle_ms)
        self.cycle_ms = cycle_ms
        self.latency_budget_s = latency_budget_s
        self.watchdog = Watchdog(cycle_budget_s=cycle_ms / 1000.0)
        self.safe = SafeState(slot_count=self.manifest.slot_count)
        self.dtc = DtcStore()
        self.bridge = BrainToEcuBridge(slot_count=self.manifest.slot_count)
        self.motion = MotionEcu()
        self.combustion = CombustionEcu()
        self.generator = GeneratorEcu()
        self.buffer = BufferEcu()
        self._setpoints: BrainSetpointFrame | None = None
        self._t_s = 0.0

    @property
    def flash_id(self) -> str:
        return self.manifest.flash_id

    def accept_brain(self, commands: object) -> BrainSetpointFrame:
        """Ingest ATPE BrainCommands (or BrainSetpointFrame) into ECU memory."""
        if isinstance(commands, BrainSetpointFrame):
            frame = commands
        else:
            frame = self.bridge.from_brain_commands(commands)
        self._setpoints = frame
        self.watchdog.kick_brain(self._t_s)
        if frame.mode == ModeCode.OFF:
            self.request_controlled_shutdown("brain_mode_off")
        return frame

    def request_controlled_shutdown(self, reason: str = "operator_stop") -> None:
        """Begin a ramped shutdown (distinct from watchdog emergency OFF)."""
        self.dtc.raise_(DtcCode.CONTROLLED_SHUTDOWN)
        self.safe.request_controlled_shutdown(reason)

    def clear_faults(self) -> None:
        """Recover path: clear DTCs and sticky watchdog after a safe stop."""
        self.watchdog.reset()
        self.dtc.clear()
        self.safe.shutdown = None

    def tick(
        self,
        sensors: SensorFrame | None = None,
        *,
        dt_s: float | None = None,
        simulate_elapsed_s: float | None = None,
    ) -> EcuTickResult:
        """Run one deterministic ECU cycle. No AI — only setpoints + sensors."""
        t0 = time.perf_counter()
        dt = self.cycle_ms / 1000.0 if dt_s is None else dt_s
        self._t_s += dt
        sensors = sensors or SensorFrame(t_s=self._t_s)

        frame = self._setpoints
        if frame is None:
            self.dtc.raise_(DtcCode.NO_BRAIN_FRAME)
            self.dtc.raise_(DtcCode.EMERGENCY_OFF)
            cmd = self.safe.hold(
                sequence_ack=0,
                reason="no_brain_frame",
                active_dtcs=self.dtc.active_names,
            )
            return EcuTickResult(
                command=cmd,
                flash_id=self.flash_id,
                cycle_ms=self.cycle_ms,
                elapsed_s=0.0,
                brain_sequence=0,
            )

        # Controlled shutdown: operator/thermal stop has authority — keep brain
        # watchdog kicked so a ramp is not aborted by BRAIN_TIMEOUT.
        shutting_down = self.safe.shutdown is not None
        if shutting_down:
            self.watchdog.kick_brain(self._t_s)
        else:
            self.watchdog.check_brain_alive(self._t_s, frame.brain_alive)

        if self.watchdog.tripped:
            reason = self.watchdog.reason or "watchdog"
            if "timeout" in reason:
                self.dtc.raise_(DtcCode.BRAIN_TIMEOUT)
            elif "not_alive" in reason:
                self.dtc.raise_(DtcCode.BRAIN_NOT_ALIVE)
            elif "overrun" in reason:
                self.dtc.raise_(DtcCode.CYCLE_OVERRUN)
            self.dtc.raise_(DtcCode.EMERGENCY_OFF)
            cmd = self.safe.hold(
                sequence_ack=frame.sequence,
                reason=reason,
                active_dtcs=self.dtc.active_names,
            )
            elapsed = (
                simulate_elapsed_s
                if simulate_elapsed_s is not None
                else (time.perf_counter() - t0)
            )
            return EcuTickResult(
                command=cmd,
                flash_id=self.flash_id,
                cycle_ms=self.cycle_ms,
                elapsed_s=elapsed,
                brain_sequence=frame.sequence,
            )

        # Controlled shutdown takes priority over normal setpoints (not over watchdog).
        # After the ramp completes, stay latched OFF until clear_faults().
        if self.safe.shutdown is not None:
            if not self.safe.shutdown.complete:
                cmd = self.safe.shutdown.step(
                    motion=self.motion,
                    slot_count=self.manifest.slot_count,
                    dt_s=dt,
                    sequence_ack=frame.sequence,
                    active_dtcs=self.dtc.active_names,
                )
            else:
                cmd = replace(
                    self.safe.hold(
                        sequence_ack=frame.sequence,
                        reason=f"controlled_shutdown_latched:{self.safe.shutdown.reason}",
                        active_dtcs=self.dtc.active_names,
                        clear_shutdown=False,
                    ),
                    watchdog_ok=True,
                    safe_mode=SafeMode.CONTROLLED_SHUTDOWN,
                    notes=f"controlled_shutdown_latched:{self.safe.shutdown.reason}",
                )
            elapsed = (
                simulate_elapsed_s
                if simulate_elapsed_s is not None
                else (time.perf_counter() - t0)
            )
            return EcuTickResult(
                command=cmd,
                flash_id=self.flash_id,
                cycle_ms=self.cycle_ms,
                elapsed_s=elapsed,
                brain_sequence=frame.sequence,
            )

        assessment = assess_sensors(
            sensors,
            ecu_t_s=self._t_s,
            slot_count=self.manifest.slot_count,
        )
        apply_assessment_dtcs(self.dtc, assessment)
        thermal_inhibit = set(assessment.inhibit_slots)
        brain_safe = (not self.watchdog.tripped) and assessment.sensors_ok

        motion_out = self.motion.step(frame.cartridges, dt)
        ign_out = self.combustion.step(frame.cartridges, thermal_inhibit=thermal_inhibit)
        gen_out = self.generator.step(
            frame.cartridges, sensors, dt, inhibited=thermal_inhibit,
        )
        if (
            not math.isfinite(sensors.bus_voltage_v)
            or sensors.bus_voltage_v < ECU_DC_PRECHARGE_READY_V
        ):
            self.dtc.raise_(DtcCode.DC_PRECHARGE_NOT_READY)
        assist_w, burst_w, pre_w = self.buffer.step(
            assist_w=frame.buffer_assist_w,
            burst_w=frame.buffer_burst_w,
            precharge_w=frame.buffer_precharge_w,
            soc=sensors.buffer_soc,
            brain_safe=brain_safe,
            bus_voltage_v=sensors.bus_voltage_v,
        )

        slots: list[SlotActuatorOut] = []
        for sp in frame.cartridges:
            en, load = motion_out.get(sp.slot_index, (False, 0.0))
            if sp.slot_index in thermal_inhibit:
                en = False
                load = 0.0
            slots.append(
                SlotActuatorOut(
                    slot_index=sp.slot_index,
                    enable=en,
                    load_fraction=load,
                    ignition_scale=ign_out.get(sp.slot_index, 0.0),
                    generator_force_scale=gen_out.get(sp.slot_index, 0.0),
                    valve_authority=1.0 if en else 0.0,
                )
            )

        elapsed = (
            simulate_elapsed_s
            if simulate_elapsed_s is not None
            else (time.perf_counter() - t0)
        )
        self.watchdog.kick_cycle(self._t_s, elapsed)
        latency_ok = elapsed <= self.latency_budget_s

        if self.watchdog.tripped:
            self.dtc.raise_(DtcCode.CYCLE_OVERRUN)
            self.dtc.raise_(DtcCode.EMERGENCY_OFF)
            cmd = self.safe.hold(
                sequence_ack=frame.sequence,
                reason=self.watchdog.reason or "cycle_overrun",
                active_dtcs=self.dtc.active_names,
            )
        else:
            notes = "ecu_ok" if assessment.sensors_ok else "ecu_ok_sensor_degraded"
            if assessment.age_s > 0.0:
                notes = f"{notes};sensor_age_ms={assessment.age_s * 1e3:.1f}"
            cmd = ActuatorCommand(
                sequence_ack=frame.sequence,
                mode=frame.mode,
                slots=tuple(slots),
                buffer_assist_w=assist_w,
                buffer_burst_w=burst_w,
                buffer_precharge_w=pre_w,
                safe_state=False,
                watchdog_ok=True,
                latency_budget_ok=latency_ok,
                notes=notes,
                safe_mode=SafeMode.NORMAL,
                active_dtcs=self.dtc.active_names,
            )
            self.safe.latch(cmd)

        return EcuTickResult(
            command=cmd,
            flash_id=self.flash_id,
            cycle_ms=self.cycle_ms,
            elapsed_s=elapsed,
            brain_sequence=frame.sequence,
        )

    def enabled_indices(self, result: EcuTickResult) -> tuple[int, ...]:
        return tuple(s.slot_index for s in result.command.slots if s.enable)
