"""Safe-state — emergency fail-OFF vs controlled shutdown ramp."""

from __future__ import annotations

from dataclasses import dataclass

from .bus import ActuatorCommand, CartridgeSetpoint, ModeCode, SafeMode, SlotActuatorOut
from .domains import MotionEcu
from .limits import ECU_CONTROLLED_SHUTDOWN_S


def _idle_slots(n: int = 12) -> tuple[SlotActuatorOut, ...]:
    return tuple(
        SlotActuatorOut(
            slot_index=i,
            enable=False,
            load_fraction=0.0,
            ignition_scale=0.0,
            generator_force_scale=0.0,
            valve_authority=0.0,
        )
        for i in range(n)
    )


def _zero_setpoints(slot_count: int) -> tuple[CartridgeSetpoint, ...]:
    return tuple(
        CartridgeSetpoint(
            slot_index=i,
            enabled=False,
            load_scale=0.0,
            ignition_scale=0.0,
            generator_force_scale=0.0,
        )
        for i in range(slot_count)
    )


@dataclass
class ControlledShutdown:
    """Ramp loads to zero over ``duration_s``, then cut ignition hard."""

    reason: str
    duration_s: float = ECU_CONTROLLED_SHUTDOWN_S
    elapsed_s: float = 0.0
    complete: bool = False

    def step(
        self,
        *,
        motion: MotionEcu,
        slot_count: int,
        dt_s: float,
        sequence_ack: int,
        active_dtcs: tuple[str, ...],
    ) -> ActuatorCommand:
        self.elapsed_s += dt_s
        motion_out = motion.step(_zero_setpoints(slot_count), dt_s)
        loads = [motion_out.get(i, (False, 0.0))[1] for i in range(slot_count)]
        max_load = max(loads) if loads else 0.0
        # Kill ignition immediately; only mechanical load is ramped down.
        ign_cut = True
        done = self.elapsed_s >= self.duration_s or max_load < 0.01
        if done:
            self.complete = True
            return ActuatorCommand(
                sequence_ack=sequence_ack,
                mode=ModeCode.OFF,
                slots=_idle_slots(slot_count),
                buffer_assist_w=0.0,
                buffer_burst_w=0.0,
                buffer_precharge_w=0.0,
                safe_state=True,
                watchdog_ok=True,
                latency_budget_ok=True,
                notes=f"controlled_shutdown_complete:{self.reason}",
                safe_mode=SafeMode.CONTROLLED_SHUTDOWN,
                active_dtcs=active_dtcs,
            )

        slots = tuple(
            SlotActuatorOut(
                slot_index=i,
                enable=load > 0.01,
                load_fraction=load,
                ignition_scale=0.0 if ign_cut else 0.0,
                generator_force_scale=0.0,
                valve_authority=0.0,
            )
            for i, load in enumerate(loads)
        )
        return ActuatorCommand(
            sequence_ack=sequence_ack,
            mode=ModeCode.OFF,
            slots=slots,
            buffer_assist_w=0.0,
            buffer_burst_w=0.0,
            buffer_precharge_w=0.0,
            safe_state=True,
            watchdog_ok=True,
            latency_budget_ok=True,
            notes=f"controlled_shutdown_ramp:{self.reason}",
            safe_mode=SafeMode.CONTROLLED_SHUTDOWN,
            active_dtcs=active_dtcs,
        )


@dataclass
class SafeState:
    """Records last-good command; emergency hold() fails OFF immediately."""

    latched: ActuatorCommand | None = None
    slot_count: int = 12
    shutdown: ControlledShutdown | None = None

    def latch(self, cmd: ActuatorCommand) -> None:
        if not cmd.safe_state and cmd.watchdog_ok and cmd.safe_mode == SafeMode.NORMAL:
            self.latched = cmd

    def request_controlled_shutdown(self, reason: str) -> None:
        if self.shutdown is None or self.shutdown.complete:
            self.shutdown = ControlledShutdown(reason=reason)

    def hold(
        self,
        *,
        sequence_ack: int,
        reason: str,
        active_dtcs: tuple[str, ...] = (),
        clear_shutdown: bool = True,
    ) -> ActuatorCommand:
        """Emergency fail-OFF: no enable, ignition, gen force, or buffer authority."""
        if clear_shutdown:
            self.shutdown = None
        return ActuatorCommand(
            sequence_ack=sequence_ack,
            mode=ModeCode.OFF,
            slots=_idle_slots(self.slot_count),
            buffer_assist_w=0.0,
            buffer_burst_w=0.0,
            buffer_precharge_w=0.0,
            safe_state=True,
            watchdog_ok=False,
            latency_budget_ok=True,
            notes=f"emergency_off:{reason}",
            safe_mode=SafeMode.EMERGENCY_OFF,
            active_dtcs=active_dtcs,
        )
