"""Powertrain orchestrator: integrates every subsystem over one timestep."""

from __future__ import annotations

from dataclasses import dataclass

from .atpe import ATPE
from .battery import Battery
from .config import TwinConfig
from .controller import UnifiedController
from .dc_link import DcLink, DcLinkConfig
from .pcmritms import InertialBuffer
from .vehicle import Vehicle


@dataclass
class StepResult:
    """Telemetry for a single simulation step (all powers in watts)."""

    time_s: float
    speed_ms: float
    demand_w: float
    generation_w: float
    buffer_w: float          # + = buffer discharging to bus
    battery_w: float         # + = battery discharging to bus
    shortfall_w: float       # unmet traction demand (capability limit)
    surplus_w: float         # generation that could not be stored
    mode: str
    active_tier: str
    active_index: int
    efficiency: float
    fuel_l: float
    co2_kg: float
    battery_soc: float
    buffer_soc: float
    active_cartridges: int = 0
    dispatch_mode: str = ""
    imep_bar: float = 0.0
    knock_index: float = 0.0
    peak_pressure_bar: float = 0.0
    predicted_tdc_mm: float = 0.0
    dc_link_voltage_v: float = 0.0
    dc_link_limited: bool = False
    inverter_clip_w: float = 0.0


class Powertrain:
    """The digital twin. Call `step` repeatedly, or use `simulation.run`."""

    def __init__(self, cfg: TwinConfig, controller=None) -> None:
        self.cfg = cfg
        self.vehicle = Vehicle(cfg.vehicle)
        ring_cfg = cfg.atpe.dynamic_ring
        self._use_dynamic_ring = bool(ring_cfg and ring_cfg.enabled)
        if self._use_dynamic_ring:
            from .atpe_ring import DynamicRingATPE

            self.ring_atpe = DynamicRingATPE(cfg.atpe)
            self.atpe = self.ring_atpe
        else:
            self.atpe = ATPE(cfg.atpe)
        self.buffer = InertialBuffer(cfg.buffer)
        self.battery = Battery(cfg.battery)
        # The control policy is pluggable: any object exposing the same
        # `decide(...) -> ControlState` interface works (e.g. a learned policy).
        # Defaults to the rule-based UnifiedController.
        self.controller = controller or UnifiedController(
            cfg.control,
            battery_soc_target=cfg.battery.soc_target,
            battery_soc_ev_floor=cfg.battery.soc_ev_floor,
        )
        link_cfg = cfg.dc_link or DcLinkConfig(enabled=False)
        self.dc_link = DcLink(link_cfg)
        self.time_s = 0.0

    def _buffer_telemetry(self):
        """Build ATPE Brain PCMRITMS telemetry from the live inertial buffer."""
        from atpe_brain import BufferTelemetry

        cfg = self.buffer.cfg
        return BufferTelemetry(
            soc=self.buffer.state_of_charge,
            max_discharge_w=cfg.max_discharge_w,
            max_charge_w=cfg.max_charge_w,
            peak_transient_w=cfg.peak_transient_w,
            soc_target=self.cfg.control.buffer_soc_target,
        )

    def step(self, speed_ms: float, accel_ms2: float, grade_rad: float,
             dt_s: float) -> StepResult:
        # 1) Vehicle demand on the DC bus.
        demand_w = self.vehicle.power_demand_w(speed_ms, accel_ms2, grade_rad)

        # 2) Controller decides mode + generation setpoint (Loops A & B).
        ctrl = self.controller.decide(
            demand_w, self.battery.soc, self.atpe.max_electric_w, dt_s,
            speed_ms=speed_ms, buffer_soc=self.buffer.state_of_charge)

        # 3) ATPE generates toward the setpoint (Brain + PCMRITMS when dynamic).
        if self._use_dynamic_ring:
            gen = self.ring_atpe.generate(
                ctrl.gen_setpoint_w,
                dt_s,
                bus_demand_w=demand_w,
                buffer=self._buffer_telemetry(),
                # Residual vs filtered demand so assist fires on spike-floor steps.
                slow_setpoint_w=ctrl.filtered_demand_w,
            )
            burst_cap_w = (
                self.ring_atpe.last_buffer_burst_w
                if self.ring_atpe.last_buffer_burst_w is not None
                else ctrl.buffer_burst_w
            )
        else:
            gen = self.atpe.generate(ctrl.gen_setpoint_w, dt_s)
            burst_cap_w = ctrl.buffer_burst_w

        gen_w, gen_clip_w = self.dc_link.limit_generation(gen.electric_w, dt_s)

        # 4) Loop C: arbitrate the mismatch between demand and generation.
        mismatch_w = demand_w - gen_w
        buffer_w = 0.0
        battery_w = 0.0
        shortfall_w = 0.0
        surplus_w = 0.0
        bus_clip_w = 0.0

        if mismatch_w > 0.0:
            # Deficit: buffer first (fastest), then battery, then flag shortfall.
            # DC-link caps how much storage may discharge onto the bus.
            desired_storage = mismatch_w
            allowed_storage, storage_clip = self.dc_link.limit_storage_discharge(
                desired_storage, dt_s, generation_on_bus_w=gen_w,
            )
            bus_clip_w += storage_clip
            buffer_w = self.buffer.exchange(allowed_storage, dt_s,
                                            burst_cap_w=burst_cap_w)
            remaining = allowed_storage - buffer_w
            battery_w = self.battery.exchange(remaining, dt_s)
            shortfall_w = max(0.0, mismatch_w - buffer_w - battery_w)
        elif mismatch_w < 0.0:
            # Surplus: refill buffer toward target first, then charge battery.
            surplus = -mismatch_w
            buffer_headroom_target = max(
                0.0,
                self.cfg.control.buffer_soc_target - self.buffer.state_of_charge,
            )
            if buffer_headroom_target > 0.0:
                charge_cap = self.dc_link.limit_storage_charge(surplus, dt_s)
                refused = max(0.0, surplus - charge_cap)
                bus_clip_w += refused
                absorbed = -self.buffer.exchange(-charge_cap, dt_s)
                buffer_w = -absorbed
                surplus -= absorbed
            charge_cap = self.dc_link.limit_storage_charge(surplus, dt_s)
            refused = max(0.0, surplus - charge_cap)
            bus_clip_w += refused
            charged = -self.battery.exchange(-charge_cap, dt_s)
            battery_w = -charged
            surplus -= charged
            surplus_w = max(0.0, surplus)

        if self._use_dynamic_ring:
            self.ring_atpe.observe_buffer_exchange(buffer_w)

        telem = self.dc_link.observe(
            gen_w=gen_w,
            buffer_w=buffer_w,
            battery_w=battery_w,
            gen_clip_w=gen_clip_w,
            bus_clip_w=bus_clip_w,
            dt_s=dt_s,
        )

        self.time_s += dt_s
        active_cartridges = 0
        dispatch_mode = ""
        if self._use_dynamic_ring:
            active_cartridges = self.ring_atpe.last_active_count
            dispatch_mode = self.ring_atpe.last_dispatch_mode
        return StepResult(
            time_s=self.time_s,
            speed_ms=speed_ms,
            demand_w=demand_w,
            generation_w=gen_w,
            buffer_w=buffer_w,
            battery_w=battery_w,
            shortfall_w=shortfall_w,
            surplus_w=surplus_w,
            mode=ctrl.mode,
            active_tier=gen.active_tier,
            active_index=gen.active_index,
            efficiency=gen.efficiency,
            fuel_l=gen.fuel_l,
            co2_kg=gen.co2_kg,
            battery_soc=self.battery.soc,
            buffer_soc=self.buffer.state_of_charge,
            active_cartridges=active_cartridges,
            dispatch_mode=dispatch_mode,
            imep_bar=gen.imep_bar,
            knock_index=gen.knock_index,
            peak_pressure_bar=gen.peak_pressure_bar,
            predicted_tdc_mm=gen.predicted_tdc_mm,
            dc_link_voltage_v=telem.voltage_v,
            dc_link_limited=telem.limited,
            inverter_clip_w=telem.gen_clip_w + telem.bus_clip_w,
        )
