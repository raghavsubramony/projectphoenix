"""Closed-loop per-rotor PCMRITMS phasing (Gate-6).

Uses the whitepaper lumped rotor model to estimate phase coherence and apply a
small proportional phase correction toward ideal 120° spacing, yielding a
coherence factor that can derate surge ceiling when rotors drift.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from digital_twin.pcmritms_rotor import RotorSet


@dataclass(frozen=True)
class RotorPhaseState:
    """Live rotor programme + coherence."""

    rotors: RotorSet
    coherence: float  # 0..1 (1 = ideal spaced phases)
    surge_scale: float  # multiply peak_transient / burst

    @property
    def phases_rad(self) -> tuple[float, ...]:
        return self.rotors.phases_rad


@dataclass(frozen=True)
class RotorPhaseCommand:
    """Incremental phase corrections (rad) per rotor."""

    phase_deltas_rad: tuple[float, ...]
    notes: str = ""


class ClosedLoopRotorPhaser:
    """P-control toward equally spaced phases on the circle."""

    def __init__(
        self,
        *,
        kp: float = 0.35,
        max_step_rad: float = 0.15,
        coherence_floor: float = 0.55,
    ) -> None:
        self.kp = kp
        self.max_step_rad = max_step_rad
        self.coherence_floor = coherence_floor
        self._rotors = RotorSet()
        self._time_s = 0.0

    @property
    def rotors(self) -> RotorSet:
        return self._rotors

    def coherence(self, rotors: RotorSet | None = None) -> float:
        """1.0 when phases are equally spaced on [0, 2π)."""
        r = rotors or self._rotors
        n = r.n_rotors
        if n < 2:
            return 1.0
        target = tuple(i * 2.0 * math.pi / n for i in range(n))
        # Compare sorted phase gaps to ideal equal gaps.
        phases = sorted(p % (2.0 * math.pi) for p in r.phases_rad)
        gaps = [
            (phases[(i + 1) % n] - phases[i]) % (2.0 * math.pi)
            for i in range(n)
        ]
        ideal = 2.0 * math.pi / n
        err = sum(abs(g - ideal) for g in gaps) / n
        # Also reward proximity to a rigid target constellation (rot-invariant).
        # Use min over cyclic shifts of rms phase error.
        best = float("inf")
        for shift in range(n):
            shifted = tuple(target[(i + shift) % n] for i in range(n))
            # Align mean
            rms = math.sqrt(
                sum(
                    (_angle_diff(phases[i], shifted[i])) ** 2
                    for i in range(n)
                )
                / n
            )
            best = min(best, rms)
        score_gap = max(0.0, 1.0 - err / ideal)
        score_align = max(0.0, 1.0 - best / (math.pi / n))
        return max(0.0, min(1.0, 0.5 * score_gap + 0.5 * score_align))

    def step(self, dt_s: float) -> RotorPhaseState:
        """Advance time, correct phases, return state + surge scale."""
        self._time_s += max(0.0, dt_s)
        n = self._rotors.n_rotors
        ideal = tuple(i * 2.0 * math.pi / n for i in range(n))
        # Find best cyclic alignment of ideal to current.
        phases = list(self._rotors.phases_rad)
        best_shift = 0
        best_err = float("inf")
        for shift in range(n):
            err = sum(
                abs(_angle_diff(phases[i], ideal[(i + shift) % n]))
                for i in range(n)
            )
            if err < best_err:
                best_err = err
                best_shift = shift
        deltas: list[float] = []
        new_phases: list[float] = []
        for i in range(n):
            tgt = ideal[(i + best_shift) % n]
            e = _angle_diff(tgt, phases[i])
            d = max(-self.max_step_rad, min(self.max_step_rad, self.kp * e))
            deltas.append(d)
            new_phases.append((phases[i] + d) % (2.0 * math.pi))
        self._rotors = replace(self._rotors, phases_rad=tuple(new_phases))
        coh = self.coherence(self._rotors)
        # Soft-knee derate below floor.
        if coh >= self.coherence_floor:
            scale = 1.0
        else:
            scale = max(0.40, coh / max(self.coherence_floor, 1e-6))
        return RotorPhaseState(
            rotors=self._rotors,
            coherence=coh,
            surge_scale=scale,
        )

    def command(self) -> RotorPhaseCommand:
        state = self.step(0.0)
        # Zero deltas — last step already applied; expose residual intent.
        return RotorPhaseCommand(
            phase_deltas_rad=tuple(0.0 for _ in state.phases_rad),
            notes=f"coherence {state.coherence:.3f} surge_scale {state.surge_scale:.3f}",
        )


def _angle_diff(a: float, b: float) -> float:
    """Shortest signed difference a-b in (-π, π]."""
    d = (a - b + math.pi) % (2.0 * math.pi) - math.pi
    return d
