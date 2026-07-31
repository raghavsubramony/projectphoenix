"""ATPE Brain supervisor — main control loop."""

from __future__ import annotations

from dataclasses import dataclass, field

from designs.phoenix_v3.cartridge_scheduler import DispatchMode
from designs.phoenix_v3.cartridge_state import CartridgeRuntimeState
from designs.phoenix_v3.health_score import apply_health_to_states
from designs.phoenix_v3_thermal import CoolantBus, advance_coolant_bus

from .fault_manager import FaultManager, FaultPlan
from .fleet_learning import FleetSample, FleetWeightAdapter
from .health_monitor import HealthMonitor, RingHealthReport
from .optimizer import BrainCommands, Optimizer, OptimizerWeights
from .pcmritms import BufferPlan, BufferTelemetry, PcmritmsCoordinator
from .physics_twin import PhysicsTwinBackend, ProbeTwinBackend
from .ring import RingContext
from .ring_balancer import RingBalancer
from .rotor_phasing import ClosedLoopRotorPhaser
from .scheduler import BrainScheduler
from .state_estimator import RingEstimate, StateEstimator
from .thermal_manager import ThermalManager, ThermalPlan


@dataclass(frozen=True)
class SupervisorConfig:
    """Supervisor loop parameters."""

    prediction_horizon_cycles: int = 50
    thermal_horizon_cycles: int = 500
    health_horizon_cycles: int = 5000
    enable_multi_horizon: bool = False
    twin_fidelity: str = "physics"  # "physics" | "probe"
    enable_rotor_phasing: bool = True
    enable_fleet_learning: bool = False
    fleet_mission_mode: str = "cruise"
    optimizer_weights: OptimizerWeights = field(default_factory=OptimizerWeights)
    health_isolate_threshold_pct: float = 80.0
    health_deprioritize_threshold_pct: float = 90.0


@dataclass(frozen=True)
class BrainCycleResult:
    """Full output of one supervisor iteration."""

    commands: BrainCommands
    estimate: RingEstimate
    prediction: TwinPrediction
    health: RingHealthReport
    fault_plan: FaultPlan
    thermal_plan: ThermalPlan
    buffer_plan: BufferPlan
    updated_states: tuple[CartridgeRuntimeState, ...]
    coolant: CoolantBus
    multi_horizon: object | None = None


class ATPESupervisor:
    """Orchestrate ATPE Brain modules each vehicle timestep."""

    def __init__(
        self,
        ring: RingContext,
        *,
        config: SupervisorConfig | None = None,
    ) -> None:
        self.ring = ring
        self.config = config or SupervisorConfig()
        self.state_estimator = StateEstimator()
        if self.config.twin_fidelity == "probe":
            self.digital_twin: ProbeTwinBackend | PhysicsTwinBackend = ProbeTwinBackend()
        else:
            self.digital_twin = PhysicsTwinBackend()
        self.pcmritms = PcmritmsCoordinator()
        self.rotor_phaser = ClosedLoopRotorPhaser()
        self.scheduler = BrainScheduler()
        self.ring_balancer = RingBalancer()
        self.thermal_manager = ThermalManager()
        self.health_monitor = HealthMonitor(
            isolate_threshold_pct=self.config.health_isolate_threshold_pct,
            deprioritize_threshold_pct=self.config.health_deprioritize_threshold_pct,
        )
        self.fault_manager = FaultManager(
            isolate_threshold_pct=self.config.health_isolate_threshold_pct,
        )
        self.optimizer = Optimizer(self.config.optimizer_weights)
        self.fleet_adapter = FleetWeightAdapter(self.config.optimizer_weights)
        self.last_multi_horizon = None
        self.last_rotor_coherence: float = 1.0

    def cycle(
        self,
        demand_w: float,
        dt_s: float,
        *,
        prev_mode: DispatchMode | None = None,
        bus_demand_w: float | None = None,
        buffer: BufferTelemetry | None = None,
        slow_setpoint_w: float | None = None,
    ) -> BrainCycleResult:
        bus_w = demand_w if bus_demand_w is None else bus_demand_w
        estimate = self.state_estimator.update(
            self.ring,
            demand_w=demand_w,
            bus_demand_w=bus_w,
            buffer_soc=None if buffer is None else buffer.soc,
        )
        states = self.state_estimator.states_from_estimate(estimate, self.ring.states)

        health = self.health_monitor.evaluate(self.ring)
        states = apply_health_to_states(states, health.scores)
        fault_plan = self.fault_manager.solve(states, health)
        states = fault_plan.updated_states

        surge_scale = 1.0
        if self.config.enable_rotor_phasing:
            rotor_state = self.rotor_phaser.step(dt_s)
            self.last_rotor_coherence = rotor_state.coherence
            surge_scale = rotor_state.surge_scale

        buffer_plan = self.pcmritms.solve(
            bus_demand_w=bus_w,
            engine_setpoint_w=demand_w,
            buffer=buffer,
            slow_setpoint_w=slow_setpoint_w,
            dt_s=dt_s,
            min_health_pct=health.min_pct,
            surge_scale=surge_scale,
        )
        schedule_demand_w = buffer_plan.engine_demand_w

        # Health-aware scheduling: when assist covers residual, soft-derate
        # marginal cartridges so buffer prefers to shield them.
        if buffer_plan.assist_event and health.below_target:
            from dataclasses import replace as _replace

            demoted: list = []
            for st in states:
                if st.slot_index in health.below_target and st.enabled:
                    demoted.append(
                        _replace(st, load_scale=min(st.load_scale, 0.70))
                    )
                else:
                    demoted.append(st)
            states = tuple(demoted)

        load_frac = min(1.0, schedule_demand_w / max(1.0, self.ring.max_power_w))
        prediction = self.digital_twin.predict(
            estimate,
            horizon_cycles=self.config.prediction_horizon_cycles,
            load_fraction=load_frac,
        )
        multi = None
        if self.config.enable_multi_horizon:
            multi = self.digital_twin.predict_multi_horizon(
                estimate,
                load_fraction=load_frac,
                control_cycles=self.config.prediction_horizon_cycles,
                thermal_cycles=self.config.thermal_horizon_cycles,
                health_cycles=self.config.health_horizon_cycles,
            )
            self.last_multi_horizon = multi

        sched = self.scheduler.solve(
            schedule_demand_w,
            self.ring.slots,
            states,
            prev_mode=prev_mode,
        )
        balance = self.ring_balancer.solve(prediction, self.ring)
        # Prefer thermal-horizon peak when multi-horizon is available.
        thermal_pred = prediction if multi is None else multi.thermal
        thermal = self.thermal_manager.solve(thermal_pred, self.ring, dt_s)

        commands = self.optimizer.generate(
            sched,
            balance,
            thermal,
            fault_plan,
            prediction,
            mean_health_pct=health.mean_pct,
            available_count=estimate.available_count,
            buffer_plan=buffer_plan,
        )

        if self.config.enable_fleet_learning:
            adapted = self.fleet_adapter.observe(
                FleetSample(
                    score=commands.score,
                    mode=self.config.fleet_mission_mode,
                )
            )
            self.optimizer.weights = adapted

        self._advance_coolant(commands, dt_s)
        updated = self._touch_operating_hours(states, dt_s)

        self.ring.states = updated
        return BrainCycleResult(
            commands=commands,
            estimate=estimate,
            prediction=prediction,
            health=health,
            fault_plan=fault_plan,
            thermal_plan=thermal,
            buffer_plan=buffer_plan,
            updated_states=updated,
            coolant=self.ring.coolant,
            multi_horizon=multi,
        )

    def _advance_coolant(self, commands: BrainCommands, dt_s: float) -> None:
        if not self.ring.shared_coolant or not commands.enabled_indices:
            return
        probe_map = self.ring.probe_by_index()
        # Approximate reject heat (W) at load scale; advance_coolant_bus
        # multiplies by duration_s itself — do not pre-scale by dt.
        _REJECT_HEAT_W_AT_NOMINAL = 8000.0
        heat_w = 0.0
        for idx in commands.enabled_indices:
            cache = probe_map[idx]
            scale = commands.load_scales.get(idx, 1.0)
            heat_w += (
                cache.nominal_power_w * scale / max(cache.nominal_power_w, 1.0)
            ) * _REJECT_HEAT_W_AT_NOMINAL
        if not self.ring.slots:
            return
        cfg0 = self.ring.slots[0].cfg
        ambient = cfg0.ambient_temp_k
        self.ring.coolant = advance_coolant_bus(
            self.ring.coolant,
            ambient_temp_k=ambient,
            coolant_thermal_mass_j_per_k=cfg0.coolant_thermal_mass_j_per_k,
            coolant_radiator_w_per_k=cfg0.coolant_radiator_w_per_k,
            heat_in_w=heat_w,
            duration_s=max(dt_s, 1e-6),
        )

    def _touch_operating_hours(
        self,
        states: tuple[CartridgeRuntimeState, ...],
        dt_s: float,
    ) -> tuple[CartridgeRuntimeState, ...]:
        from dataclasses import replace

        hours_delta = dt_s / 3600.0
        return tuple(
            replace(st, operating_hours=st.operating_hours + hours_delta)
            for st in states
        )
