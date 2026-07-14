"""Predictive thermal management — forecast before derate."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_scheduler import (
    DEFAULT_SCHEDULER_CONFIG,
    SchedulerConfig,
    thermal_rotation_adjustment,
)

from .digital_twin import TwinPrediction
from .ring import RingContext


@dataclass(frozen=True)
class ThermalForecast:
    """Per-slot temperature outlook."""

    slot_index: int
    wall_temp_c_now: float
    wall_temp_c_future: float
    generator_temp_c_future: float
    over_limit: bool


@dataclass(frozen=True)
class ThermalPlan:
    """Load reductions and rotation for thermal headroom."""

    forecasts: tuple[ThermalForecast, ...]
    load_adjustments: dict[int, float]
    activate_spare: bool
    notes: str = ""


class ThermalManager:
    """60-second lookahead thermal zoning (scaled to supervisor dt)."""

    def __init__(
        self,
        config: SchedulerConfig | None = None,
        *,
        wall_limit_c: float = 200.0,
        lookahead_s: float = 60.0,
    ) -> None:
        self.config = config or DEFAULT_SCHEDULER_CONFIG
        self.wall_limit_c = wall_limit_c
        self.lookahead_s = lookahead_s

    def solve(
        self,
        prediction: TwinPrediction,
        ring: RingContext,
        dt_s: float,
    ) -> ThermalPlan:
        horizon_s = min(self.lookahead_s, prediction.horizon_cycles * 0.04)
        heat_factor = prediction.peak_thermal_w / max(1.0, prediction.peak_thermal_w)

        forecasts: list[ThermalForecast] = []
        over_any = False
        for st in ring.states:
            if not st.enabled:
                continue
            rise_c = (prediction.peak_thermal_w / 10_000.0) * (horizon_s / 60.0)
            future_wall = st.wall_temp_c + rise_c * heat_factor
            future_gen = st.generator_temp_c + rise_c * 0.6
            over = future_wall > self.wall_limit_c
            if over:
                over_any = True
            forecasts.append(
                ThermalForecast(
                    slot_index=st.slot_index,
                    wall_temp_c_now=st.wall_temp_c,
                    wall_temp_c_future=future_wall,
                    generator_temp_c_future=future_gen,
                    over_limit=over,
                )
            )

        adjustments = dict(thermal_rotation_adjustment(ring.states, self.config))
        for fc in forecasts:
            if fc.over_limit:
                prev = adjustments.get(fc.slot_index, 1.0)
                adjustments[fc.slot_index] = max(0.5, prev * 0.85)

        notes = "thermal headroom low" if over_any else ""
        return ThermalPlan(
            forecasts=tuple(forecasts),
            load_adjustments=adjustments,
            activate_spare=over_any,
            notes=notes,
        )
