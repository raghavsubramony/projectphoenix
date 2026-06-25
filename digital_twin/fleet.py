"""Fleet comparison harness: run every vehicle body over standard cycles.

This is the project's *data spine*. It evaluates the whole Phase-1 body lineup
across a fixed set of drive cycles and emits structured, comparable metrics so
that the effect of any change (e.g. enabling PCMRITMS rotor coupling) can be
measured per body rather than asserted. Every comparison is apples-to-apples:
the same cycles, the same charge state, only the thing under test differs.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from .config import TwinConfig, phase1_variants, phase1_config_for, PHASE1_BODIES
from .drive_cycles import DriveCycle, DriveCycles
from .powertrain import Powertrain
from .simulation import run

TwinBuilder = Callable[[], Powertrain]


def standard_cycles() -> list[DriveCycle]:
    """The fixed comparison cycle set (built once, reused for every body)."""
    return [
        DriveCycles.urban(),
        DriveCycles.highway(),
        DriveCycles.towing_grade(),
        DriveCycles.mixed(),
    ]


def stress_unmet_launch_kj(coupled: bool, energy_scale: float = 1.0,
                           battery_derate_w: float = 40_000.0,
                           slew_w_per_s: float = 60_000.0) -> float:
    """Unmet launch energy (kJ) for the lead body on the transient-stress cycle.

    Drives the Phase-1 lead body over the hard-launch stress cycle with a cold
    (power-limited) battery and a slew-limited engine, integrating the per-step
    capability shortfall. ``coupled`` enables PCMRITMS rotor coupling and
    ``energy_scale`` grows the inertial reservoir (a proxy for a larger rotor
    set). Shared by the demo runner and the rotor-scaling regression test so the
    headline numbers and the assertions cannot silently diverge.
    """
    cfg = phase1_config_for(PHASE1_BODIES[0], rotor_coupled=coupled)
    buf = replace(cfg.buffer, max_energy_j=cfg.buffer.max_energy_j * energy_scale)
    cfg = replace(
        cfg, buffer=buf,
        atpe=replace(cfg.atpe, max_slew_w_per_s=slew_w_per_s),
        battery=replace(cfg.battery, max_discharge_w=battery_derate_w,
                        initial_soc=cfg.battery.soc_target))
    twin = Powertrain(cfg)
    cyc = DriveCycles.transient_stress(dt_s=0.2)
    acc = cyc.accelerations()
    unmet_j = 0.0
    for i in range(len(cyc.speeds_ms)):
        rec = twin.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
        unmet_j += rec.shortfall_w * cyc.dt_s
    return unmet_j / 1000.0


def charge_sustaining_bodies(
    configs: dict[str, TwinConfig] | None = None,
    rotor_coupled: bool = False,
) -> dict[str, TwinBuilder]:
    """Per-body builders started at the charge-sustaining SoC target.

    Fuel economy is only representative when the engine (not a depleting
    battery) supplies trip energy, so the battery starts at its `soc_target`.
    When `rotor_coupled` is True (and `configs` is not overridden), the buffer
    carries the PCMRITMS rotor-derived brief-burst rating, for A/B studies.
    """
    configs = configs or phase1_variants(rotor_coupled=rotor_coupled)
    builders: dict[str, TwinBuilder] = {}
    for name, cfg in configs.items():
        cs_cfg = replace(cfg, battery=replace(cfg.battery,
                                              initial_soc=cfg.battery.soc_target))
        builders[name] = lambda c=cs_cfg: Powertrain(c)
    return builders


def stress_bodies(
    rotor_coupled: bool = False,
    slew_w_per_s: float = 60_000.0,
    battery_derate_w: float | None = None,
    thermal: bool = False,
) -> dict[str, TwinBuilder]:
    """Per-body builders configured for a realistic-transient stress test.

    Two physical constraints are imposed so the inertial buffer actually
    matters (instead of the engine instantly covering every spike):

    * `slew_w_per_s` caps how fast the ATPE generation can ramp - a real
      free-piston generator cannot step from idle to full power. The buffer
      must bridge the ramp.
    * `battery_derate_w` optionally caps battery discharge power (e.g. a cold or
      depleted pack). With the pack constrained, the buffer's brief-burst rating
      becomes the difference between meeting and missing a launch demand.

    When `rotor_coupled` is True the buffer carries the rotor-derived
    `peak_transient_w`; otherwise it uses its 90 kW continuous rating. When
    `thermal` is True the battery runs its lumped-thermal + derating model, so
    the pack's peak temperature and I^2R heat become observable.
    """
    from .config import with_battery_thermal
    builders: dict[str, TwinBuilder] = {}
    for name, cfg in phase1_variants(rotor_coupled=rotor_coupled).items():
        atpe = replace(cfg.atpe, max_slew_w_per_s=slew_w_per_s)
        battery = cfg.battery
        if battery_derate_w is not None:
            battery = replace(battery, max_discharge_w=battery_derate_w)
        battery = replace(battery, initial_soc=battery.soc_target)
        stress_cfg = replace(cfg, atpe=atpe, battery=battery)
        if thermal:
            stress_cfg = with_battery_thermal(stress_cfg)
        builders[name] = lambda c=stress_cfg: Powertrain(c)
    return builders


def closed_loop_rotor_bodies(
    slew_w_per_s: float = 60_000.0,
    battery_derate_w: float | None = 40_000.0,
    reserve_floor: float = 0.15,
) -> dict[str, TwinBuilder]:
    """Stress-test builders that install the closed-loop rotor controller.

    Identical hardware to ``stress_bodies(rotor_coupled=True, ...)`` - the buffer
    still carries the rotor-derived surge ceiling - but a
    :class:`ClosedLoopRotorController` gates that surge so the reservoir is only
    spent on genuine launches. The A/B against static coupling shows whether
    smarter surge timing preserves more reserve (higher ``buffer_min_soc``) at
    equal-or-better shortfall performance.
    """
    from .config import phase1_variants
    from .controller import ClosedLoopRotorController
    builders: dict[str, TwinBuilder] = {}
    for name, cfg in phase1_variants(rotor_coupled=True).items():
        atpe = replace(cfg.atpe, max_slew_w_per_s=slew_w_per_s)
        battery = cfg.battery
        if battery_derate_w is not None:
            battery = replace(battery, max_discharge_w=battery_derate_w)
        battery = replace(battery, initial_soc=battery.soc_target)
        cl_cfg = replace(cfg, atpe=atpe, battery=battery)
        surge_w = cl_cfg.buffer.peak_transient_w or cl_cfg.buffer.max_discharge_w
        cont_w = cl_cfg.buffer.max_discharge_w
        assist_w = cl_cfg.battery.max_discharge_w

        def _build(c=cl_cfg, surge=surge_w, cont=cont_w, assist=assist_w) -> Powertrain:
            ctrl = ClosedLoopRotorController(
                c.control,
                battery_soc_target=c.battery.soc_target,
                battery_soc_ev_floor=c.battery.soc_ev_floor,
                surge_ceiling_w=surge,
                continuous_rating_w=cont,
                battery_assist_w=assist,
                reserve_floor=reserve_floor,
            )
            return Powertrain(c, controller=ctrl)

        builders[name] = _build
    return builders


@dataclass(frozen=True)
class FleetCell:
    """One body evaluated on one cycle: the comparable metric bundle."""

    body: str
    cycle: str
    fuel_l_per_100km: float
    equiv_fuel_l_per_100km: float
    co2_g_per_km: float
    mean_efficiency: float
    buffer_throughput_kj: float
    buffer_peak_kw: float
    buffer_min_soc: float
    battery_peak_kw: float
    battery_throughput_kj: float
    battery_peak_temp_c: float
    battery_efc_per_100km: float
    projected_pack_life_km: float
    shortfall_events: int
    max_shortfall_kw: float
    net_battery_kwh: float


# Metric metadata: attribute -> (column label, format, lower-is-better).
_METRICS: dict[str, tuple[str, str, bool]] = {
    "fuel_l_per_100km": ("Fuel L/100km", "{:.2f}", True),
    "equiv_fuel_l_per_100km": ("Eq.Fuel L/100", "{:.2f}", True),
    "co2_g_per_km": ("CO2 g/km", "{:.0f}", True),
    "mean_efficiency": ("ATPE eff %", "{:.1f}", False),
    "buffer_throughput_kj": ("Buffer kJ", "{:.0f}", False),
    "buffer_peak_kw": ("Buffer pk kW", "{:.1f}", False),
    "buffer_min_soc": ("Buf min SoC", "{:.2f}", False),
    "battery_peak_kw": ("Batt pk kW", "{:.1f}", True),
    "battery_throughput_kj": ("Batt kJ", "{:.0f}", True),
    "battery_peak_temp_c": ("Batt pk C", "{:.1f}", True),
    "battery_efc_per_100km": ("EFC/100km", "{:.3f}", True),
    "projected_pack_life_km": ("Life kkm", "{:.0f}", False),
    "shortfall_events": ("Shortfalls", "{:.0f}", True),
    "max_shortfall_kw": ("Max short kW", "{:.1f}", True),
    "net_battery_kwh": ("Net batt kWh", "{:+.3f}", True),
}

# Compact column labels for the long synthetic-cycle names.
_CYCLE_SHORT: dict[str, str] = {
    "Urban stop-go": "Urban",
    "Highway cruise": "Highway",
    "Towing + 6% grade": "Tow+grade",
    "Mixed (urban+highway+sprint)": "Mixed",
}


def _short(cycle_name: str) -> str:
    return _CYCLE_SHORT.get(cycle_name, cycle_name[:10])


def run_fleet(build_twins: dict[str, TwinBuilder],
              cycles: list[DriveCycle] | None = None) -> list[FleetCell]:
    """Run every body over every cycle; return one FleetCell per (body, cycle)."""
    cycles = cycles or standard_cycles()
    cells: list[FleetCell] = []
    for body, build in build_twins.items():
        for cycle in cycles:
            res = run(build(), cycle)
            eff_pct = res.mean_efficiency * 100.0
            cells.append(FleetCell(
                body=body,
                cycle=cycle.name,
                fuel_l_per_100km=res.fuel_l_per_100km,
                equiv_fuel_l_per_100km=res.equiv_fuel_l_per_100km,
                co2_g_per_km=res.co2_g_per_km,
                mean_efficiency=eff_pct,
                buffer_throughput_kj=res.buffer_throughput_kj,
                buffer_peak_kw=res.buffer_peak_kw,
                buffer_min_soc=res.buffer_min_soc,
                battery_peak_kw=res.battery_peak_kw,
                battery_throughput_kj=res.battery_throughput_kj,
                battery_peak_temp_c=res.battery_peak_temp_c,
                battery_efc_per_100km=res.battery_efc_per_100km,
                projected_pack_life_km=res.projected_pack_life_km / 1000.0,
                shortfall_events=res.shortfall_events,
                max_shortfall_kw=res.max_shortfall_kw,
                net_battery_kwh=res.net_battery_kwh,
            ))
    return cells


def _cycle_order(cells: list[FleetCell]) -> list[str]:
    seen: list[str] = []
    for c in cells:
        if c.cycle not in seen:
            seen.append(c.cycle)
    return seen


def _body_order(cells: list[FleetCell]) -> list[str]:
    seen: list[str] = []
    for c in cells:
        if c.body not in seen:
            seen.append(c.body)
    return seen


def fleet_table(cells: list[FleetCell], metric: str) -> str:
    """Body x cycle grid for a single metric."""
    label, fmt, _ = _METRICS[metric]
    bodies = _body_order(cells)
    cycles = _cycle_order(cells)
    index = {(c.body, c.cycle): c for c in cells}

    col_w = 10
    header = f"  {'Body':<12}" + "".join(f" {_short(c):>{col_w}}" for c in cycles)
    sep = "  " + "-" * (len(header) - 2)
    lines = [f"=== Fleet: {label} ===", header, sep]
    for body in bodies:
        row = f"  {body:<12}"
        for cyc in cycles:
            cell = index[(body, cyc)]
            val = fmt.format(getattr(cell, metric))
            row += f" {val:>{col_w}}"
        lines.append(row)
    return "\n".join(lines)


def fleet_report(build_twins: dict[str, TwinBuilder],
                 cycles: list[DriveCycle] | None = None,
                 metrics: list[str] | None = None) -> str:
    """Full multi-metric fleet report (one table per metric)."""
    cells = run_fleet(build_twins, cycles)
    metrics = metrics or ["fuel_l_per_100km", "co2_g_per_km",
                          "buffer_throughput_kj", "shortfall_events"]
    return "\n\n".join(fleet_table(cells, m) for m in metrics)


def fleet_delta(cells_before: list[FleetCell], cells_after: list[FleetCell],
                metric: str) -> str:
    """Per (body, cycle) change in a metric: after - before.

    Used for A/B studies (e.g. rotor coupling on vs off). A leading +/- sign and
    a directional flag (better/worse/same) make the impact explicit.
    """
    label, fmt, lower_is_better = _METRICS[metric]
    before = {(c.body, c.cycle): c for c in cells_before}
    after = {(c.body, c.cycle): c for c in cells_after}
    bodies = _body_order(cells_before)
    cycles = _cycle_order(cells_before)

    col_w = 12
    header = f"  {'Body':<12}" + "".join(f" {_short(c):>{col_w}}" for c in cycles)
    sep = "  " + "-" * (len(header) - 2)
    lines = [f"=== Fleet delta (after - before): {label} ===", header, sep]
    for body in bodies:
        row = f"  {body:<12}"
        for cyc in cycles:
            key = (body, cyc)
            if key not in before or key not in after:
                row += f" {'n/a':>{col_w}}"
                continue
            d = getattr(after[key], metric) - getattr(before[key], metric)
            if abs(d) < 1e-9:
                tag = "="
            elif (d < 0) == lower_is_better:
                tag = "v"  # improved
            else:
                tag = "^"  # worsened
            cell = f"{fmt.format(d)}{tag}"
            row += f" {cell:>{col_w}}"
        lines.append(row)
    lines.append("  (v = better, ^ = worse, = = no change)")
    return "\n".join(lines)
