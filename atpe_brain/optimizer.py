"""Multi-objective command generation."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_scheduler import DispatchMode

from .digital_twin import TwinPrediction
from .fault_manager import FaultPlan
from .pcmritms import BufferPlan
from .ring_balancer import BalancePlan
from .scheduler import SchedulerPlan
from .thermal_manager import ThermalPlan


@dataclass(frozen=True)
class OptimizerWeights:
    """Gate-6 unified objective weights."""

    efficiency: float = 0.40
    health: float = 0.20
    thermal_margin: float = 0.20
    balance: float = 0.10
    fault_tolerance: float = 0.10

    def normalized(self) -> OptimizerWeights:
        total = (
            self.efficiency
            + self.health
            + self.thermal_margin
            + self.balance
            + self.fault_tolerance
        )
        if total <= 0.0:
            return OptimizerWeights()
        return OptimizerWeights(
            efficiency=self.efficiency / total,
            health=self.health / total,
            thermal_margin=self.thermal_margin / total,
            balance=self.balance / total,
            fault_tolerance=self.fault_tolerance / total,
        )


@dataclass(frozen=True)
class OptimizationScore:
    """Weighted supervisor quality metric (0..1)."""

    total: float
    efficiency: float
    health: float
    thermal_margin: float
    balance: float
    fault_tolerance: float


@dataclass(frozen=True)
class BrainCommands:
    """ECU setpoints emitted by the optimizer."""

    demand_w: float
    mode: DispatchMode
    enabled_indices: tuple[int, ...]
    load_scales: dict[int, float]
    target_power_w: float
    score: OptimizationScore
    notes: str = ""
    buffer_assist_w: float = 0.0
    buffer_precharge_w: float = 0.0
    buffer_burst_w: float | None = None


class Optimizer:
    """Fuse module plans into final cartridge dispatch commands."""

    def __init__(self, weights: OptimizerWeights | None = None) -> None:
        self.weights = (weights or OptimizerWeights()).normalized()

    def score(
        self,
        prediction: TwinPrediction,
        *,
        mean_health_pct: float,
        thermal_plan: ThermalPlan,
        balance_plan: BalancePlan,
        fault_plan: FaultPlan,
        available_count: int,
        active_count: int,
    ) -> OptimizationScore:
        w = self.weights
        eff = min(1.0, prediction.mean_efficiency / 0.55)
        health = mean_health_pct / 100.0
        thermal_margin = 1.0 if not thermal_plan.activate_spare else 0.6
        balance = max(0.0, 1.0 - balance_plan.rbi_proxy_pct / 10.0)
        fault_tol = min(1.0, available_count / max(1, active_count))

        total = (
            w.efficiency * eff
            + w.health * health
            + w.thermal_margin * thermal_margin
            + w.balance * balance
            + w.fault_tolerance * fault_tol
        )
        return OptimizationScore(
            total=total,
            efficiency=eff,
            health=health,
            thermal_margin=thermal_margin,
            balance=balance,
            fault_tolerance=fault_tol,
        )

    def generate(
        self,
        scheduler: SchedulerPlan,
        balance: BalancePlan,
        thermal: ThermalPlan,
        fault: FaultPlan,
        prediction: TwinPrediction,
        *,
        mean_health_pct: float,
        available_count: int,
        buffer_plan: BufferPlan | None = None,
    ) -> BrainCommands:
        load_scales = dict(scheduler.load_scales)
        for idx, scale in balance.load_trim.items():
            if idx in load_scales:
                load_scales[idx] = load_scales[idx] * scale
            elif idx in scheduler.enabled_indices:
                load_scales[idx] = scale

        for idx, adj in thermal.load_adjustments.items():
            if idx in load_scales:
                load_scales[idx] = load_scales[idx] * adj
            elif idx in scheduler.enabled_indices:
                load_scales[idx] = adj

        score = self.score(
            prediction,
            mean_health_pct=mean_health_pct,
            thermal_plan=thermal,
            balance_plan=balance,
            fault_plan=fault,
            available_count=available_count,
            active_count=len(scheduler.enabled_indices),
        )

        buf = buffer_plan
        notes = "; ".join(
            n
            for n in (
                scheduler.decision.notes,
                balance.notes,
                thermal.notes,
                buf.notes if buf is not None else "",
            )
            if n
        )
        return BrainCommands(
            demand_w=scheduler.decision.demand_w,
            mode=scheduler.mode,
            enabled_indices=scheduler.enabled_indices,
            load_scales=load_scales,
            target_power_w=scheduler.target_power_w,
            score=score,
            notes=notes,
            buffer_assist_w=buf.buffer_assist_w if buf is not None else 0.0,
            buffer_precharge_w=buf.precharge_w if buf is not None else 0.0,
            buffer_burst_w=buf.buffer_burst_w if buf is not None else None,
        )
