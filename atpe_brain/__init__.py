"""ATPE AI Brain — supervisory layer for Phoenix V3.1 mixed ring.

Orchestrates prediction, PCMRITMS coordination, scheduling, balancing, thermal
management, health monitoring, fault isolation, and multi-objective
optimization. Never drives actuators directly; emits setpoints for
deterministic ECUs.
"""

from .cartridge import CartridgeProbe, CartridgeSnapshot
from .digital_twin import DigitalTwin, TwinForecast, TwinPrediction
from .fault_manager import FaultAction, FaultManager, FaultPlan
from .fleet_learning import FleetSample, FleetWeightAdapter
from .health_monitor import HealthMonitor, RingHealthReport
from .hil import DeterministicEcuStub, HilHarness, HilStepResult, VehicleEcuAdapter
from .optimizer import BrainCommands, OptimizationScore, Optimizer, OptimizerWeights
from .pcmritms import BufferPlan, BufferTelemetry, PcmritmsCoordinator
from .physics_twin import MultiHorizonPrediction, PhysicsTwinBackend, ProbeTwinBackend
from .ring import RingContext
from .ring_balancer import BalancePlan, RingBalancer
from .rotor_phasing import ClosedLoopRotorPhaser, RotorPhaseState
from .scheduler import BrainScheduler, SchedulerPlan
from .state_estimator import RingEstimate, StateEstimator
from .supervisor import ATPESupervisor, BrainCycleResult, SupervisorConfig
from .thermal_manager import ThermalForecast, ThermalManager, ThermalPlan

__all__ = [
    "ATPESupervisor",
    "BalancePlan",
    "BrainCommands",
    "BrainCycleResult",
    "BrainScheduler",
    "BufferPlan",
    "BufferTelemetry",
    "CartridgeProbe",
    "CartridgeSnapshot",
    "ClosedLoopRotorPhaser",
    "DeterministicEcuStub",
    "DigitalTwin",
    "FaultAction",
    "FaultManager",
    "FaultPlan",
    "FleetSample",
    "FleetWeightAdapter",
    "HealthMonitor",
    "HilHarness",
    "HilStepResult",
    "MultiHorizonPrediction",
    "OptimizationScore",
    "Optimizer",
    "OptimizerWeights",
    "PcmritmsCoordinator",
    "PhysicsTwinBackend",
    "ProbeTwinBackend",
    "RingBalancer",
    "RingContext",
    "RingEstimate",
    "RingHealthReport",
    "RotorPhaseState",
    "SchedulerPlan",
    "StateEstimator",
    "SupervisorConfig",
    "ThermalForecast",
    "ThermalManager",
    "ThermalPlan",
    "TwinForecast",
    "TwinPrediction",
    "VehicleEcuAdapter",
]
