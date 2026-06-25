"""PCMRITMS inertial torque buffer, modeled as a bounded kinetic reservoir."""

from __future__ import annotations

import math

from .config import BufferConfig


class InertialBuffer:
    """Phase-controlled multi-rotor buffer with split round-trip efficiency.

    Sign convention for `exchange`: a positive request means "deliver power to
    the bus" (discharge); a negative request means "absorb power from the bus"
    (charge). The returned value is the power actually exchanged with the bus,
    clamped to the buffer's power and energy limits.
    """

    def __init__(self, cfg: BufferConfig) -> None:
        self.cfg = cfg
        self._eta_leg = math.sqrt(cfg.round_trip_efficiency)
        self.energy_j = cfg.max_energy_j * cfg.initial_fraction
        # Brief-burst discharge cap (rotor coupling); falls back to continuous.
        self._discharge_cap_w = (cfg.peak_transient_w
                                 if cfg.peak_transient_w is not None
                                 else cfg.max_discharge_w)

    @property
    def state_of_charge(self) -> float:
        return self.energy_j / self.cfg.max_energy_j

    def exchange(self, request_w: float, dt_s: float,
                 burst_cap_w: float | None = None) -> float:
        """Attempt to source (+) or sink (-) `request_w` for `dt_s` seconds.

        `burst_cap_w` optionally overrides the discharge ceiling for this step
        (a closed-loop controller uses it to gate surge authority). It only
        tightens or relaxes discharge; charging is unaffected.
        """
        if request_w > 0.0:
            return self._discharge(request_w, dt_s, burst_cap_w)
        if request_w < 0.0:
            return -self._charge(-request_w, dt_s)
        return 0.0

    def _discharge(self, power_w: float, dt_s: float,
                   burst_cap_w: float | None = None) -> float:
        cap_w = self._discharge_cap_w if burst_cap_w is None else burst_cap_w
        power_w = min(power_w, cap_w)
        # Stored energy drains faster than bus delivery because of leg losses.
        drain_w = power_w / self._eta_leg
        max_drain_w = self.energy_j / dt_s
        if drain_w > max_drain_w:
            drain_w = max_drain_w
            power_w = drain_w * self._eta_leg
        self.energy_j -= drain_w * dt_s
        return power_w

    def _charge(self, power_w: float, dt_s: float) -> float:
        power_w = min(power_w, self.cfg.max_charge_w)
        # Only a fraction of bus power becomes stored energy.
        store_w = power_w * self._eta_leg
        headroom_j = self.cfg.max_energy_j - self.energy_j
        max_store_w = headroom_j / dt_s
        if store_w > max_store_w:
            store_w = max_store_w
            power_w = store_w / self._eta_leg
        self.energy_j += store_w * dt_s
        return power_w
