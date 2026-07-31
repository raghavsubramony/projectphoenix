"""Flash identity — proves which ECU software is running on the vehicle."""

from __future__ import annotations

from dataclasses import dataclass

# Bump when the vehicle-facing ECU contract changes.
ECU_FLASH_ID = "PHOENIX-V31-ECU-R3"
ECU_INTERFACE_VERSION = "1.0.0"
ECU_CYCLE_MS = 10
ECU_SLOT_COUNT = 12


@dataclass(frozen=True)
class EcuBuildManifest:
    """What is flashed / loaded on a vehicle ECU domain."""

    flash_id: str = ECU_FLASH_ID
    interface_version: str = ECU_INTERFACE_VERSION
    cycle_ms: int = ECU_CYCLE_MS
    slot_count: int = ECU_SLOT_COUNT
    domains: tuple[str, ...] = (
        "motion",
        "combustion",
        "generator",
        "buffer",
        "ring",
    )

    def fingerprint(self) -> str:
        domains = ",".join(self.domains)
        return (
            f"{self.flash_id}|v{self.interface_version}|"
            f"{self.cycle_ms}ms|{self.slot_count}slots|{domains}"
        )
