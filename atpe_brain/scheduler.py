"""Demand-driven cartridge dispatch — wraps Gate-5 scheduler."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    DispatchMode,
    SchedulerConfig,
    SchedulerDecision,
    schedule_for_demand,
)
from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState
from designs.phoenix_v3_mixed_ring import MixedCartridgeSlot


@dataclass(frozen=True)
class SchedulerPlan:
    """Dispatch decision with mode and per-slot load scales."""

    decision: SchedulerDecision
    mode: DispatchMode
    enabled_indices: tuple[int, ...]
    load_scales: dict[int, float]
    target_power_w: float


class BrainScheduler:
    """Select active cartridge set for bus demand."""

    def __init__(self, config: SchedulerConfig | None = None) -> None:
        self.config = config or DEFAULT_SCHEDULER_CONFIG

    def solve(
        self,
        demand_w: float,
        slots: tuple[MixedCartridgeSlot, ...],
        states: tuple[CartridgeRuntimeState, ...],
        *,
        prev_mode: DispatchMode | None = None,
    ) -> SchedulerPlan:
        decision = schedule_for_demand(
            demand_w,
            slots,
            states,
            config=self.config,
            prev_mode=prev_mode,
        )
        return SchedulerPlan(
            decision=decision,
            mode=decision.mode,
            enabled_indices=decision.enabled_indices,
            load_scales=dict(decision.load_scales),
            target_power_w=decision.target_power_w,
        )
