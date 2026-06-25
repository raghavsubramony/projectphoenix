"""Move L -- grid-charging (PHEV) economics.

Move D priced the architecture as a pure series hybrid: the engine supplies all
trip energy and the battery is only a buffer. But the 20 kWh pack is big enough
to drive a meaningful distance on **grid electricity** if the owner plugs in --
that is the plug-in-hybrid (PHEV) use case. This Move compares the two energy
sources honestly.

The model follows the standard PHEV split (cf. SAE J2841):

* **Charge-depleting (CD) range.** Measured from a fully-electric urban run: the
  battery's usable depletion window divided by the electrical consumption gives
  the distance the car can drive on a charge.
* **Utility factor (UF).** For a given average daily distance, the fraction of
  driving that falls inside the CD range and therefore runs on grid energy. The
  rest runs charge-sustaining, on fuel (the validated Move-D figure).
* **Blended cost & CO2.** A UF-weighted mix of grid electricity (priced and
  carbon-rated by ``GridConfig``) and fuel (priced and carbon-rated by the
  existing ``EconomicsConfig``).

It is **read-only**: it runs copies of the validated configs and reuses the
Move-D economics parameters, so no validated number changes. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for
from .drive_cycles import DriveCycle, DriveCycles
from .economics import EconomicsConfig
from .fleet import FleetCell, run_fleet, charge_sustaining_bodies
from .powertrain import Powertrain
from .simulation import run

_CO2_TAILPIPE_KG_PER_L = 2.31  # gasoline tailpipe (matches the sim)


@dataclass(frozen=True)
class GridConfig:
    """Electricity price + carbon, and the owner's daily driving distance."""

    elec_price_per_kwh: float = 0.30     # 2026 domestic tariff (currency-neutral)
    grid_co2_kg_per_kwh: float = 0.30    # mixed-grid intensity
    charge_efficiency: float = 0.90      # wall -> pack round trip
    daily_km: float = 50.0               # average daily driving distance
    # SoC window available for grid depletion (start -> EV floor).
    soc_start: float = 0.80
    soc_floor: float = 0.25


@dataclass(frozen=True)
class PhevResult:
    """Plug-in vs fuel-only economics for one body."""

    body: str
    ev_kwh_per_100km: float          # charge-depleting electrical consumption
    cd_range_km: float               # distance on one full charge
    utility_factor: float            # share of daily driving on grid energy
    fuel_l_per_100km: float          # charge-sustaining fuel (Move D)
    # Fuel-only baseline (no plugging in).
    fuel_only_cost_per_km: float
    fuel_only_co2_g_per_km: float
    # Plug-in blend (grid + fuel by utility factor).
    phev_cost_per_km: float
    phev_co2_g_per_km: float

    @property
    def cost_saving_pct(self) -> float:
        b = self.fuel_only_cost_per_km
        return 100.0 * (b - self.phev_cost_per_km) / b if b else 0.0

    @property
    def co2_saving_pct(self) -> float:
        b = self.fuel_only_co2_g_per_km
        return 100.0 * (b - self.phev_co2_g_per_km) / b if b else 0.0


def _utility_factor(cd_range_km: float, daily_km: float) -> float:
    """Share of distance run on grid energy for a given daily distance.

    A transparent saturating approximation: a daily trip well inside the CD
    range is almost all-electric; a trip far beyond it is mostly fuel.
    """
    if daily_km <= 0.0:
        return 0.0
    return min(1.0, cd_range_km / daily_km)


def _ev_consumption_kwh_per_100km(body: BodyStyle, grid: GridConfig) -> float:
    """Charge-depleting electrical consumption from a fully-electric urban run."""
    cfg = phase1_config_for(body)
    res = run(Powertrain(cfg), DriveCycles.urban())
    if res.distance_km <= 0.0:
        return 0.0
    return -res.net_battery_kwh / res.distance_km * 100.0


def phev_for_body(
    body: BodyStyle = PHASE1_BODIES[0],
    cells: list[FleetCell] | None = None,
    grid: GridConfig | None = None,
    econ: EconomicsConfig | None = None,
) -> PhevResult:
    """Compare plug-in (grid + fuel) vs fuel-only economics for `body`."""
    g = grid or GridConfig()
    e = econ or EconomicsConfig()
    cells = cells if cells is not None else run_fleet(charge_sustaining_bodies())

    ev_kwh = _ev_consumption_kwh_per_100km(body, g)
    usable_kwh = (g.soc_start - g.soc_floor) * e.battery_capacity_kwh
    cd_range = usable_kwh / ev_kwh * 100.0 if ev_kwh > 0 else 0.0
    uf = _utility_factor(cd_range, g.daily_km)

    # Charge-sustaining fuel for this body, blended across the usage mix would be
    # ideal, but the mixed cycle is the representative single number here.
    mixed = next((c for c in cells
                  if c.body == body.name and c.cycle.startswith("Mixed")), None)
    fuel_per_100 = mixed.fuel_l_per_100km if mixed else 0.0

    # Fuel-only baseline.
    fuel_cost_km = fuel_per_100 / 100.0 * e.fuel_price_per_l
    fuel_co2_km = (fuel_per_100 / 100.0
                   * (_CO2_TAILPIPE_KG_PER_L + e.fuel_wtt_co2_kg_per_l) * 1000.0)

    # Grid portion (wall energy includes charge losses).
    grid_cost_km = (ev_kwh / 100.0 / g.charge_efficiency * g.elec_price_per_kwh)
    grid_co2_km = (ev_kwh / 100.0 / g.charge_efficiency
                   * g.grid_co2_kg_per_kwh * 1000.0)

    phev_cost_km = uf * grid_cost_km + (1.0 - uf) * fuel_cost_km
    phev_co2_km = uf * grid_co2_km + (1.0 - uf) * fuel_co2_km

    return PhevResult(
        body=body.name,
        ev_kwh_per_100km=ev_kwh,
        cd_range_km=cd_range,
        utility_factor=uf,
        fuel_l_per_100km=fuel_per_100,
        fuel_only_cost_per_km=fuel_cost_km,
        fuel_only_co2_g_per_km=fuel_co2_km,
        phev_cost_per_km=phev_cost_km,
        phev_co2_g_per_km=phev_co2_km,
    )


def fleet_phev(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    grid: GridConfig | None = None,
    econ: EconomicsConfig | None = None,
) -> list[PhevResult]:
    """Plug-in vs fuel-only economics for every body."""
    cells = run_fleet(charge_sustaining_bodies())
    return [phev_for_body(b, cells, grid, econ) for b in bodies]


def phev_table(results: list[PhevResult], grid: GridConfig | None = None) -> str:
    """Compact plug-in vs fuel-only cost/CO2 comparison across the fleet."""
    g = grid or GridConfig()
    lines = [
        f"=== Plug-in (PHEV) vs fuel-only economics ({g.daily_km:.0f} km/day) ===",
        "  Body          EV/100  Range   UF    Cost/km        CO2 g/km",
        "                 kWh     km           fuel->phev    fuel->phev",
        "  --------------------------------------------------------------",
    ]
    for r in results:
        lines.append(
            f"  {r.body:<12} {r.ev_kwh_per_100km:5.1f}  {r.cd_range_km:5.0f}  "
            f"{r.utility_factor:4.2f}  "
            f"{r.fuel_only_cost_per_km:.3f}->{r.phev_cost_per_km:.3f}  "
            f"{r.fuel_only_co2_g_per_km:5.0f}->{r.phev_co2_g_per_km:5.0f}")
    lines.append("  (UF = share of daily km on grid energy; lower daily_km "
                 "= more electric)")
    return "\n".join(lines)
