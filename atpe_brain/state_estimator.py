"""Fuse sensor telemetry and probe caches into a ring estimate."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState

from .cartridge import CartridgeProbe, CartridgeSnapshot
from .ring import RingContext


@dataclass(frozen=True)
class RingEstimate:
    """Filtered ring state at the start of a supervisor cycle."""

    demand_w: float
    snapshots: tuple[CartridgeSnapshot, ...]
    mean_health_pct: float
    available_count: int
    coolant_temp_k: float
    buffer_soc: float | None = None
    bus_demand_w: float = 0.0

    @property
    def mean_efficiency(self) -> float:
        active = [s for s in self.snapshots if s.enabled and s.is_available]
        if not active:
            return 0.0
        return sum(s.effective_efficiency for s in active) / len(active)


class StateEstimator:
    """Kalman-style placeholder — exponential blend of probe and live state."""

    def __init__(self, *, blend: float = 0.85) -> None:
        self._blend = blend

    def update(
        self,
        ring: RingContext,
        *,
        demand_w: float = 0.0,
        bus_demand_w: float | None = None,
        buffer_soc: float | None = None,
    ) -> RingEstimate:
        probe_map = ring.probe_by_index()
        snapshots: list[CartridgeSnapshot] = []
        available = 0
        health_sum = 0.0

        for st in ring.states:
            probe = probe_map[st.slot_index]
            wall_c = self._blend * (probe.wall_temp_k - 273.15) + (1.0 - self._blend) * st.wall_temp_c
            gen_c = (
                self._blend * (probe.generator_temp_k - 273.15)
                + (1.0 - self._blend) * st.generator_temp_c
            )
            snap = CartridgeSnapshot(
                probe=probe,
                health_pct=st.health_pct,
                fault_kind=st.fault_kind,
                wall_temp_c=wall_c,
                generator_temp_c=gen_c,
                enabled=st.enabled,
                load_scale=st.load_scale,
            )
            snapshots.append(snap)
            health_sum += st.health_pct
            if snap.is_available:
                available += 1

        n = max(1, len(ring.states))
        return RingEstimate(
            demand_w=demand_w,
            snapshots=tuple(snapshots),
            mean_health_pct=health_sum / n,
            available_count=available,
            coolant_temp_k=ring.coolant.temp_k,
            buffer_soc=buffer_soc,
            bus_demand_w=demand_w if bus_demand_w is None else bus_demand_w,
        )

    def states_from_estimate(
        self,
        estimate: RingEstimate,
        base_states: tuple[CartridgeRuntimeState, ...],
    ) -> tuple[CartridgeRuntimeState, ...]:
        """Rebuild runtime states aligned with the estimate."""
        from dataclasses import replace

        snap_map = {s.probe.index: s for s in estimate.snapshots}
        out: list[CartridgeRuntimeState] = []
        for st in base_states:
            snap = snap_map[st.slot_index]
            out.append(
                replace(
                    st,
                    wall_temp_c=snap.wall_temp_c,
                    generator_temp_c=snap.generator_temp_c,
                    health_pct=snap.health_pct,
                    fault_kind=snap.fault_kind,
                    load_scale=snap.load_scale,
                )
            )
        return tuple(out)
