"""Safe-state latch — holds last valid actuator outputs on watchdog trip."""

from __future__ import annotations

from dataclasses import dataclass

from .bus import ActuatorCommand, ModeCode, SlotActuatorOut


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


@dataclass
class SafeState:
    """Latches last-good command; degrade to OFF if none exists."""

    latched: ActuatorCommand | None = None
    slot_count: int = 12
    _default_notes: str = "safe_state_hold"

    def latch(self, cmd: ActuatorCommand) -> None:
        if not cmd.safe_state and cmd.watchdog_ok:
            self.latched = cmd

    def hold(self, *, sequence_ack: int, reason: str) -> ActuatorCommand:
        if self.latched is not None:
            prev = self.latched
            return ActuatorCommand(
                sequence_ack=sequence_ack,
                mode=prev.mode,
                slots=prev.slots,
                buffer_assist_w=0.0,
                buffer_burst_w=0.0,
                buffer_precharge_w=0.0,
                safe_state=True,
                watchdog_ok=False,
                latency_budget_ok=True,
                notes=f"{self._default_notes}:{reason}",
            )
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
            notes=f"safe_state_off:{reason}",
        )
