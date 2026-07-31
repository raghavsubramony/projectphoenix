"""ECU watchdog — deadline and brain-alive supervision."""

from __future__ import annotations

from dataclasses import dataclass

from .limits import ECU_BRAIN_TIMEOUT_S


@dataclass
class Watchdog:
    """Trip when cycle overruns or brain setpoints go stale.

    Brain liveness is based on time since the last ``kick_brain`` (accept),
    not on repeating the last frame's ``brain_alive`` flag each tick.
    """

    cycle_budget_s: float = 0.010
    brain_timeout_s: float = ECU_BRAIN_TIMEOUT_S
    _last_kick_s: float = 0.0
    _last_brain_s: float = 0.0
    tripped: bool = False
    reason: str = ""

    def kick_cycle(self, t_s: float, cycle_elapsed_s: float) -> None:
        self._last_kick_s = t_s
        if cycle_elapsed_s > self.cycle_budget_s:
            self.tripped = True
            self.reason = f"cycle_overrun {cycle_elapsed_s * 1e3:.1f}ms"

    def kick_brain(self, t_s: float) -> None:
        self._last_brain_s = t_s

    def check_brain_alive(self, t_s: float, brain_alive: bool) -> None:
        if not brain_alive:
            self.tripped = True
            self.reason = "brain_not_alive"
            return
        if (t_s - self._last_brain_s) > self.brain_timeout_s:
            self.tripped = True
            self.reason = "brain_timeout"

    def reset(self) -> None:
        self.tripped = False
        self.reason = ""
