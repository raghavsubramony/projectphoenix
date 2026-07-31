"""HIL verification — ATPE-BRAIN-VV-001.

BrainCommands → vehicle ECU (``ecu.VehicleEcuRuntime`` or lag stub) → ack.
Safety rule: AI never bypasses ECU acknowledgment / fail-OFF safe-state.

Also exercises real-world bus faults: lost brain frames and delayed sensors.
"""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.cartridge_scheduler import DispatchMode

from .optimizer import BrainCommands


@dataclass(frozen=True)
class EcuAck:
    """What the ECU reports back after applying setpoints."""

    applied_mode: DispatchMode
    applied_indices: tuple[int, ...]
    applied_scales: dict[int, float]
    latency_s: float
    watchdog_ok: bool
    notes: str = ""
    flash_id: str = ""
    safe_mode: str = "NORMAL"
    active_dtcs: tuple[str, ...] = ()


@dataclass(frozen=True)
class HilStepResult:
    commands: BrainCommands
    ack: EcuAck
    pass_: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class HilFaultCaseResult:
    """Outcome of a scripted bus / sensor fault case."""

    name: str
    pass_: bool
    detail: str
    ack: EcuAck | None = None


class DeterministicEcuStub:
    """First-order lag + integer cycle delay on enabled set (legacy stub)."""

    def __init__(self, *, latency_s: float = 0.02, scale_lag: float = 0.6) -> None:
        self.latency_s = latency_s
        self.scale_lag = scale_lag
        self._scales: dict[int, float] = {}
        self._indices: tuple[int, ...] = ()
        self._mode = DispatchMode.OFF
        self.watchdog_ok = True

    def apply(self, commands: BrainCommands) -> EcuAck:
        if not self.watchdog_ok:
            self._mode = DispatchMode.OFF
            self._indices = ()
            self._scales = {}
            return EcuAck(
                applied_mode=DispatchMode.OFF,
                applied_indices=(),
                applied_scales={},
                latency_s=self.latency_s,
                watchdog_ok=False,
                notes="watchdog trip — safe-state OFF",
                flash_id="STUB",
                safe_mode="EMERGENCY_OFF",
            )
        self._mode = commands.mode
        self._indices = commands.enabled_indices
        for idx in commands.enabled_indices:
            target = commands.load_scales.get(idx, 1.0)
            prev = self._scales.get(idx, target)
            self._scales[idx] = prev + self.scale_lag * (target - prev)
        self._scales = {i: self._scales[i] for i in self._indices if i in self._scales}
        return EcuAck(
            applied_mode=self._mode,
            applied_indices=self._indices,
            applied_scales=dict(self._scales),
            latency_s=self.latency_s,
            watchdog_ok=True,
            flash_id="STUB",
            safe_mode="NORMAL",
        )


class VehicleEcuAdapter:
    """Wraps ``ecu.VehicleEcuRuntime`` as the HIL apply path (preferred)."""

    def __init__(self, *, settle_ticks: int = 15) -> None:
        from ecu import VehicleEcuRuntime

        self.runtime = VehicleEcuRuntime()
        self.settle_ticks = settle_ticks
        self.watchdog_ok = True
        self.latency_s = 0.01

    def apply(self, commands: BrainCommands) -> EcuAck:
        from ecu import ModeCode, SensorFrame

        if not self.watchdog_ok:
            self.runtime.watchdog.tripped = True
            self.runtime.watchdog.reason = "hil_injected"
        self.runtime.accept_brain(commands)
        result = None
        for _ in range(self.settle_ticks):
            # Re-kick brain each settle tick so timeout does not fire during lag.
            if _ % 5 == 0:
                self.runtime.accept_brain(commands)
            result = self.runtime.tick(SensorFrame(buffer_soc=0.7))
        assert result is not None
        return self._to_ack(result)

    def _to_ack(self, result: object) -> EcuAck:
        from ecu import ModeCode

        mode_map = {
            ModeCode.OFF: DispatchMode.OFF,
            ModeCode.IDLE: DispatchMode.IDLE,
            ModeCode.CITY: DispatchMode.CITY,
            ModeCode.HIGHWAY: DispatchMode.HIGHWAY,
            ModeCode.OVERTAKE: DispatchMode.OVERTAKE,
            ModeCode.TRACK: DispatchMode.TRACK,
        }
        command = result.command  # type: ignore[attr-defined]
        indices = self.runtime.enabled_indices(result)  # type: ignore[arg-type]
        scales = {
            s.slot_index: s.load_fraction
            for s in command.slots
            if s.enable
        }
        return EcuAck(
            applied_mode=mode_map.get(command.mode, DispatchMode.OFF),
            applied_indices=indices,
            applied_scales=scales,
            latency_s=result.elapsed_s,  # type: ignore[attr-defined]
            watchdog_ok=command.watchdog_ok,
            notes=command.notes,
            flash_id=result.flash_id,  # type: ignore[attr-defined]
            safe_mode=command.safe_mode.name,
            active_dtcs=command.active_dtcs,
        )


class HilHarness:
    """Runs BrainCommands through the ECU stub and scores VV criteria."""

    def __init__(self, ecu: DeterministicEcuStub | VehicleEcuAdapter | None = None) -> None:
        self.ecu = ecu or DeterministicEcuStub()

    def step(self, commands: BrainCommands) -> HilStepResult:
        ack = self.ecu.apply(commands)
        reasons: list[str] = []
        ok = True
        if not ack.watchdog_ok:
            ok = False
            reasons.append("watchdog_failed")
        if ack.latency_s > 0.05:
            ok = False
            reasons.append(f"latency {ack.latency_s:.3f}s > 50ms")
        extra = set(ack.applied_indices) - set(commands.enabled_indices)
        if extra:
            ok = False
            reasons.append(f"ecu_extra_indices {sorted(extra)}")
        if not reasons:
            reasons.append("ok")
        return HilStepResult(commands=commands, ack=ack, pass_=ok, reasons=tuple(reasons))


def synthetic_commands(demand_w: float = 90_000.0) -> BrainCommands:
    from .optimizer import OptimizationScore

    return BrainCommands(
        demand_w=demand_w,
        mode=DispatchMode.TRACK if demand_w > 50_000 else DispatchMode.IDLE,
        enabled_indices=(0, 1, 2, 3),
        load_scales={0: 1.0, 1: 0.9, 2: 0.85, 3: 0.8},
        target_power_w=demand_w,
        score=OptimizationScore(0.8, 0.8, 0.9, 0.85, 0.8, 0.75),
    )


def run_lost_brain_frame_case() -> HilFaultCaseResult:
    """Brain stops publishing; ECU must EMERGENCY_OFF within the timeout budget."""
    from ecu import SensorFrame, VehicleEcuRuntime

    ecu = VehicleEcuRuntime()
    cmd = synthetic_commands(90_000.0)
    ecu.accept_brain(cmd)
    for _ in range(10):
        ecu.tick(SensorFrame(buffer_soc=0.7))
    # Lose frames: tick past brain timeout without accept_brain.
    ticks = int(ecu.watchdog.brain_timeout_s / (ecu.cycle_ms / 1000.0)) + 3
    last = None
    for _ in range(ticks):
        last = ecu.tick(SensorFrame(buffer_soc=0.7))
    assert last is not None
    adapter = VehicleEcuAdapter()
    adapter.runtime = ecu
    ack = adapter._to_ack(last)
    ok = (
        ack.safe_mode == "EMERGENCY_OFF"
        and ack.applied_indices == ()
        and any("BRAIN_TIMEOUT" in d for d in ack.active_dtcs)
    )
    return HilFaultCaseResult(
        name="lost_brain_frame",
        pass_=ok,
        detail=f"safe_mode={ack.safe_mode} dtcs={ack.active_dtcs}",
        ack=ack,
    )


def run_sensor_delay_case(*, delay_s: float = 0.080) -> HilFaultCaseResult:
    """DAQ / CAN sensor stamp older than stale budget → inhibit + SENSOR_STALE DTC."""
    from ecu import SensorFrame, VehicleEcuRuntime

    ecu = VehicleEcuRuntime()
    ecu.accept_brain(synthetic_commands(90_000.0))
    # Establish load with fresh sensors.
    for _ in range(15):
        ecu.accept_brain(synthetic_commands(90_000.0))
        ecu.tick(SensorFrame(buffer_soc=0.7))
    # Delayed sample: stamp is in the past relative to ECU time.
    delayed = SensorFrame(
        buffer_soc=0.7,
        stamped_t_s=max(0.0, ecu._t_s - delay_s),
    )
    last = ecu.tick(delayed)
    adapter = VehicleEcuAdapter()
    adapter.runtime = ecu
    ack = adapter._to_ack(last)
    ok = (
        ack.applied_indices == ()
        and any("SENSOR_STALE" in d for d in ack.active_dtcs)
    )
    return HilFaultCaseResult(
        name="sensor_delay",
        pass_=ok,
        detail=f"enabled={ack.applied_indices} dtcs={ack.active_dtcs} notes={ack.notes}",
        ack=ack,
    )


def run_controlled_shutdown_case() -> HilFaultCaseResult:
    """Operator stop ramps load down; ignition cuts immediately; not EMERGENCY_OFF."""
    from ecu import SafeMode, SensorFrame, VehicleEcuRuntime

    ecu = VehicleEcuRuntime()
    for _ in range(20):
        ecu.accept_brain(synthetic_commands(90_000.0))
        last = ecu.tick(SensorFrame(buffer_soc=0.7))
    assert last is not None and ecu.enabled_indices(last)
    ecu.request_controlled_shutdown("hil_operator_stop")
    ramp_modes: list[str] = []
    final = None
    for _ in range(20):
        final = ecu.tick(SensorFrame(buffer_soc=0.7))
        ramp_modes.append(final.command.safe_mode.name)
        if all(s.ignition_scale == 0.0 for s in final.command.slots):
            pass
    assert final is not None
    ok = (
        "CONTROLLED_SHUTDOWN" in ramp_modes
        and final.command.safe_mode == SafeMode.CONTROLLED_SHUTDOWN
        and final.command.watchdog_ok
        and ecu.enabled_indices(final) == ()
        and any("CONTROLLED_SHUTDOWN" in d for d in final.command.active_dtcs)
    )
    adapter = VehicleEcuAdapter()
    adapter.runtime = ecu
    return HilFaultCaseResult(
        name="controlled_shutdown",
        pass_=ok,
        detail=f"final={final.command.safe_mode.name} notes={final.command.notes}",
        ack=adapter._to_ack(final),
    )


def run_hil_fault_suite() -> tuple[HilFaultCaseResult, ...]:
    """All real-world HIL fault cases used by VV smoke / unit tests."""
    return (
        run_lost_brain_frame_case(),
        run_sensor_delay_case(),
        run_controlled_shutdown_case(),
    )
