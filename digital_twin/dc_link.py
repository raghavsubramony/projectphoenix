"""HV DC-link + inverter limits for the series-hybrid bus.

Opt-in: when ``DcLinkConfig.enabled`` is False the limiter is a no-op so
validated Phase-1 fuel figures are unchanged. When enabled, generation and
storage exchanges are clipped by continuous/peak inverter ratings, bus
voltage sag, and a simple precharge gate (contactor not ready).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DcLinkConfig:
    """Traction / generator inverter and HV bus ratings."""

    enabled: bool = False
    nominal_voltage_v: float = 400.0
    continuous_bus_w: float = 160_000.0
    peak_bus_w: float = 240_000.0
    peak_hold_s: float = 2.0
    generator_continuous_w: float = 150_000.0
    generator_peak_w: float = 200_000.0
    min_operate_voltage_v: float = 330.0
    precharge_ready_v: float = 360.0
    initial_voltage_v: float = 400.0
    # Effective source impedance for a first-order sag model (V = V0 - I*R).
    sag_ohm: float = 0.040
    # While below precharge_ready_v, only this much charge power may raise the bus.
    precharge_charge_w: float = 5_000.0


@dataclass
class DcLinkTelemetry:
    """Per-step DC-link observability."""

    voltage_v: float
    bus_limit_w: float
    gen_limit_w: float
    gen_clip_w: float
    bus_clip_w: float
    precharge_ready: bool
    limited: bool


@dataclass
class DcLink:
    """Stateful limiter: peak timer + voltage tracking."""

    cfg: DcLinkConfig
    voltage_v: float = 0.0
    _peak_used_s: float = 0.0

    def __post_init__(self) -> None:
        if self.voltage_v <= 0.0:
            self.voltage_v = self.cfg.initial_voltage_v

    @property
    def precharge_ready(self) -> bool:
        if not self.cfg.enabled:
            return True
        return self.voltage_v >= self.cfg.precharge_ready_v

    def _bus_cap_w(self, dt_s: float) -> float:
        cfg = self.cfg
        if not cfg.enabled:
            return float("inf")
        if not self.precharge_ready:
            return 0.0
        # Allow peak until hold budget exhausted this "event"; recover slowly.
        if self._peak_used_s < cfg.peak_hold_s:
            return cfg.peak_bus_w
        return cfg.continuous_bus_w

    def _gen_cap_w(self) -> float:
        cfg = self.cfg
        if not cfg.enabled:
            return float("inf")
        if self._peak_used_s < cfg.peak_hold_s:
            return cfg.generator_peak_w
        return cfg.generator_continuous_w

    def _update_voltage(self, net_out_w: float, dt_s: float) -> None:
        """Positive net_out_w = power leaving the bus toward the wheels."""
        cfg = self.cfg
        if not cfg.enabled:
            return
        # Precharge phase: only charging may raise the bus; never snap to nominal.
        if self.voltage_v < cfg.precharge_ready_v:
            if net_out_w < 0.0:
                # ~few kW precharge raises V gradually.
                self.voltage_v = min(
                    cfg.nominal_voltage_v,
                    self.voltage_v + 15.0 * dt_s,
                )
            return
        v = max(cfg.min_operate_voltage_v, self.voltage_v)
        current_a = net_out_w / max(v, 1.0)
        sag = current_a * cfg.sag_ohm
        if net_out_w >= 0.0:
            target = cfg.nominal_voltage_v - sag
        else:
            target = min(cfg.nominal_voltage_v, v + 5.0 * dt_s)
        # Cap tracking rate so a 1 s sim step cannot teleport the bus.
        alpha = min(0.35, max(0.0, dt_s) / 0.2)
        self.voltage_v = max(
            cfg.min_operate_voltage_v,
            self.voltage_v + alpha * (target - self.voltage_v),
        )

    def _update_peak_timer(self, used_peak: bool, dt_s: float) -> None:
        if not self.cfg.enabled:
            return
        if used_peak:
            self._peak_used_s = min(
                self.cfg.peak_hold_s + 1.0,
                self._peak_used_s + dt_s,
            )
        else:
            self._peak_used_s = max(0.0, self._peak_used_s - 0.5 * dt_s)

    def limit_generation(self, gen_w: float, dt_s: float) -> tuple[float, float]:
        """Return (delivered_gen_w, clip_w)."""
        if not self.cfg.enabled:
            return gen_w, 0.0
        cap = self._gen_cap_w()
        # Voltage collapse further derates generator inverter.
        if self.voltage_v < self.cfg.min_operate_voltage_v + 10.0:
            span = 10.0
            derate = max(
                0.2,
                (self.voltage_v - self.cfg.min_operate_voltage_v) / span,
            )
            cap *= derate
        delivered = min(max(0.0, gen_w), cap)
        return delivered, max(0.0, gen_w - delivered)

    def limit_storage_discharge(
        self,
        requested_w: float,
        dt_s: float,
        *,
        generation_on_bus_w: float,
    ) -> tuple[float, float]:
        """Cap buffer+battery discharge so gen+storage ≤ bus inverter rating."""
        if not self.cfg.enabled:
            return max(0.0, requested_w), 0.0
        if requested_w <= 0.0:
            return 0.0, 0.0
        bus_cap = self._bus_cap_w(dt_s)
        headroom = max(0.0, bus_cap - max(0.0, generation_on_bus_w))
        delivered = min(requested_w, headroom)
        return delivered, max(0.0, requested_w - delivered)

    def limit_storage_charge(self, requested_charge_w: float, dt_s: float) -> float:
        """Cap charging power (positive = into storage)."""
        if not self.cfg.enabled:
            return max(0.0, requested_charge_w)
        if not self.precharge_ready:
            return min(max(0.0, requested_charge_w), self.cfg.precharge_charge_w)
        return min(max(0.0, requested_charge_w), self.cfg.continuous_bus_w)

    def observe(
        self,
        *,
        gen_w: float,
        buffer_w: float,
        battery_w: float,
        gen_clip_w: float,
        bus_clip_w: float,
        dt_s: float,
    ) -> DcLinkTelemetry:
        """Update voltage/peak state after the step's bus flows are known."""
        # Power leaving the HV source side toward traction motors.
        net_out = max(0.0, gen_w) + max(0.0, buffer_w) + max(0.0, battery_w)
        net_out -= max(0.0, -buffer_w) + max(0.0, -battery_w)
        used_peak = net_out > self.cfg.continuous_bus_w + 1.0 or gen_w > (
            self.cfg.generator_continuous_w + 1.0
        )
        self._update_peak_timer(used_peak and self.cfg.enabled, dt_s)
        self._update_voltage(net_out, dt_s)
        limited = (gen_clip_w > 1.0) or (bus_clip_w > 1.0) or (
            self.cfg.enabled and not self.precharge_ready and net_out > 1.0
        )
        return DcLinkTelemetry(
            voltage_v=self.voltage_v,
            bus_limit_w=self._bus_cap_w(dt_s) if self.cfg.enabled else float("inf"),
            gen_limit_w=self._gen_cap_w() if self.cfg.enabled else float("inf"),
            gen_clip_w=gen_clip_w,
            bus_clip_w=bus_clip_w,
            precharge_ready=self.precharge_ready,
            limited=limited,
        )


def default_phase1_dc_link(*, enabled: bool = True) -> DcLinkConfig:
    """Representative Phase-1 HV inverter sizing (opt-in studies)."""
    return DcLinkConfig(enabled=enabled)
