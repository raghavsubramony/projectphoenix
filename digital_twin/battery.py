"""LFP traction battery: bounded electrochemical store with power limits."""

from __future__ import annotations

from .config import BatteryConfig


class Battery:
    """Energy buffer + EV-range store.

    Sign convention for `exchange` matches the buffer: positive request means
    discharge (deliver to bus), negative means charge (absorb from bus). A
    simple coulombic efficiency is applied on charging.

    When `cfg.thermal` is set the pack also runs a lumped single-node thermal
    model: internal-resistance (I^2R) heat raises the cell temperature, Newtonian
    cooling removes it, and above a threshold the usable power ramps down
    (battery-management thermal protection). With `cfg.thermal is None` the pack
    is thermodynamically ideal and behaves exactly as the validated baseline.
    """

    CHARGE_EFFICIENCY = 0.97

    def __init__(self, cfg: BatteryConfig) -> None:
        self.cfg = cfg
        self.energy_j = cfg.usable_capacity_j * cfg.initial_soc
        # Thermal state (only meaningful when cfg.thermal is set).
        self.temperature_c = cfg.thermal.initial_temp_c if cfg.thermal else 25.0
        self.peak_temperature_c = self.temperature_c
        self.heat_loss_j = 0.0
        # Cumulative bidirectional throughput (for durability / EFC accounting).
        self.throughput_j = 0.0

    @property
    def soc(self) -> float:
        return self.energy_j / self.cfg.usable_capacity_j

    def exchange(self, request_w: float, dt_s: float) -> float:
        """Attempt to source (+) or sink (-) `request_w` for `dt_s` seconds."""
        if request_w > 0.0:
            return self._discharge(request_w, dt_s)
        if request_w < 0.0:
            return -self._charge(-request_w, dt_s)
        return 0.0

    # --- thermal helpers (no-ops when cfg.thermal is None) ------------------

    def _derate_factor(self) -> float:
        t = self.cfg.thermal
        if t is None:
            return 1.0
        temp = self.temperature_c
        if temp <= t.derate_start_c:
            return 1.0
        if temp >= t.derate_end_c:
            return t.floor_fraction
        span = t.derate_end_c - t.derate_start_c
        frac = (temp - t.derate_start_c) / span
        return 1.0 - frac * (1.0 - t.floor_fraction)

    def _loss_w(self, terminal_w: float) -> float:
        """Internal-resistance heat for a given terminal power (I^2R)."""
        t = self.cfg.thermal
        if t is None:
            return 0.0
        current_a = terminal_w / t.nominal_voltage_v
        return current_a * current_a * t.internal_resistance_ohm

    def _update_temperature(self, loss_w: float, dt_s: float) -> None:
        t = self.cfg.thermal
        if t is None:
            return
        cooling_w = t.cooling_w_per_k * (self.temperature_c - t.ambient_temp_c)
        d_temp = (loss_w - cooling_w) / t.thermal_mass_j_per_k * dt_s
        self.temperature_c += d_temp
        self.peak_temperature_c = max(self.peak_temperature_c, self.temperature_c)
        self.heat_loss_j += loss_w * dt_s

    # --- power exchange -----------------------------------------------------

    def _discharge(self, power_w: float, dt_s: float) -> float:
        power_w = min(power_w, self.cfg.max_discharge_w * self._derate_factor())
        if self.cfg.thermal is None:
            max_w = self.energy_j / dt_s
            power_w = min(power_w, max_w)
            self.energy_j -= power_w * dt_s
            self.throughput_j += power_w * dt_s
            return power_w
        # Thermal path: the cell must supply the terminal power *plus* the I^2R
        # loss, so it drains faster than the bus sees.
        loss_w = self._loss_w(power_w)
        total_w = power_w + loss_w
        max_w = self.energy_j / dt_s
        if total_w > max_w and total_w > 0.0:
            scale = max_w / total_w
            power_w *= scale
            loss_w = self._loss_w(power_w)
            total_w = power_w + loss_w
        self.energy_j -= total_w * dt_s
        self.throughput_j += power_w * dt_s
        self._update_temperature(loss_w, dt_s)
        return power_w

    def _charge(self, power_w: float, dt_s: float) -> float:
        power_w = min(power_w, self.cfg.max_charge_w * self._derate_factor())
        if self.cfg.thermal is None:
            store_w = power_w * self.CHARGE_EFFICIENCY
            headroom_j = self.cfg.usable_capacity_j - self.energy_j
            max_store_w = headroom_j / dt_s
            if store_w > max_store_w:
                store_w = max_store_w
                power_w = store_w / self.CHARGE_EFFICIENCY
            self.energy_j += store_w * dt_s
            self.throughput_j += store_w * dt_s
            return power_w
        # Thermal path: coulombic loss + I^2R heat both reduce what is stored.
        loss_w = self._loss_w(power_w)
        store_w = power_w * self.CHARGE_EFFICIENCY - loss_w
        if store_w < 0.0:
            store_w = 0.0
        headroom_j = self.cfg.usable_capacity_j - self.energy_j
        max_store_w = headroom_j / dt_s
        if store_w > max_store_w:
            store_w = max_store_w
            power_w = (store_w + loss_w) / self.CHARGE_EFFICIENCY
            loss_w = self._loss_w(power_w)
        self.energy_j += store_w * dt_s
        self.throughput_j += store_w * dt_s
        self._update_temperature(loss_w, dt_s)
        return power_w

