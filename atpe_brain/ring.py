"""Ring-level context shared across ATPE Brain modules."""

from __future__ import annotations

from dataclasses import dataclass, field

from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState
from designs.phoenix_v3_mixed_ring import MixedCartridgeSlot
from designs.phoenix_v3_thermal import CoolantBus

from .cartridge import CartridgeProbe


@dataclass
class RingContext:
    """Mutable ring state for one supervisor cycle."""

    slots: tuple[MixedCartridgeSlot, ...]
    probes: tuple[CartridgeProbe, ...]
    states: tuple[CartridgeRuntimeState, ...]
    coolant: CoolantBus = field(default_factory=lambda: CoolantBus(temp_k=293.15))
    shared_coolant: bool = True

    @property
    def slot_count(self) -> int:
        return len(self.slots)

    @property
    def max_power_w(self) -> float:
        return sum(p.nominal_power_w for p in self.probes)

    def probe_by_index(self) -> dict[int, CartridgeProbe]:
        return {p.index: p for p in self.probes}

    def state_by_index(self) -> dict[int, CartridgeRuntimeState]:
        return {s.slot_index: s for s in self.states}
