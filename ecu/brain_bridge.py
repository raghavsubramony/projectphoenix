"""Translate ATPE BrainCommands into validated BrainSetpointFrame for the ECUs.

Safety rules:
  - Brain cannot invent slot indices outside 0..slot_count-1
  - Disabled slots get zero load
  - Unknown modes map to OFF
"""

from __future__ import annotations

from .bus import BrainSetpointFrame, CartridgeSetpoint, ModeCode
from .identity import ECU_SLOT_COUNT


_MODE_FROM_NAME: dict[str, ModeCode] = {
    "off": ModeCode.OFF,
    "idle": ModeCode.IDLE,
    "city": ModeCode.CITY,
    "highway": ModeCode.HIGHWAY,
    "overtake": ModeCode.OVERTAKE,
    "track": ModeCode.TRACK,
}


class BrainToEcuBridge:
    """Validated Brain → ECU setpoint packing."""

    def __init__(self, *, slot_count: int = ECU_SLOT_COUNT) -> None:
        self.slot_count = slot_count
        self._sequence = 0

    def from_brain_commands(self, commands: object) -> BrainSetpointFrame:
        """Accept ``atpe_brain.BrainCommands`` or a duck-typed equivalent."""
        mode_obj = getattr(commands, "mode", "off")
        mode_name = mode_obj.value if hasattr(mode_obj, "value") else str(mode_obj)
        mode = _MODE_FROM_NAME.get(mode_name.lower(), ModeCode.OFF)

        enabled = tuple(int(i) for i in getattr(commands, "enabled_indices", ()))
        scales = dict(getattr(commands, "load_scales", {}) or {})
        enabled_set = set(enabled)

        carts: list[CartridgeSetpoint] = []
        for idx in range(self.slot_count):
            on = idx in enabled_set
            scale = float(scales.get(idx, 1.0 if on else 0.0))
            if not on:
                scale = 0.0
            scale = max(0.0, min(1.2, scale))
            carts.append(
                CartridgeSetpoint(
                    slot_index=idx,
                    enabled=on,
                    load_scale=scale,
                    ignition_scale=1.0 if on else 0.0,
                    generator_force_scale=1.0 if on else 0.0,
                )
            )

        # Reject inventing indices beyond slot map (bridge strips them).
        self._sequence += 1
        return BrainSetpointFrame(
            sequence=self._sequence,
            mode=mode,
            demand_w=float(getattr(commands, "demand_w", 0.0)),
            target_power_w=float(getattr(commands, "target_power_w", 0.0)),
            cartridges=tuple(carts),
            buffer_assist_w=float(getattr(commands, "buffer_assist_w", 0.0) or 0.0),
            buffer_precharge_w=float(getattr(commands, "buffer_precharge_w", 0.0) or 0.0),
            buffer_burst_w=float(getattr(commands, "buffer_burst_w", 0.0) or 0.0),
            brain_alive=True,
        )
