"""Gate-6 physics digital-twin backends.

ProbeTwinBackend keeps the Gate-5 cache extrapolation. PhysicsTwinBackend adds
lumped combustion/thermal/PCMRITMS-aware dynamics on top of probe baselines
(still not cycle-resolved CFD; that is the next fidelity step).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from .digital_twin import DigitalTwin, TwinForecast, TwinPrediction
from .state_estimator import RingEstimate


class TwinBackend(Protocol):
    """Predictive twin used inside ``ATPESupervisor``."""

    def predict(
        self,
        estimate: RingEstimate,
        *,
        horizon_cycles: int = 50,
        load_fraction: float = 1.0,
    ) -> TwinPrediction:
        ...


@dataclass(frozen=True)
class MultiHorizonPrediction:
    """Control / thermal / health forecast bundle (Gate-6 multi-horizon)."""

    control: TwinPrediction
    thermal: TwinPrediction
    health: TwinPrediction


class ProbeTwinBackend:
    """Existing probe-cache predictor (Gate-5 default)."""

    def __init__(self, *, cycle_time_s: float = 0.04) -> None:
        self._twin = DigitalTwin(cycle_time_s=cycle_time_s)
        self.fidelity = "probe"

    def predict(
        self,
        estimate: RingEstimate,
        *,
        horizon_cycles: int = 50,
        load_fraction: float = 1.0,
        stride: int = 1,
    ) -> TwinPrediction:
        return self._twin.predict(
            estimate,
            horizon_cycles=horizon_cycles,
            load_fraction=load_fraction,
            stride=stride,
        )

    def predict_multi_horizon(
        self,
        estimate: RingEstimate,
        *,
        load_fraction: float = 1.0,
        control_cycles: int = 50,
        thermal_cycles: int = 500,
        health_cycles: int = 5000,
    ) -> MultiHorizonPrediction:
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


class PhysicsTwinBackend:
    """Lumped physics predictor: thermal RC + health wear + rotor NVH coupling.

    Uses per-cartridge probe powers/temps as initial conditions, then integrates:
      - wall/generator thermal first-order response under load
      - efficiency derate with generator temperature
      - fault risk growth with thermal + low health
      - NVH proxy from active count + PCMRITMS rotor beat intensity
    """

    def __init__(
        self,
        *,
        cycle_time_s: float = 0.04,
        wall_tau_s: float = 45.0,
        gen_tau_s: float = 25.0,
        ambient_k: float = 293.15,
        rotor_nvh_gain: float = 12.0,
    ) -> None:
        self._cycle_time_s = cycle_time_s
        self.wall_tau_s = max(1.0, wall_tau_s)
        self.gen_tau_s = max(1.0, gen_tau_s)
        self.ambient_k = ambient_k
        self.rotor_nvh_gain = rotor_nvh_gain
        self.fidelity = "physics_lumped"

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

        load = max(0.0, min(1.5, load_fraction))
        n_active = len(active)
        base_eff = sum(s.effective_efficiency for s in active) / n_active
        base_power = sum(s.effective_power_w for s in active)
        wall0 = sum(s.wall_temp_c for s in active) / n_active + 273.15
        gen0 = sum(s.generator_temp_c for s in active) / n_active + 273.15
        health0 = estimate.mean_health_pct
        buffer_soc = estimate.buffer_soc if estimate.buffer_soc is not None else 0.7
        # Rotor beat intensity rises when buffer SoC is high and loaded.
        rotor_intensity = load * max(0.0, min(1.0, buffer_soc))

        stride = max(1, stride)
        wall = wall0
        gen = gen0
        forecasts: list[TwinForecast] = []

        for step in range(0, horizon_cycles + 1, stride):
            t_s = step * self._cycle_time_s
            # First-order thermal toward load-dependent targets.
            wall_tgt = self.ambient_k + (180.0 + 120.0 * load) * (base_power / max(n_active * 25_000.0, 1.0))
            gen_tgt = self.ambient_k + (90.0 + 80.0 * load) * (base_power / max(n_active * 25_000.0, 1.0))
            # Integrate for `stride` cycles in one step.
            dt = stride * self._cycle_time_s
            aw = 1.0 - math.exp(-dt / self.wall_tau_s)
            ag = 1.0 - math.exp(-dt / self.gen_tau_s)
            wall = wall + aw * (wall_tgt - wall)
            gen = gen + ag * (gen_tgt - gen)

            gen_derate = max(0.85, 1.0 - max(0.0, (gen - 373.15) / 200.0))
            eff = max(0.38, base_eff * gen_derate - 0.0003 * t_s * load)
            heat_w = base_power * load * (0.30 + 0.05 * (wall - self.ambient_k) / 200.0)
            fault = min(
                99.0,
                max(0.0, 100.0 - health0)
                + 0.015 * t_s * load * n_active
                + max(0.0, (gen - 420.0) * 0.05),
            )
            nvh = n_active * 30.0 + self.rotor_nvh_gain * rotor_intensity * 10.0
            forecasts.append(
                TwinForecast(
                    cycle_offset=step,
                    efficiency=eff,
                    capture_fraction=max(0.55, 0.78 - 0.00015 * t_s),
                    thermal_load_w=heat_w,
                    fault_risk_pct=fault,
                    nvh_proxy=nvh,
                )
            )

        if forecasts[-1].cycle_offset != horizon_cycles:
            # Ensure endpoint present for thermal manager.
            last = forecasts[-1]
            forecasts.append(
                TwinForecast(
                    cycle_offset=horizon_cycles,
                    efficiency=last.efficiency,
                    capture_fraction=last.capture_fraction,
                    thermal_load_w=last.thermal_load_w,
                    fault_risk_pct=last.fault_risk_pct,
                    nvh_proxy=last.nvh_proxy,
                )
            )

        return TwinPrediction(
            horizon_cycles=horizon_cycles,
            forecasts=tuple(forecasts),
            mean_efficiency=sum(f.efficiency for f in forecasts) / len(forecasts),
            peak_thermal_w=max(f.thermal_load_w for f in forecasts),
            max_fault_risk_pct=max(f.fault_risk_pct for f in forecasts),
            nvh_proxy=forecasts[0].nvh_proxy,
        )

    def predict_multi_horizon(
        self,
        estimate: RingEstimate,
        *,
        load_fraction: float = 1.0,
        control_cycles: int = 50,
        thermal_cycles: int = 500,
        health_cycles: int = 5000,
    ) -> MultiHorizonPrediction:
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
