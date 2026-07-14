"""PCMRITMS coordination — benefit-seeking buffer / ATPE demand shaping.

Matches docs/04 two-loop hierarchy, Gate-6 calibrated toward net fuel benefit:

  Assist only when bus residual exceeds a spike threshold (not always).
  Pre-charge only after a spike (recovery window) or at emergency SoC;
  never opportunistic on tow/cruise with no transient history.
  After assist/surge, suppress precharge for a cooldown window (anti energy-cycle).
  Otherwise leave engine demand unmodified.

  Closed-loop: measured buffer exchange trims over-credited assist.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BufferTelemetry:
    """Live PCMRITMS reservoir state fed into the ATPE Brain each cycle."""

    soc: float
    max_discharge_w: float
    max_charge_w: float
    peak_transient_w: float | None = None
    soc_target: float = 0.7
    reserve_floor: float = 0.15
    measured_buffer_w: float = 0.0
    closed_loop: bool = True

    @property
    def continuous_w(self) -> float:
        return max(0.0, self.max_discharge_w)

    @property
    def peak_w(self) -> float:
        peak = self.peak_transient_w
        if peak is None:
            return self.continuous_w
        return max(self.continuous_w, peak)


@dataclass(frozen=True)
class BufferPlan:
    """PCMRITMS arbitration output for one supervisor cycle."""

    engine_demand_w: float
    buffer_assist_w: float
    precharge_w: float
    buffer_burst_w: float | None
    notes: str = ""
    assist_error_w: float = 0.0
    closed_loop: bool = False
    assist_event: bool = False
    precharge_event: bool = False
    surge_event: bool = False
    precharge_blocked_cooldown: bool = False


class PcmritmsCoordinator:
    """Benefit-seeking PCMRITMS / ATPE demand arbitration."""

    def __init__(
        self,
        *,
        spike_assist_threshold_w: float = 25_000.0,
        precharge_enable_soc: float = 0.35,
        precharge_target_soc: float = 0.50,
        max_precharge_fraction: float = 0.20,
        post_assist_precharge_cooldown_s: float = 12.0,
        steady_load_suppress_s: float = 8.0,
        post_spike_precharge_window_s: float = 25.0,
        emergency_soc: float = 0.28,
        closed_loop_gain: float = 0.35,
        closed_loop_alpha: float = 0.30,
        assume_dt_s: float = 1.0,
        health_protect_threshold_pct: float = 90.0,
        health_protect_threshold_scale: float = 0.70,
    ) -> None:
        self.spike_assist_threshold_w = max(0.0, spike_assist_threshold_w)
        self.precharge_enable_soc = precharge_enable_soc
        self.precharge_target_soc = max(precharge_target_soc, precharge_enable_soc)
        self.max_precharge_fraction = min(1.0, max(0.0, max_precharge_fraction))
        self.post_assist_precharge_cooldown_s = max(0.0, post_assist_precharge_cooldown_s)
        self.steady_load_suppress_s = max(0.0, steady_load_suppress_s)
        self.post_spike_precharge_window_s = max(0.0, post_spike_precharge_window_s)
        self.emergency_soc = min(emergency_soc, precharge_enable_soc)
        self.closed_loop_gain = max(0.0, closed_loop_gain)
        self.closed_loop_alpha = min(1.0, max(0.0, closed_loop_alpha))
        self.assume_dt_s = max(1e-3, assume_dt_s)
        self.health_protect_threshold_pct = health_protect_threshold_pct
        self.health_protect_threshold_scale = min(
            1.0, max(0.2, health_protect_threshold_scale)
        )
        self._last_assist_w: float = 0.0
        self._assist_error_w: float = 0.0
        self._precharge_armed: bool = False
        self._precharge_cooldown_s: float = 0.0
        # Large = no recent assist/surge (tow/cruise → no opportunistic precharge).
        self._seconds_since_assist: float = 1e9
        self.assist_energy_j: float = 0.0
        self.precharge_energy_j: float = 0.0

    def reset(self) -> None:
        self._last_assist_w = 0.0
        self._assist_error_w = 0.0
        self._precharge_armed = False
        self._precharge_cooldown_s = 0.0
        self._seconds_since_assist = 1e9
        self.assist_energy_j = 0.0
        self.precharge_energy_j = 0.0

    @property
    def recharge_to_assist_ratio(self) -> float:
        """Commanded precharge energy / commanded assist energy (J/J)."""
        if self.assist_energy_j <= 1.0:
            return 0.0
        return self.precharge_energy_j / self.assist_energy_j

    def solve(
        self,
        *,
        bus_demand_w: float,
        engine_setpoint_w: float,
        buffer: BufferTelemetry | None,
        slow_setpoint_w: float | None = None,
        dt_s: float | None = None,
        min_health_pct: float | None = None,
        surge_scale: float = 1.0,
    ) -> BufferPlan:
        """Arbitrate only when there is an expected net benefit; else pass-through.

        ``slow_setpoint_w`` is the filtered / pre-spike-floor generation reference.
        Residual is measured against that so assist can fire when the vehicle
        controller has already floored setpoint toward bus demand.

        ``min_health_pct`` enables Gate-6 health-aware assist: when the weakest
        cartridge is below threshold, the spike threshold is lowered so PCMRITMS
        absorbs more load before marginal tiers fire.

        ``surge_scale`` (0..1) comes from closed-loop rotor coherence.
        """
        dt = self.assume_dt_s if dt_s is None else max(1e-6, dt_s)
        setpoint = max(0.0, engine_setpoint_w)
        if buffer is None:
            self._last_assist_w = 0.0
            self._tick_cooldown(dt)
            return BufferPlan(
                engine_demand_w=setpoint,
                buffer_assist_w=0.0,
                precharge_w=0.0,
                buffer_burst_w=None,
                notes="",
            )

        if buffer.closed_loop:
            self._update_closed_loop(buffer)

        bus = max(0.0, bus_demand_w)
        continuous = buffer.continuous_w
        scale = max(0.0, min(1.0, surge_scale))
        peak = continuous + scale * max(0.0, buffer.peak_w - continuous)
        ready = buffer.soc > buffer.reserve_floor
        slow = (
            max(0.0, slow_setpoint_w)
            if slow_setpoint_w is not None
            else setpoint
        )
        residual = max(0.0, bus - slow)

        spike_thr = self.spike_assist_threshold_w
        health_protect = False
        if (
            min_health_pct is not None
            and min_health_pct < self.health_protect_threshold_pct
        ):
            spike_thr *= self.health_protect_threshold_scale
            health_protect = True

        assist = 0.0
        assist_event = False
        if ready and residual > spike_thr:
            assist = min(residual, continuous)
            assist_event = assist > 1.0

        if buffer.closed_loop and self._assist_error_w > 0.0 and assist > 0.0:
            assist = max(0.0, assist - self.closed_loop_gain * self._assist_error_w)
            assist_event = assist > 1.0

        genuine_launch = ready and residual > continuous * 0.98
        if genuine_launch:
            if buffer.closed_loop and self._assist_error_w > continuous * 0.15:
                burst_w: float | None = continuous + 0.5 * (peak - continuous)
            else:
                burst_w = peak
        else:
            burst_w = continuous if ready else continuous

        # Recovery clock runs only after real assist/surge — not residual blips
        # (those were causing tow/grade opportunistic precharge).
        if assist_event or genuine_launch:
            self._seconds_since_assist = 0.0
            self._precharge_cooldown_s = self.post_assist_precharge_cooldown_s
        else:
            self._seconds_since_assist += dt

        # Hysteresis SoC band: arm below enable, stay armed until target.
        if buffer.soc <= self.precharge_enable_soc:
            self._precharge_armed = True
        elif buffer.soc >= self.precharge_target_soc:
            self._precharge_armed = False

        cooldown_block = self._precharge_cooldown_s > 0.0
        # Precharge only in post-assist/surge recovery window (after cooldown).
        # Empty-buffer refill on steady tow/grade is left to plant Loop C, so
        # the brain does not opportunistically raise engine demand without a
        # prior assist/surge event.
        recovery = self._seconds_since_assist < self.post_spike_precharge_window_s
        precharge = 0.0
        precharge_event = False
        if (
            setpoint > 0.0
            and self._precharge_armed
            and not cooldown_block
            and recovery
            and buffer.soc < self.precharge_target_soc
        ):
            deficit = self.precharge_target_soc - buffer.soc
            band = max(0.05, self.precharge_target_soc - self.precharge_enable_soc)
            frac = min(1.0, deficit / band)
            precharge = self.max_precharge_fraction * buffer.max_charge_w * frac
            precharge_event = precharge > 1.0

        softened = max(0.0, setpoint - assist)
        if slow_setpoint_w is not None and assist > 0.0:
            softened = max(slow, softened)
        engine_demand = softened + precharge
        self._last_assist_w = assist

        if assist > 1.0:
            self.assist_energy_j += assist * dt
        if precharge > 1.0:
            self.precharge_energy_j += precharge * dt

        self._tick_cooldown(dt)

        notes: list[str] = []
        if assist_event:
            notes.append(f"pcmritms assist {assist / 1e3:.1f} kW")
        if precharge_event:
            notes.append(f"pcmritms precharge {precharge / 1e3:.1f} kW")
        if genuine_launch:
            notes.append(f"pcmritms burst {(burst_w or 0.0) / 1e3:.0f} kW")
        if cooldown_block and self._precharge_armed:
            notes.append("pcmritms prech cooldown")
        if buffer.closed_loop and abs(self._assist_error_w) > 100.0:
            notes.append(f"pcmritms cl-err {self._assist_error_w / 1e3:.1f} kW")
        if health_protect:
            notes.append(
                f"pcmritms health-protect minH={min_health_pct:.0f} thr={spike_thr / 1e3:.0f}kW"
            )
        if scale < 0.999:
            notes.append(f"pcmritms surge_scale {scale:.2f}")

        return BufferPlan(
            engine_demand_w=engine_demand,
            buffer_assist_w=assist,
            precharge_w=precharge,
            buffer_burst_w=burst_w,
            notes="; ".join(notes),
            assist_error_w=self._assist_error_w,
            closed_loop=buffer.closed_loop,
            assist_event=assist_event,
            precharge_event=precharge_event,
            surge_event=genuine_launch,
            precharge_blocked_cooldown=cooldown_block,
        )

    def _tick_cooldown(self, dt_s: float) -> None:
        if self._precharge_cooldown_s > 0.0:
            self._precharge_cooldown_s = max(0.0, self._precharge_cooldown_s - dt_s)

    def _update_closed_loop(self, buffer: BufferTelemetry) -> None:
        measured_discharge = max(0.0, buffer.measured_buffer_w)
        if self._last_assist_w <= 1.0:
            return
        instant = self._last_assist_w - measured_discharge
        a = self.closed_loop_alpha
        self._assist_error_w = (1.0 - a) * self._assist_error_w + a * instant
