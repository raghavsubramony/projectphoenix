"""Dynamic ring ATPE — demand-driven cartridge dispatch for the vehicle twin.

Uses Gate-5 production freeze (phase layout + load policy) with per-slot probes
cached at init. Each vehicle timestep runs the ATPE Brain supervisor to schedule
cartridges by bus demand without re-running full cartridge physics.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from atpe_brain import (
    ATPESupervisor,
    BufferTelemetry,
    CartridgeProbe,
    RingContext,
    SupervisorConfig,
)
from designs.phoenix_v3.cartridge_scheduler import (
    DispatchMode,
    build_gate5_production_options,
)
from designs.phoenix_v3.cartridge_state import (
    CartridgeRuntimeState,
    initial_ring_states,
    update_state_from_telemetry,
)
from designs.phoenix_v3_mixed_ring import (
    GATE4_LAYOUT,
    MixedCartridgeSlot,
    MixedRingLayout,
    RingBuildOptions,
    build_mixed_ring_slots,
)
from designs.phoenix_v3_thermal import CoolantBus, advance_coolant_bus

from .atpe import GenerationResult
from .config import (
    ATPEConfig,
    DynamicRingConfig,
    GASOLINE_CO2_KG_PER_L,
    GASOLINE_DENSITY_KG_PER_L,
    GASOLINE_LHV_MJ_PER_KG,
)

_LHV_J_PER_KG = GASOLINE_LHV_MJ_PER_KG * 1e6


@dataclass(frozen=True)
class SlotProbeCache:
    """Cached Gate-5 probe for one ring slot (vehicle-twin alias)."""

    index: int
    tier_index: int
    tier_name: str
    nominal_power_w: float
    nominal_efficiency: float
    wall_temp_k: float
    generator_temp_k: float
    valve_temp_k: float
    generator_derate: float
    spring_recovery: float

    @classmethod
    def from_probe(cls, probe: CartridgeProbe) -> SlotProbeCache:
        return cls(**probe.__dict__)

    def to_probe(self) -> CartridgeProbe:
        return CartridgeProbe(**self.__dict__)


def _probe_slot(
    slot: MixedCartridgeSlot,
    *,
    cycles: int,
    coolant: CoolantBus | None,
) -> SlotProbeCache:
    from designs.phoenix_v3.config import PhoenixV3Config
    from designs.phoenix_v3_simulation import PhoenixV3Simulator

    cfg = slot.cfg
    if coolant is not None:
        cfg = replace(
            cfg,
            shared_coolant_enabled=True,
            coolant_temp_k=coolant.temp_k,
        )
    sim = PhoenixV3Simulator(cfg).simulate(cycles=cycles, record_history=False)
    reports = [
        r for r in sim.energy.cycle_reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]
    late = reports[-max(1, len(reports) // 10):] if reports else []
    if not late:
        return SlotProbeCache(
            slot.index, slot.tier_index, slot.tier_name,
            0.0, 0.45, 293.15, 293.15, 293.15, 1.0, 0.95,
        )
    elec_j = float(np.mean([r.elec_energy_j for r in late]))
    eff = float(np.mean([r.net_cartridge_efficiency for r in late]))
    power_w = elec_j / max(cfg.cycle_time_s, 1e-9)
    return SlotProbeCache(
        index=slot.index,
        tier_index=slot.tier_index,
        tier_name=slot.tier_name,
        nominal_power_w=power_w,
        nominal_efficiency=max(eff, 0.10),
        wall_temp_k=float(np.mean([r.wall_temp_k for r in late])),
        generator_temp_k=float(np.mean([r.generator_temp_k for r in late])),
        valve_temp_k=float(np.mean([r.valve_temp_k for r in late])),
        generator_derate=float(np.mean([r.generator_derate for r in late])),
        spring_recovery=float(np.mean([r.spring_recovery_efficiency for r in late])),
    )


def probe_gate5_ring_slots(
    *,
    probe_cycles: int = 6,
    fast_probe: bool = True,
    shared_coolant: bool = True,
) -> tuple[tuple[MixedCartridgeSlot, ...], tuple[SlotProbeCache, ...], RingBuildOptions]:
    """Build Gate-5 production ring and probe each slot once."""
    layout = MixedRingLayout(*GATE4_LAYOUT)
    if fast_probe:
        build_opts = build_gate5_production_options(
            layout, capture_steps=15, capture_passes=1, phase_steps=20, cycles=probe_cycles,
        )
    else:
        build_opts = build_gate5_production_options(
            layout, capture_steps=25, capture_passes=2, phase_steps=30, cycles=probe_cycles,
        )
    slots = build_mixed_ring_slots(layout, build_options=build_opts)
    coolant = CoolantBus(temp_k=293.15) if shared_coolant else None
    caches: list[SlotProbeCache] = []
    ambient = slots[0].cfg.ambient_temp_k if slots else 293.15
    for slot in slots:
        cache = _probe_slot(slot, cycles=probe_cycles, coolant=coolant)
        caches.append(cache)
        if coolant is not None and slot.cfg.cycle_time_s > 0:
            from designs.phoenix_v3_thermal import estimate_cartridge_heat_w
            from designs.phoenix_v3_simulation import PhoenixV3Simulator

            cfg = replace(
                slot.cfg,
                shared_coolant_enabled=True,
                coolant_temp_k=coolant.temp_k,
            )
            sim = PhoenixV3Simulator(cfg).simulate(cycles=probe_cycles, record_history=False)
            heat_w = estimate_cartridge_heat_w(
                sim.energy.cycle_reports, cfg.cycle_time_s,
            )
            coolant = advance_coolant_bus(
                coolant,
                ambient_temp_k=ambient,
                coolant_thermal_mass_j_per_k=cfg.coolant_thermal_mass_j_per_k,
                coolant_radiator_w_per_k=cfg.coolant_radiator_w_per_k,
                heat_in_w=heat_w,
                duration_s=probe_cycles * cfg.cycle_time_s,
            )
    return slots, tuple(caches), build_opts


class DynamicRingATPE:
    """ATPE replacement: ATPE Brain supervises individual cartridge dispatch."""

    def __init__(self, cfg: ATPEConfig) -> None:
        self.cfg = cfg
        ring_cfg = cfg.dynamic_ring or DynamicRingConfig()
        self._ring_cfg = ring_cfg
        self._slots, self._caches, self._build_opts = probe_gate5_ring_slots(
            probe_cycles=ring_cfg.probe_cycles,
            fast_probe=ring_cfg.fast_probe,
            shared_coolant=ring_cfg.shared_coolant,
        )
        self._cache_by_index = {c.index: c for c in self._caches}
        probes = tuple(c.to_probe() for c in self._caches)
        self._ring = RingContext(
            slots=self._slots,
            probes=probes,
            states=initial_ring_states(self._slots),
            shared_coolant=ring_cfg.shared_coolant,
        )
        self._supervisor = ATPESupervisor(
            self._ring,
            config=SupervisorConfig(
                enable_multi_horizon=False,  # on-demand via supervisor; keep vehicle loop light
            ),
        )
        self._pcmritms_brain_enabled = bool(ring_cfg.pcmritms_brain_enabled)
        self._closed_loop_surge = bool(ring_cfg.closed_loop_surge)
        self._prev_electric_w = 0.0
        self._last_measured_buffer_w = 0.0
        self.last_dispatch_mode = DispatchMode.OFF.value
        self.last_active_count = 0
        self.last_health_pct: tuple[float, ...] = ()
        self.last_optimization_score: float = 0.0
        self.last_buffer_burst_w: float | None = None
        self.last_buffer_assist_w: float = 0.0
        self.last_buffer_precharge_w: float = 0.0
        self.last_schedule_demand_w: float = 0.0
        self.assist_event_count: int = 0
        self.precharge_event_count: int = 0
        self.surge_event_count: int = 0

    @property
    def supervisor(self) -> ATPESupervisor:
        return self._supervisor

    @property
    def recharge_to_assist_ratio(self) -> float:
        return self._supervisor.pcmritms.recharge_to_assist_ratio

    @property
    def assist_energy_j(self) -> float:
        return self._supervisor.pcmritms.assist_energy_j

    @property
    def precharge_energy_j(self) -> float:
        return self._supervisor.pcmritms.precharge_energy_j

    @property
    def max_electric_w(self) -> float:
        return self._ring.max_power_w

    def _states_from_caches(self) -> tuple[CartridgeRuntimeState, ...]:
        out: list[CartridgeRuntimeState] = []
        for st, cache in zip(self._ring.states, self._caches):
            out.append(
                update_state_from_telemetry(
                    st,
                    wall_temp_k=cache.wall_temp_k,
                    generator_temp_k=cache.generator_temp_k,
                    valve_temp_k=cache.valve_temp_k,
                    generator_derate=cache.generator_derate,
                    mean_power_w=cache.nominal_power_w,
                    mean_capture_fraction=0.75,
                    mean_efficiency=cache.nominal_efficiency,
                    boundary_valid=True,
                    cycle_time_s=self._slots[st.slot_index].cfg.cycle_time_s,
                    cycles=0,
                )
            )
        return tuple(out)

    def observe_buffer_exchange(self, buffer_w: float) -> None:
        """Feed last plant buffer exchange into the next closed-loop cycle."""
        self._last_measured_buffer_w = float(buffer_w)

    def generate(
        self,
        setpoint_w: float,
        dt_s: float,
        *,
        bus_demand_w: float | None = None,
        buffer: BufferTelemetry | None = None,
        slow_setpoint_w: float | None = None,
    ) -> GenerationResult:
        setpoint_w = max(0.0, min(setpoint_w, self.max_electric_w))
        slew = self.cfg.max_slew_w_per_s
        if slew is not None:
            max_step = slew * dt_s
            if setpoint_w > self._prev_electric_w + max_step:
                setpoint_w = self._prev_electric_w + max_step
            elif setpoint_w < self._prev_electric_w - max_step:
                setpoint_w = self._prev_electric_w - max_step
        self._prev_electric_w = setpoint_w

        if setpoint_w <= 0.0:
            self.last_dispatch_mode = DispatchMode.OFF.value
            self.last_active_count = 0
            self.last_optimization_score = 0.0
            self.last_buffer_burst_w = None
            self.last_buffer_assist_w = 0.0
            self.last_buffer_precharge_w = 0.0
            self.last_schedule_demand_w = 0.0
            return GenerationResult(0.0, 0.0, 0.0, 0.0, "engine off", -1, 0.0)

        self._ring.states = self._states_from_caches()

        prev_mode = None
        if self.last_dispatch_mode not in ("", "off"):
            try:
                prev_mode = DispatchMode(self.last_dispatch_mode)
            except ValueError:
                prev_mode = None

        brain_buffer = None
        if self._pcmritms_brain_enabled and buffer is not None:
            brain_buffer = BufferTelemetry(
                soc=buffer.soc,
                max_discharge_w=buffer.max_discharge_w,
                max_charge_w=buffer.max_charge_w,
                peak_transient_w=buffer.peak_transient_w,
                soc_target=buffer.soc_target,
                reserve_floor=buffer.reserve_floor,
                measured_buffer_w=self._last_measured_buffer_w,
                closed_loop=self._closed_loop_surge,
            )

        result = self._supervisor.cycle(
            setpoint_w,
            dt_s,
            prev_mode=prev_mode,
            bus_demand_w=bus_demand_w,
            buffer=brain_buffer,
            slow_setpoint_w=slow_setpoint_w,
        )
        commands = result.commands

        self.last_dispatch_mode = commands.mode.value
        self.last_active_count = len(commands.enabled_indices)
        self.last_optimization_score = commands.score.total
        self.last_buffer_burst_w = commands.buffer_burst_w
        self.last_buffer_assist_w = commands.buffer_assist_w
        self.last_buffer_precharge_w = commands.buffer_precharge_w
        self.last_schedule_demand_w = commands.demand_w
        if result.buffer_plan.assist_event:
            self.assist_event_count += 1
        if result.buffer_plan.precharge_event:
            self.precharge_event_count += 1
        if result.buffer_plan.surge_event:
            self.surge_event_count += 1

        # Deliver toward the PCMRITMS-shaped schedule (assist / pre-charge),
        # not the raw vehicle setpoint alone.
        deliver_cap_w = max(setpoint_w, commands.target_power_w, commands.demand_w)

        electric_w = 0.0
        fuel_power_w = 0.0
        governing_tier = -1
        governing_name = "engine off"

        for idx in commands.enabled_indices:
            cache = self._cache_by_index[idx]
            scale = commands.load_scales.get(idx, 1.0)
            slot_elec = min(cache.nominal_power_w * scale, deliver_cap_w - electric_w)
            if slot_elec <= 0.0:
                continue
            eff = max(0.10, cache.nominal_efficiency * cache.generator_derate)
            electric_w += slot_elec
            fuel_power_w += slot_elec / eff
            if cache.tier_index > governing_tier:
                governing_tier = cache.tier_index
                governing_name = cache.tier_name

        efficiency = electric_w / fuel_power_w if fuel_power_w > 0.0 else 0.0
        fuel_energy_j = fuel_power_w * dt_s
        fuel_kg = fuel_energy_j / _LHV_J_PER_KG
        fuel_l = fuel_kg / GASOLINE_DENSITY_KG_PER_L
        co2_kg = fuel_l * GASOLINE_CO2_KG_PER_L

        self.last_health_pct = tuple(
            h.score_pct
            for h in result.health.scores
            if h.slot_index in commands.enabled_indices
        )

        return GenerationResult(
            electric_w=electric_w,
            fuel_power_w=fuel_power_w,
            fuel_l=fuel_l,
            co2_kg=co2_kg,
            active_tier=governing_name,
            active_index=governing_tier,
            efficiency=efficiency,
        )
