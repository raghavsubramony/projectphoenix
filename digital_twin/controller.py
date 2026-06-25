"""Unified three-loop controller: mode, generation setpoint, buffer arbitration."""

from __future__ import annotations

from dataclasses import dataclass

from .config import ControlConfig


@dataclass
class ControlState:
    mode: str               # "EV" or "CS"
    gen_setpoint_w: float   # commanded ATPE electrical output
    filtered_demand_w: float
    # Optional per-step buffer discharge ceiling (W). None = use the buffer's
    # own static cap (no behavioral change). A closed-loop rotor controller sets
    # this to authorize the full surge only on genuine launches and otherwise
    # hold the buffer to its continuous rating, preserving the reservoir.
    buffer_burst_w: float | None = None


class UnifiedController:
    """Rule-based realization of the layered control law in docs/07 section 5."""

    def __init__(self, cfg: ControlConfig, battery_soc_target: float,
                 battery_soc_ev_floor: float) -> None:
        self.cfg = cfg
        self.soc_target = battery_soc_target
        self.soc_ev_floor = battery_soc_ev_floor
        self._filtered_demand_w = 0.0

    def _update_filter(self, demand_w: float, dt_s: float) -> float:
        # First-order low-pass; the slow loop must not chase transients.
        tau = self.cfg.generation_filter_tau_s
        alpha = dt_s / (tau + dt_s)
        positive = max(0.0, demand_w)
        self._filtered_demand_w += alpha * (positive - self._filtered_demand_w)
        return self._filtered_demand_w

    def decide(self, demand_w: float, battery_soc: float,
               max_generation_w: float, dt_s: float,
               speed_ms: float = 0.0, buffer_soc: float = 1.0) -> ControlState:
        filtered = self._update_filter(demand_w, dt_s)

        # Loop A: choose operating mode.
        can_ev = battery_soc > self.soc_ev_floor
        low_load = filtered < self.cfg.ev_demand_threshold_w
        # Fast-loop override: a hard instantaneous spike forces the engine on,
        # even from EV mode, so the buffers are not left to cover it alone.
        spike = demand_w > self.cfg.spike_threshold_w
        mode = "EV" if (can_ev and low_load and not spike) else "CS"

        if mode == "EV":
            return ControlState("EV", 0.0, filtered)

        # Loop B: generation setpoint = smoothed load + charge-sustaining term.
        soc_error = self.soc_target - battery_soc
        recharge_w = self.cfg.soc_correction_gain_w * soc_error
        setpoint = filtered + recharge_w
        if spike:
            # During a spike, floor the setpoint toward the instantaneous demand
            # so the engine assists the buffers instead of trailing the filter.
            setpoint = max(setpoint, min(demand_w, max_generation_w))
        setpoint = max(0.0, min(setpoint, max_generation_w))
        return ControlState("CS", setpoint, filtered)


class ClosedLoopRotorController:
    """Gates the inertial buffer's surge authority by demand context.

    Static rotor coupling leaves the full burst ceiling (``peak_transient_w``)
    permanently available, so the buffer spends its irreplaceable inertial
    reserve on *any* transient it is asked to cover - even ones the battery could
    have absorbed. This wrapper authorizes the full surge only when the deficit
    beyond generation genuinely exceeds what the *continuous* buffer rating plus
    the available battery assist can supply (and the reservoir is above a reserve
    floor). Otherwise it holds the buffer to its continuous rating and lets the
    battery absorb the overflow, conserving the inertial reservoir for the
    launches that actually need it.

    It delegates mode/setpoint decisions to an inner :class:`UnifiedController`,
    so charge-sustaining fuel economy is unchanged; only buffer surge timing
    differs. With ``buffer_burst_w`` left ``None`` the buffer behaves exactly as
    before, so this controller is a strict, opt-in superset.
    """

    def __init__(self, cfg, battery_soc_target: float,
                 battery_soc_ev_floor: float,
                 surge_ceiling_w: float, continuous_rating_w: float,
                 battery_assist_w: float = 0.0,
                 reserve_floor: float = 0.15) -> None:
        self.inner = UnifiedController(cfg, battery_soc_target,
                                       battery_soc_ev_floor)
        self.surge_ceiling_w = surge_ceiling_w
        self.continuous_rating_w = continuous_rating_w
        # How much steady power the battery can lend to cover a launch overflow.
        self.battery_assist_w = battery_assist_w
        self.reserve_floor = reserve_floor

    def decide(self, demand_w: float, battery_soc: float,
               max_generation_w: float, dt_s: float,
               speed_ms: float = 0.0, buffer_soc: float = 1.0) -> ControlState:
        state = self.inner.decide(demand_w, battery_soc, max_generation_w,
                                  dt_s, speed_ms=speed_ms, buffer_soc=buffer_soc)
        # Deficit the buffer would have to cover once generation does its part.
        deficit_w = demand_w - state.gen_setpoint_w
        # Spend the inertial surge only when continuous buffer + battery assist
        # cannot cover the deficit and there is reserve worth spending.
        battery_available = (self.battery_assist_w
                             if battery_soc > self.inner.soc_ev_floor else 0.0)
        needs_surge = deficit_w > self.continuous_rating_w + battery_available
        genuine_launch = needs_surge and buffer_soc > self.reserve_floor
        burst_w = (self.surge_ceiling_w if genuine_launch
                   else self.continuous_rating_w)
        state.buffer_burst_w = burst_w
        return state
