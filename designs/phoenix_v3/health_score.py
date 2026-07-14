"""Per-cartridge health score for predictive maintenance."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState

HEALTH_TARGET_PCT = 95.0


@dataclass(frozen=True)
class CartridgeHealthScore:
    slot_index: int
    tier_index: int
    tier_name: str
    score_pct: float
    spring_recovery_pct: float
    boundary_penalty_pct: float
    derate_penalty_pct: float
    fault_penalty_pct: float
    wear_penalty_pct: float

    @property
    def meets_target(self) -> bool:
        return self.score_pct >= HEALTH_TARGET_PCT


def compute_cartridge_health(
    state: CartridgeRuntimeState,
    *,
    spring_recovery: float = 0.95,
    collision: bool = False,
) -> CartridgeHealthScore:
    """Composite 0–100 health from operating telemetry."""
    spring_pct = max(0.0, min(100.0, spring_recovery * 100.0))
    boundary_penalty = 0.0 if state.boundary_valid and not collision else 15.0
    derate_penalty = max(0.0, (1.0 - state.generator_derate) * 40.0)
    fault_penalty = 0.0
    if state.fault_kind == "generator_derate":
        fault_penalty = 8.0
    elif state.fault_kind in {"misfire", "stuck_intake_a", "load_double"}:
        fault_penalty = 5.0
    elif state.is_faulted:
        fault_penalty = 25.0
    wear_penalty = min(10.0, state.operating_hours / 500.0)
    raw = spring_pct - boundary_penalty - derate_penalty - fault_penalty - wear_penalty
    score = max(0.0, min(100.0, raw))
    return CartridgeHealthScore(
        slot_index=state.slot_index,
        tier_index=state.tier_index,
        tier_name=state.tier_name,
        score_pct=score,
        spring_recovery_pct=spring_pct,
        boundary_penalty_pct=boundary_penalty,
        derate_penalty_pct=derate_penalty,
        fault_penalty_pct=fault_penalty,
        wear_penalty_pct=wear_penalty,
    )


def apply_health_to_states(
    states: tuple[CartridgeRuntimeState, ...],
    scores: tuple[CartridgeHealthScore, ...],
) -> tuple[CartridgeRuntimeState, ...]:
    from dataclasses import replace

    by_index = {s.slot_index: s.score_pct for s in scores}
    return tuple(
        replace(st, health_pct=by_index.get(st.slot_index, st.health_pct))
        for st in states
    )
