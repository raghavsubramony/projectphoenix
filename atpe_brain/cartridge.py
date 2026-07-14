"""Cartridge probe and runtime snapshot types for the ATPE Brain."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CartridgeProbe:
    """Cached Gate-5 nominal operating point for one ring slot."""

    index: int
    tier_index: int
    tier_name: str
    nominal_power_w: float
    nominal_efficiency: float
    wall_temp_k: float
    generator_temp_k: float
    valve_temp_k: float
    generator_derate: float
    spring_recovery: float


@dataclass(frozen=True)
class CartridgeSnapshot:
    """Fused probe + live telemetry for one slot."""

    probe: CartridgeProbe
    health_pct: float
    fault_kind: str
    wall_temp_c: float
    generator_temp_c: float
    enabled: bool
    load_scale: float = 1.0

    @property
    def is_available(self) -> bool:
        from designs.phoenix_v3.cartridge_state import ISOLATING_FAULTS

        return self.fault_kind not in ISOLATING_FAULTS

    @property
    def effective_power_w(self) -> float:
        return (
            self.probe.nominal_power_w
            * self.load_scale
            * self.probe.generator_derate
        )

    @property
    def effective_efficiency(self) -> float:
        return max(0.10, self.probe.nominal_efficiency * self.probe.generator_derate)
