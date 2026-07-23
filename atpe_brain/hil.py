"""HIL verification — ATPE-BRAIN-VV-001.

BrainCommands → vehicle ECU (``ecu.VehicleEcuRuntime`` or lag stub) → ack.
Safety rule: AI never bypasses ECU acknowledgment / safe-state hold.
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


@dataclass(frozen=True)
class HilStepResult:
    commands: BrainCommands
    ack: EcuAck
    pass_: bool
    reasons: tuple[str, ...]


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
            return EcuAck(
                applied_mode=self._mode,
                applied_indices=self._indices,
                applied_scales=dict(self._scales),
                latency_s=self.latency_s,
                watchdog_ok=False,
                notes="watchdog trip — holding last ECU setpoints",
                flash_id="STUB",
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
        from ecu import ModeCode

        if not self.watchdog_ok:
            self.runtime.watchdog.tripped = True
            self.runtime.watchdog.reason = "hil_injected"
        self.runtime.accept_brain(commands)
        result = None
        for _ in range(self.settle_ticks):
            result = self.runtime.tick()
        assert result is not None
        mode_map = {
            ModeCode.OFF: DispatchMode.OFF,
            ModeCode.IDLE: DispatchMode.IDLE,
            ModeCode.CITY: DispatchMode.CITY,
            ModeCode.HIGHWAY: DispatchMode.HIGHWAY,
            ModeCode.OVERTAKE: DispatchMode.OVERTAKE,
            ModeCode.TRACK: DispatchMode.TRACK,
        }
        indices = self.runtime.enabled_indices(result)
        scales = {
            s.slot_index: s.load_fraction
            for s in result.command.slots
            if s.enable
        }
        return EcuAck(
            applied_mode=mode_map.get(result.command.mode, DispatchMode.OFF),
            applied_indices=indices,
            applied_scales=scales,
            latency_s=result.elapsed_s,
            watchdog_ok=result.command.watchdog_ok,
            notes=result.command.notes,
            flash_id=result.flash_id,
        )


class HilHarness:
    """Runs BrainCommands through the ECU stub and scores VV criteria."""

    def __init__(self, ecu: DeterministicEcuStub | None = None) -> None:
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
        # ECU must not invent indices beyond brain command.
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
