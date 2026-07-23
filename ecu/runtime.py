"""Vehicle ECU runtime — the code that runs on the car.

Call ``tick()`` every ``cycle_ms`` (default 10 ms). The ATPE Brain feeds
setpoints via ``accept_brain()`` on a slower cadence; between brain frames
the ECU holds and slews.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .brain_bridge import BrainToEcuBridge
from .bus import (
    ActuatorCommand,
    BrainSetpointFrame,
    ModeCode,
    SensorFrame,
    SlotActuatorOut,
)
from .domains import BufferEcu, CombustionEcu, GeneratorEcu, MotionEcu
from .identity import ECU_CYCLE_MS, EcuBuildManifest
from .safety import SafeState
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
        latency_budget_s: float = 0.050,
    ) -> None:
        self.manifest = EcuBuildManifest(cycle_ms=cycle_ms)
        self.cycle_ms = cycle_ms
        self.latency_budget_s = latency_budget_s
        self.watchdog = Watchdog(cycle_budget_s=cycle_ms / 1000.0)
        self.safe = SafeState(slot_count=self.manifest.slot_count)
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
        return frame

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
            cmd = self.safe.hold(sequence_ack=0, reason="no_brain_frame")
            return EcuTickResult(
                command=cmd,
                flash_id=self.flash_id,
                cycle_ms=self.cycle_ms,
                elapsed_s=0.0,
                brain_sequence=0,
            )

        self.watchdog.check_brain_alive(self._t_s, frame.brain_alive)
        if self.watchdog.tripped:
            cmd = self.safe.hold(
                sequence_ack=frame.sequence,
                reason=self.watchdog.reason or "watchdog",
            )
            elapsed = simulate_elapsed_s if simulate_elapsed_s is not None else (time.perf_counter() - t0)
            return EcuTickResult(
                command=cmd,
                flash_id=self.flash_id,
                cycle_ms=self.cycle_ms,
                elapsed_s=elapsed,
                brain_sequence=frame.sequence,
            )

        thermal_inhibit = {
            i
            for i, temp in enumerate(sensors.slot_wall_temp_c)
            if temp > 205.0
        }
        motion_out = self.motion.step(frame.cartridges, dt)
        ign_out = self.combustion.step(frame.cartridges, thermal_inhibit=thermal_inhibit)
        gen_out = self.generator.step(frame.cartridges, sensors, dt)
        assist_w, burst_w, pre_w = self.buffer.step(
            assist_w=frame.buffer_assist_w,
            burst_w=frame.buffer_burst_w,
            precharge_w=frame.buffer_precharge_w,
            soc=sensors.buffer_soc,
            brain_safe=True,
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

        elapsed = simulate_elapsed_s if simulate_elapsed_s is not None else (time.perf_counter() - t0)
        self.watchdog.kick_cycle(self._t_s, elapsed)
        latency_ok = elapsed <= self.latency_budget_s

        if self.watchdog.tripped:
            cmd = self.safe.hold(
                sequence_ack=frame.sequence,
                reason=self.watchdog.reason or "cycle_overrun",
            )
        else:
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
                notes="ecu_ok",
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
