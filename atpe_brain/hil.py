"""HIL verification stub — ATPE-BRAIN-VV-001.

Simulates BrainCommands → deterministic ECU lag/noise → plant acknowledgment
without hardware. Safety rule: AI never bypasses ECU acknowledgment.
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


@dataclass(frozen=True)
class HilStepResult:
    commands: BrainCommands
    ack: EcuAck
    pass_: bool
    reasons: tuple[str, ...]


class DeterministicEcuStub:
    """First-order lag + integer cycle delay on enabled set."""

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
            )
        self._mode = commands.mode
        self._indices = commands.enabled_indices
        for idx in commands.enabled_indices:
            target = commands.load_scales.get(idx, 1.0)
            prev = self._scales.get(idx, target)
            self._scales[idx] = prev + self.scale_lag * (target - prev)
        # Drop scales for disabled slots.
        self._scales = {i: self._scales[i] for i in self._indices if i in self._scales}
        return EcuAck(
            applied_mode=self._mode,
            applied_indices=self._indices,
            applied_scales=dict(self._scales),
            latency_s=self.latency_s,
            watchdog_ok=True,
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
