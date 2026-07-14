"""Ring balance — NVH and capture uniformity recommendations."""

from __future__ import annotations

from dataclasses import dataclass

from .digital_twin import TwinPrediction
from .ring import RingContext


@dataclass(frozen=True)
class BalancePlan:
    """Phase/load nudges to reduce RBI and peak force."""

    rbi_proxy_pct: float
    medium_capture_variance_pct: float
    phase_adjust: bool
    load_trim: dict[int, float]
    notes: str = ""


class RingBalancer:
    """Recommend balance corrections from twin forecast and ring state."""

    def __init__(
        self,
        *,
        capture_variance_threshold_pct: float = 3.0,
        rbi_target_pct: float = 1.0,
    ) -> None:
        self.capture_variance_threshold_pct = capture_variance_threshold_pct
        self.rbi_target_pct = rbi_target_pct

    def solve(
        self,
        prediction: TwinPrediction,
        ring: RingContext,
    ) -> BalancePlan:
        medium_caps: list[float] = []
        for st in ring.states:
            if st.tier_index == 1 and st.enabled:
                medium_caps.append(st.mean_capture_fraction)

        if len(medium_caps) >= 2:
            mean_cap = sum(medium_caps) / len(medium_caps)
            var_pct = (
                sum((c - mean_cap) ** 2 for c in medium_caps) / len(medium_caps)
            ) ** 0.5 * 100.0
        else:
            var_pct = 0.0

        active_count = sum(1 for s in ring.states if s.enabled and s.is_available)
        rbi_proxy = max(0.0, prediction.nvh_proxy / max(1, active_count) - 30.0)

        load_trim: dict[int, float] = {}
        if var_pct > self.capture_variance_threshold_pct:
            for st in ring.states:
                if st.tier_index == 1 and st.is_available:
                    if st.mean_capture_fraction > 0.78:
                        load_trim[st.slot_index] = 0.97
                    elif st.mean_capture_fraction < 0.72:
                        load_trim[st.slot_index] = 1.03

        phase_adjust = var_pct > self.capture_variance_threshold_pct
        notes = []
        if phase_adjust:
            notes.append("medium capture variance high")
        if rbi_proxy > self.rbi_target_pct:
            notes.append(f"RBI proxy {rbi_proxy:.1f}%")

        return BalancePlan(
            rbi_proxy_pct=rbi_proxy,
            medium_capture_variance_pct=var_pct,
            phase_adjust=phase_adjust,
            load_trim=load_trim,
            notes="; ".join(notes),
        )
