"""Per-cartridge health scoring across the ring."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.health_score import CartridgeHealthScore, compute_cartridge_health

from .cartridge import CartridgeProbe
from .ring import RingContext


@dataclass(frozen=True)
class RingHealthReport:
    """Health summary for all slots."""

    scores: tuple[CartridgeHealthScore, ...]
    mean_pct: float
    min_pct: float
    below_target: tuple[int, ...]
    isolate_candidates: tuple[int, ...]

    def score_for(self, slot_index: int) -> CartridgeHealthScore | None:
        for s in self.scores:
            if s.slot_index == slot_index:
                return s
        return None


class HealthMonitor:
    """Composite health from telemetry and spring recovery."""

    def __init__(
        self,
        *,
        target_pct: float = 95.0,
        isolate_threshold_pct: float = 80.0,
        deprioritize_threshold_pct: float = 90.0,
    ) -> None:
        self.target_pct = target_pct
        self.isolate_threshold_pct = isolate_threshold_pct
        self.deprioritize_threshold_pct = deprioritize_threshold_pct

    def evaluate(
        self,
        ring: RingContext,
        probes: dict[int, CartridgeProbe] | None = None,
    ) -> RingHealthReport:
        probe_map = probes or ring.probe_by_index()
        scores: list[CartridgeHealthScore] = []
        below: list[int] = []
        isolate: list[int] = []

        for st in ring.states:
            probe = probe_map[st.slot_index]
            score = compute_cartridge_health(
                st,
                spring_recovery=probe.spring_recovery,
            )
            scores.append(score)
            if score.score_pct < self.target_pct:
                below.append(st.slot_index)
            if score.score_pct < self.isolate_threshold_pct:
                isolate.append(st.slot_index)

        pcts = [s.score_pct for s in scores] if scores else [100.0]
        return RingHealthReport(
            scores=tuple(scores),
            mean_pct=sum(pcts) / len(pcts),
            min_pct=min(pcts),
            below_target=tuple(below),
            isolate_candidates=tuple(isolate),
        )
