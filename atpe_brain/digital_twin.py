"""Internal simulation — short-horizon prediction from cached probes."""

from __future__ import annotations

from dataclasses import dataclass

from .state_estimator import RingEstimate


@dataclass(frozen=True)
class TwinForecast:
    """Predicted KPI at one future cycle offset."""

    cycle_offset: int
    efficiency: float
    capture_fraction: float
    thermal_load_w: float
    fault_risk_pct: float
    nvh_proxy: float


@dataclass(frozen=True)
class TwinPrediction:
    """Horizon bundle returned by the digital twin."""

    horizon_cycles: int
    forecasts: tuple[TwinForecast, ...]
    mean_efficiency: float
    peak_thermal_w: float
    max_fault_risk_pct: float
    nvh_proxy: float = 0.0

    def at(self, cycle_offset: int) -> TwinForecast | None:
        for f in self.forecasts:
            if f.cycle_offset == cycle_offset:
                return f
        return None


class DigitalTwin:
    """Lightweight probe-based predictor (Gate-6 foundation for full physics twin)."""

    def __init__(self, *, cycle_time_s: float = 0.04) -> None:
        self._cycle_time_s = cycle_time_s

    def predict(
        self,
        estimate: RingEstimate,
        *,
        horizon_cycles: int = 50,
        load_fraction: float = 1.0,
        stride: int = 1,
    ) -> TwinPrediction:
        active = [s for s in estimate.snapshots if s.enabled and s.is_available]
        if not active:
            empty = TwinForecast(0, 0.0, 0.0, 0.0, 0.0, 0.0)
            return TwinPrediction(horizon_cycles, (empty,), 0.0, 0.0, 0.0, 0.0)

        base_eff = sum(s.effective_efficiency for s in active) / len(active)
        base_capture = 0.75
        base_heat = sum(s.effective_power_w * 0.35 for s in active)
        base_fault = max(0.0, 100.0 - estimate.mean_health_pct)
        base_nvh = len(active) * 35.0
        stride = max(1, stride)

        forecasts: list[TwinForecast] = []
        for step in range(0, horizon_cycles + 1, stride):
            t_s = step * self._cycle_time_s
            heat_ramp = 1.0 + 0.002 * t_s * load_fraction
            eff_decay = max(0.40, base_eff - 0.0005 * t_s * load_fraction)
            fault_rise = min(99.0, base_fault + 0.01 * t_s * load_fraction * len(active))
            forecasts.append(
                TwinForecast(
                    cycle_offset=step,
                    efficiency=eff_decay,
                    capture_fraction=max(0.5, base_capture - 0.0002 * t_s),
                    thermal_load_w=base_heat * heat_ramp,
                    fault_risk_pct=fault_rise,
                    nvh_proxy=base_nvh,
                )
            )
        if forecasts[-1].cycle_offset != horizon_cycles:
            t_s = horizon_cycles * self._cycle_time_s
            forecasts.append(
                TwinForecast(
                    cycle_offset=horizon_cycles,
                    efficiency=max(0.40, base_eff - 0.0005 * t_s * load_fraction),
                    capture_fraction=max(0.5, base_capture - 0.0002 * t_s),
                    thermal_load_w=base_heat * (1.0 + 0.002 * t_s * load_fraction),
                    fault_risk_pct=min(
                        99.0, base_fault + 0.01 * t_s * load_fraction * len(active)
                    ),
                    nvh_proxy=base_nvh,
                )
            )

        return TwinPrediction(
            horizon_cycles=horizon_cycles,
            forecasts=tuple(forecasts),
            mean_efficiency=sum(f.efficiency for f in forecasts) / len(forecasts),
            peak_thermal_w=max(f.thermal_load_w for f in forecasts),
            max_fault_risk_pct=max(f.fault_risk_pct for f in forecasts),
            nvh_proxy=base_nvh,
        )

    def predict_multi_horizon(
        self,
        estimate: RingEstimate,
        *,
        load_fraction: float = 1.0,
        control_cycles: int = 50,
        thermal_cycles: int = 500,
        health_cycles: int = 5000,
    ):
        """Gate-6 multi-horizon bundle (control / thermal / health)."""
        from .physics_twin import MultiHorizonPrediction

        return MultiHorizonPrediction(
            control=self.predict(
                estimate, horizon_cycles=control_cycles, load_fraction=load_fraction,
            ),
            thermal=self.predict(
                estimate,
                horizon_cycles=thermal_cycles,
                load_fraction=load_fraction,
                stride=max(1, thermal_cycles // 50),
            ),
            health=self.predict(
                estimate,
                horizon_cycles=health_cycles,
                load_fraction=load_fraction,
                stride=max(1, health_cycles // 50),
            ),
        )
