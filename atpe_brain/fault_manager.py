"""Fault detection, isolation, and recovery."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_scheduler import inject_cartridge_fault
from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState

from .health_monitor import RingHealthReport


@dataclass(frozen=True)
class FaultAction:
    """One fault response."""

    slot_index: int
    action: str
    fault_kind: str


@dataclass(frozen=True)
class FaultPlan:
    """Isolation and redistribution plan."""

    actions: tuple[FaultAction, ...]
    updated_states: tuple[CartridgeRuntimeState, ...]
    maintain_demand: bool = True


class FaultManager:
    """Isolate degraded cartridges and preserve bus power."""

    def __init__(self, *, isolate_threshold_pct: float = 80.0) -> None:
        self.isolate_threshold_pct = isolate_threshold_pct

    def solve(
        self,
        states: tuple[CartridgeRuntimeState, ...],
        health: RingHealthReport,
    ) -> FaultPlan:
        actions: list[FaultAction] = []
        updated = states

        for idx in health.isolate_candidates:
            st = next(s for s in states if s.slot_index == idx)
            if st.is_faulted:
                continue
            updated = inject_cartridge_fault(
                updated, idx, "cartridge_isolation",
            )
            actions.append(
                FaultAction(idx, "isolate", "cartridge_isolation"),
            )

        for idx in health.below_target:
            if idx in health.isolate_candidates:
                continue
            st = next(s for s in states if s.slot_index == idx)
            if st.health_pct < 90.0 and not st.is_faulted:
                actions.append(
                    FaultAction(idx, "reduce_runtime", st.fault_kind or "none"),
                )

        return FaultPlan(
            actions=tuple(actions),
            updated_states=updated,
            maintain_demand=True,
        )
