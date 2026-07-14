"""Per-cartridge runtime state for dynamic ring scheduling."""

from __future__ import annotations

from dataclasses import dataclass, replace

from designs.phoenix_v3_mixed_ring import MixedCartridgeResult, MixedCartridgeSlot

# Scheduler-visible fault kinds (cartridge isolated from dispatch when not recoverable).
CARTRIDGE_FAULT_KINDS: frozenset[str] = frozenset({
    "none",
    "misfire",
    "stuck_intake_a",
    "load_double",
    "injector_failure",
    "valve_failure",
    "generator_derate",
    "pressure_sensor_fault",
    "cartridge_isolation",
})

ISOLATING_FAULTS: frozenset[str] = frozenset({
    "injector_failure",
    "valve_failure",
    "cartridge_isolation",
    "pressure_sensor_fault",
})


@dataclass
class CartridgeRuntimeState:
    """Live state for one ring slot (thermal, health, faults)."""

    slot_index: int
    tier_index: int
    tier_name: str
    enabled: bool = True
    wall_temp_c: float = 178.0
    generator_temp_c: float = 120.0
    valve_temp_c: float = 165.0
    health_pct: float = 100.0
    fault_kind: str = "none"
    load_scale: float = 1.0
    generator_derate: float = 1.0
    mean_power_w: float = 0.0
    mean_capture_fraction: float = 0.0
    mean_efficiency: float = 0.0
    boundary_valid: bool = True
    operating_hours: float = 0.0

    @property
    def is_faulted(self) -> bool:
        return self.fault_kind in ISOLATING_FAULTS

    @property
    def is_available(self) -> bool:
        return not self.is_faulted and self.boundary_valid

    def thermal_headroom_c(self, *, setpoint_c: float = 200.0) -> float:
        return setpoint_c - self.wall_temp_c


def state_from_slot(slot: MixedCartridgeSlot) -> CartridgeRuntimeState:
    """Initial runtime state from a built ring slot."""
    return CartridgeRuntimeState(
        slot_index=slot.index,
        tier_index=slot.tier_index,
        tier_name=slot.tier_name,
        enabled=True,
    )


def initial_ring_states(
    slots: tuple[MixedCartridgeSlot, ...],
) -> tuple[CartridgeRuntimeState, ...]:
    return tuple(state_from_slot(s) for s in slots)


def update_state_from_result(
    state: CartridgeRuntimeState,
    result: MixedCartridgeResult,
    *,
    cycle_time_s: float,
    cycles: int,
    wall_temp_k: float | None = None,
    generator_temp_k: float | None = None,
    valve_temp_k: float | None = None,
    generator_derate: float | None = None,
) -> CartridgeRuntimeState:
    """Refresh harvest (and optional thermal) fields after a physics probe."""
    hours = state.operating_hours + cycles * cycle_time_s / 3600.0
    updates: dict = dict(
        mean_power_w=result.mean_elec_power_w,
        mean_capture_fraction=result.mean_capture_fraction,
        mean_efficiency=result.mean_net_efficiency,
        boundary_valid=result.boundary_valid,
        operating_hours=hours,
    )
    if wall_temp_k is not None:
        updates["wall_temp_c"] = wall_temp_k - 273.15
    if generator_temp_k is not None:
        updates["generator_temp_c"] = generator_temp_k - 273.15
    if valve_temp_k is not None:
        updates["valve_temp_c"] = valve_temp_k - 273.15
    if generator_derate is not None:
        updates["generator_derate"] = generator_derate
    return replace(state, **updates)


def update_state_from_telemetry(
    state: CartridgeRuntimeState,
    *,
    wall_temp_k: float,
    generator_temp_k: float,
    valve_temp_k: float,
    generator_derate: float,
    mean_power_w: float,
    mean_capture_fraction: float,
    mean_efficiency: float,
    boundary_valid: bool,
    cycle_time_s: float,
    cycles: int,
) -> CartridgeRuntimeState:
    """Refresh state from explicit thermal / harvest telemetry."""
    hours = state.operating_hours + cycles * cycle_time_s / 3600.0
    return replace(
        state,
        wall_temp_c=wall_temp_k - 273.15,
        generator_temp_c=generator_temp_k - 273.15,
        valve_temp_c=valve_temp_k - 273.15,
        generator_derate=generator_derate,
        mean_power_w=mean_power_w,
        mean_capture_fraction=mean_capture_fraction,
        mean_efficiency=mean_efficiency,
        boundary_valid=boundary_valid,
        operating_hours=hours,
    )
