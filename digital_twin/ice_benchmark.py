"""Move N -- conventional ICE benchmark: head-to-head against ATPE.

Every fuel, cost, and CO2 figure validated so far describes ATPE in isolation.
This Move answers the question every investor and engineer asks first:
*compared to what?* It runs an otherwise-identical vehicle -- same body, same
inertial buffer, same battery, same unified controller, same drive cycles --
with the only change being the engine: ATPE's three-tier free-piston stack
swapped for a conventional fixed-displacement turbo-petrol engine-generator
(`ice_engine.ICEEngine`).

Because `ICEEngine.generate` implements the exact same contract as
`atpe.ATPE.generate`, the substitution is a single constructor swap
(`ICEPowertrain` below) -- nothing about the vehicle, buffer, battery, or
control law changes. That is what makes the comparison honest: any fuel/CO2
delta is attributable to the *engine*, not to a confound elsewhere in the
stack.

This is read-only and additive: it does not alter `phase1_config_for` or any
validated ATPE figure. Pure standard library.
"""

from __future__ import annotations

from dataclasses import replace

from .battery import Battery
from .config import KW, PHASE1_BODIES, BodyStyle, TwinConfig, phase1_config_for
from .controller import UnifiedController
from .drive_cycles import DriveCycle
from .fleet import FleetCell, standard_cycles
from .ice_engine import ICEConfig, ICEEngine
from .pcmritms import InertialBuffer
from .powertrain import Powertrain, StepResult
from .simulation import run
from .vehicle import Vehicle


class ICEPowertrain(Powertrain):
    """Powertrain variant with a conventional ICE in place of ATPE.

    Subclasses `Powertrain` and overrides only the generator subsystem,
    reusing its `step()` unchanged. `self.atpe` is bound to `ICEEngine`
    instead of `atpe.ATPE`; `Powertrain.step()` only ever calls
    `self.atpe.generate(...)` and reads `self.atpe.max_electric_w`, both of
    which `ICEEngine` implements identically, so no other line of `step()`
    needs to know the difference.
    """

    def __init__(self, cfg: TwinConfig, ice_cfg: ICEConfig | None = None,
                 controller=None) -> None:
        self.cfg = cfg
        self.vehicle = Vehicle(cfg.vehicle)
        self.atpe = ICEEngine(ice_cfg or ICEConfig())  # type: ignore[assignment]
        self.buffer = InertialBuffer(cfg.buffer)
        self.battery = Battery(cfg.battery)
        self.controller = controller or UnifiedController(
            cfg.control,
            battery_soc_target=cfg.battery.soc_target,
            battery_soc_ev_floor=cfg.battery.soc_ev_floor,
        )
        self.time_s = 0.0


def _ice_cfg_for(body: BodyStyle) -> ICEConfig:
    """Size the ICE-generator's peak output to match ATPE's stack for `body`."""
    atpe_cfg = phase1_config_for(body).atpe
    return ICEConfig(peak_electric_w=atpe_cfg.max_electric_w)


def ice_config_for(body: BodyStyle = PHASE1_BODIES[0]) -> TwinConfig:
    """Body config identical to Phase-1 except it is built for `ICEPowertrain`.

    Reuses `phase1_config_for` verbatim (same vehicle/buffer/battery/control);
    the `atpe` field is unused by `ICEPowertrain` but left intact so the config
    remains a valid `TwinConfig` for any code that introspects it.
    """
    return phase1_config_for(body)


def charge_sustaining_ice_bodies(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
) -> dict[str, "callable"]:
    """Per-body ICEPowertrain builders, started at the charge-sustaining SoC."""
    builders = {}
    for body in bodies:
        cfg = phase1_config_for(body)
        cs_cfg = replace(cfg, battery=replace(
            cfg.battery, initial_soc=cfg.battery.soc_target))
        ice_cfg = _ice_cfg_for(body)
        builders[body.name] = (
            lambda c=cs_cfg, ic=ice_cfg: ICEPowertrain(c, ic))
    return builders


def run_ice_fleet(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycles: list[DriveCycle] | None = None,
) -> list[FleetCell]:
    """ICEPowertrain run over the standard cycle set, same FleetCell shape as ATPE."""
    cycles = cycles or standard_cycles()
    builders = charge_sustaining_ice_bodies(bodies)
    cells: list[FleetCell] = []
    for body_name, build in builders.items():
        for cycle in cycles:
            res = run(build(), cycle)
            eff_pct = res.mean_efficiency * 100.0
            cells.append(FleetCell(
                body=body_name,
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


def benchmark_table(atpe_cells: list[FleetCell],
                    ice_cells: list[FleetCell]) -> str:
    """Body x cycle fuel-economy comparison: ATPE vs conventional ICE.

    Mirrors fleet.fleet_delta's body/cycle grid layout for visual consistency
    with the rest of the report suite.
    """
    by_atpe = {(c.body, c.cycle): c for c in atpe_cells}
    by_ice = {(c.body, c.cycle): c for c in ice_cells}
    bodies: list[str] = []
    cycles: list[str] = []
    for c in atpe_cells:
        if c.body not in bodies:
            bodies.append(c.body)
        if c.cycle not in cycles:
            cycles.append(c.cycle)

    from .fleet import _short  # reuse the existing cycle-name shortener

    col_w = 22
    header = f"  {'Body':<12}" + "".join(f" {_short(c):>{col_w}}" for c in cycles)
    sep = "  " + "-" * (len(header) - 2)
    lines = [
        "=== ATPE vs conventional 2.0L turbo ICE: fuel L/100km "
        "(ATPE -> ICE, % saving) ===",
        header, sep,
    ]
    for body in bodies:
        row = f"  {body:<12}"
        for cyc in cycles:
            a = by_atpe.get((body, cyc))
            i = by_ice.get((body, cyc))
            if a is None or i is None:
                row += f" {'n/a':>{col_w}}"
                continue
            saving = (100.0 * (i.fuel_l_per_100km - a.fuel_l_per_100km)
                      / i.fuel_l_per_100km if i.fuel_l_per_100km > 0 else 0.0)
            cell = (f"{a.fuel_l_per_100km:.2f}->{i.fuel_l_per_100km:.2f} "
                    f"(-{saving:.0f}%)")
            row += f" {cell:>{col_w}}"
        lines.append(row)
    lines.append("  (ATPE fuel always lower than ICE -> negative number means "
                 "ATPE used MORE fuel)")
    return "\n".join(lines)


def benchmark_co2_table(atpe_cells: list[FleetCell],
                        ice_cells: list[FleetCell]) -> str:
    """Body x cycle CO2 comparison: ATPE vs conventional ICE."""
    by_atpe = {(c.body, c.cycle): c for c in atpe_cells}
    by_ice = {(c.body, c.cycle): c for c in ice_cells}
    bodies: list[str] = []
    cycles: list[str] = []
    for c in atpe_cells:
        if c.body not in bodies:
            bodies.append(c.body)
        if c.cycle not in cycles:
            cycles.append(c.cycle)

    from .fleet import _short

    col_w = 18
    header = f"  {'Body':<12}" + "".join(f" {_short(c):>{col_w}}" for c in cycles)
    sep = "  " + "-" * (len(header) - 2)
    lines = ["=== ATPE vs conventional ICE: CO2 g/km (ATPE -> ICE) ===",
             header, sep]
    for body in bodies:
        row = f"  {body:<12}"
        for cyc in cycles:
            a = by_atpe.get((body, cyc))
            i = by_ice.get((body, cyc))
            if a is None or i is None:
                row += f" {'n/a':>{col_w}}"
                continue
            cell = f"{a.co2_g_per_km:.0f}->{i.co2_g_per_km:.0f}"
            row += f" {cell:>{col_w}}"
        lines.append(row)
    return "\n".join(lines)


def benchmark_summary(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycles: list[DriveCycle] | None = None,
) -> str:
    """Full ATPE-vs-ICE report: fuel table + CO2 table + headline saving."""
    from .fleet import charge_sustaining_bodies, run_fleet

    atpe_cells = run_fleet(charge_sustaining_bodies(), cycles)
    ice_cells = run_ice_fleet(bodies, cycles)

    # Headline: mixed-cycle saving for the lead body (AWD SUV).
    lead = bodies[0].name
    mixed_atpe = next((c for c in atpe_cells
                       if c.body == lead and c.cycle.startswith("Mixed")), None)
    mixed_ice = next((c for c in ice_cells
                      if c.body == lead and c.cycle.startswith("Mixed")), None)
    headline = ""
    if mixed_atpe and mixed_ice and mixed_ice.fuel_l_per_100km > 0:
        saving = (100.0 * (mixed_ice.fuel_l_per_100km - mixed_atpe.fuel_l_per_100km)
                  / mixed_ice.fuel_l_per_100km)
        headline = (
            f"\n  Headline ({lead}, mixed cycle): ATPE "
            f"{mixed_atpe.fuel_l_per_100km:.2f} L/100km vs conventional ICE "
            f"{mixed_ice.fuel_l_per_100km:.2f} L/100km -> {saving:.1f}% fuel "
            f"saving, {mixed_atpe.co2_g_per_km:.0f} vs {mixed_ice.co2_g_per_km:.0f} "
            f"g/km CO2\n")

    return "\n\n".join([
        benchmark_table(atpe_cells, ice_cells),
        benchmark_co2_table(atpe_cells, ice_cells),
    ]) + headline


def wltp_benchmark(bodies: tuple[BodyStyle, ...] = PHASE1_BODIES) -> str:
    """ATPE vs ICE on the reconstructed WLTP Class 3 cycle (regulatory figure).

    The standard cycle set's "Urban stop-go" stays under the EV-mode threshold
    for the entire trip (a pre-existing ATPE behaviour, not introduced by this
    benchmark -- ATPE alone reports 0.00 L/100km on it too), so it is not a
    useful engine-efficiency comparison on its own. WLTP mixes low/medium/high/
    extra-high phases and is the figure that actually appears on a type-approval
    label, so it is the more honest headline for investor and regulatory use.
    """
    from .regulatory_cycles import RegulatoryCycles

    wltp = RegulatoryCycles.wltp().cycle
    return benchmark_summary(bodies, cycles=[wltp])
