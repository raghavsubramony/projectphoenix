"""Fleet-learning adaptation of optimizer weights (Gate-6).

Learns only ``OptimizerWeights``. Never mutates fault thresholds, thermal hard
limits, or ECU maps — those stay deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass

from .optimizer import OptimizationScore, OptimizerWeights


@dataclass(frozen=True)
class FleetSample:
    """One logged supervisor score observation."""

    score: OptimizationScore
    mode: str = "cruise"  # launch | cruise | endurance | failure | thermal


@dataclass
class FleetLearningState:
    """Running means of score components by mission mode."""

    n: int = 0
    sum_eff: float = 0.0
    sum_health: float = 0.0
    sum_thermal: float = 0.0
    sum_balance: float = 0.0
    sum_fault: float = 0.0


# Re-export for supervisor typing convenience.
__all__ = [
    "FleetLearningState",
    "FleetSample",
    "FleetWeightAdapter",
]


class FleetWeightAdapter:
    """Gradient-free weight nudge toward underperforming objectives."""

    def __init__(
        self,
        base: OptimizerWeights | None = None,
        *,
        learning_rate: float = 0.05,
        min_weight: float = 0.05,
    ) -> None:
        self.base = (base or OptimizerWeights()).normalized()
        self.learning_rate = learning_rate
        self.min_weight = min_weight
        self._by_mode: dict[str, FleetLearningState] = {}
        self.weights = self.base

    def observe(self, sample: FleetSample) -> OptimizerWeights:
        st = self._by_mode.setdefault(sample.mode, FleetLearningState())
        st.n += 1
        st.sum_eff += sample.score.efficiency
        st.sum_health += sample.score.health
        st.sum_thermal += sample.score.thermal_margin
        st.sum_balance += sample.score.balance
        st.sum_fault += sample.score.fault_tolerance
        self.weights = self._adapt(sample.mode)
        return self.weights

    def _adapt(self, mode: str) -> OptimizerWeights:
        st = self._by_mode[mode]
        if st.n < 3:
            return self.base
        means = {
            "efficiency": st.sum_eff / st.n,
            "health": st.sum_health / st.n,
            "thermal_margin": st.sum_thermal / st.n,
            "balance": st.sum_balance / st.n,
            "fault_tolerance": st.sum_fault / st.n,
        }
        # Boost weights for components below 0.7 mean.
        w = self.base
        lr = self.learning_rate
        eff = w.efficiency + lr * max(0.0, 0.7 - means["efficiency"])
        health = w.health + lr * max(0.0, 0.7 - means["health"])
        thermal = w.thermal_margin + lr * max(0.0, 0.7 - means["thermal_margin"])
        balance = w.balance + lr * max(0.0, 0.7 - means["balance"])
        fault = w.fault_tolerance + lr * max(0.0, 0.7 - means["fault_tolerance"])
        # Mission priors.
        if mode == "launch":
            balance += 0.02
            fault += 0.02
        elif mode == "thermal":
            thermal += 0.05
        elif mode == "failure":
            fault += 0.08
            health += 0.04
        elif mode == "endurance":
            efficiency = eff + 0.03
            health += 0.02
            eff = efficiency
        adapted = OptimizerWeights(
            efficiency=max(self.min_weight, eff),
            health=max(self.min_weight, health),
            thermal_margin=max(self.min_weight, thermal),
            balance=max(self.min_weight, balance),
            fault_tolerance=max(self.min_weight, fault),
        )
        return adapted.normalized()
