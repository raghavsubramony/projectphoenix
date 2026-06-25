"""Total cost of ownership + lifecycle CO2 rollup for the digital twin.

This turns the per-(body, cycle) fuel and durability metrics from the fleet
spine into ownership-level conclusions: what does the architecture cost to run,
and what is its true (well-to-wheel + embodied) carbon footprint, over a
vehicle lifetime?

Two architecture properties make the rollup interesting and are surfaced
explicitly:

* The pack is **small** (20 kWh) - so its embodied manufacturing CO2 is a small,
  quickly-amortized fixed cost, unlike a large-battery BEV.
* Charge-sustaining operation barely cycles the pack (see the durability study),
  so it is **never replaced** within a normal vehicle life - no mid-life battery
  cost or second embodied-CO2 hit.

All figures are representative 2026 values, declared in `EconomicsConfig` so they
are easy to override for a sensitivity study. Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass

from .fleet import FleetCell


# Default usage mix: how the lifetime mileage is split across the standard
# cycles. Towing is a small fraction of normal duty; urban/highway/mixed carry
# the rest. Weights are normalized, so they need not sum to exactly 1.0.
DEFAULT_USAGE_MIX: dict[str, float] = {
    "Urban": 0.30,
    "Highway": 0.35,
    "Mixed": 0.30,
    "Tow+grade": 0.05,
}


@dataclass(frozen=True)
class EconomicsConfig:
    """Representative 2026 cost + carbon parameters for the lifecycle rollup."""

    lifetime_km: float = 250_000.0
    fuel_price_per_l: float = 1.60          # pump price (currency-neutral)
    maintenance_per_km: float = 0.030       # tyres, fluids, service
    # Battery: a small LFP pack. Cost + embodied CO2 are per usable kWh.
    battery_capacity_kwh: float = 20.0
    battery_cost_per_kwh: float = 100.0     # 2026 LFP cell+pack
    battery_embodied_co2_kg_per_kwh: float = 60.0  # LFP, cradle-to-gate
    # Fuel carbon: tailpipe is already in the sim (2.31 kg/L); this adds the
    # upstream well-to-tank (extraction + refining + distribution) share.
    fuel_wtt_co2_kg_per_l: float = 0.50
    # Glider (everything except the battery) embodied CO2 - a fixed offset that
    # is identical across powertrains, shown for context only.
    glider_embodied_co2_t: float = 6.0


@dataclass(frozen=True)
class TcoResult:
    """Lifetime cost + lifecycle CO2 rollup for one vehicle body."""

    body: str
    lifetime_km: float
    blended_fuel_l_per_100km: float
    blended_co2_g_per_km: float          # tailpipe only (from the sim)
    # --- cost (currency-neutral) ---
    fuel_cost: float
    maintenance_cost: float
    battery_cost: float                  # initial pack + any replacements
    battery_replacements: int
    total_running_cost: float            # fuel + maintenance + replacements
    cost_per_km: float
    # --- lifecycle CO2 (tonnes) ---
    tailpipe_co2_t: float
    well_to_tank_co2_t: float
    embodied_battery_co2_t: float
    glider_co2_t: float
    lifecycle_co2_t: float
    lifecycle_co2_g_per_km: float        # all-in, per km

    def report(self) -> str:
        return "\n".join([
            f"=== TCO + lifecycle CO2: {self.body} "
            f"({self.lifetime_km/1000:.0f}k km) ===",
            f"  Blended economy     : {self.blended_fuel_l_per_100km:6.2f} "
            f"L/100km, {self.blended_co2_g_per_km:.0f} g/km tailpipe",
            f"  Fuel cost           : {self.fuel_cost:10.0f}",
            f"  Maintenance         : {self.maintenance_cost:10.0f}",
            f"  Battery (x{self.battery_replacements} replace): "
            f"{self.battery_cost:10.0f}",
            f"  Running cost / km   : {self.cost_per_km:10.3f}",
            f"  Lifecycle CO2       : {self.lifecycle_co2_t:8.2f} t "
            f"({self.lifecycle_co2_g_per_km:.0f} g/km all-in)",
            f"    tailpipe {self.tailpipe_co2_t:.2f} t | "
            f"well-to-tank {self.well_to_tank_co2_t:.2f} t | "
            f"battery {self.embodied_battery_co2_t:.2f} t | "
            f"glider {self.glider_co2_t:.2f} t",
        ])


def _blend(cells_by_cycle: dict[str, FleetCell], attr: str,
           mix: dict[str, float]) -> float:
    """Usage-weighted average of a metric across the cycles present."""
    total_w = 0.0
    acc = 0.0
    for cycle_short, weight in mix.items():
        cell = cells_by_cycle.get(cycle_short)
        if cell is None:
            continue
        acc += weight * getattr(cell, attr)
        total_w += weight
    return acc / total_w if total_w > 0 else 0.0


# The fleet uses long cycle names; map them to the short keys used in the mix.
_CYCLE_KEY: dict[str, str] = {
    "Urban stop-go": "Urban",
    "Highway cruise": "Highway",
    "Towing + 6% grade": "Tow+grade",
    "Mixed (urban+highway+sprint)": "Mixed",
}


def tco_for_body(cells: list[FleetCell], body: str,
                 econ: EconomicsConfig | None = None,
                 mix: dict[str, float] | None = None) -> TcoResult:
    """Roll a single body's per-cycle FleetCells up into a lifetime TCO."""
    econ = econ or EconomicsConfig()
    mix = mix or DEFAULT_USAGE_MIX
    by_cycle = {
        _CYCLE_KEY.get(c.cycle, c.cycle): c
        for c in cells if c.body == body
    }
    fuel_per_100 = _blend(by_cycle, "fuel_l_per_100km", mix)
    co2_g_km = _blend(by_cycle, "co2_g_per_km", mix)
    efc_per_100km = _blend(by_cycle, "battery_efc_per_100km", mix)

    km = econ.lifetime_km
    lifetime_fuel_l = fuel_per_100 / 100.0 * km

    # Battery replacements: cycle life converted to a distance, compared with the
    # vehicle life. Charge-sustaining keeps EFC/100km tiny, so this is ~0.
    pack_life_km = 0.0
    if efc_per_100km > 0:
        # 4000-EFC rating is the durability default; recover km from EFC/100km.
        pack_life_km = 4000.0 / efc_per_100km * 100.0
    replacements = int(km // pack_life_km) if pack_life_km > 0 else 0

    fuel_cost = lifetime_fuel_l * econ.fuel_price_per_l
    maintenance_cost = econ.maintenance_per_km * km
    battery_cost = (1 + replacements) * econ.battery_capacity_kwh \
        * econ.battery_cost_per_kwh
    running_cost = fuel_cost + maintenance_cost \
        + replacements * econ.battery_capacity_kwh * econ.battery_cost_per_kwh

    tailpipe_t = co2_g_km * km / 1e6
    wtt_t = lifetime_fuel_l * econ.fuel_wtt_co2_kg_per_l / 1000.0
    embodied_battery_t = (1 + replacements) * econ.battery_capacity_kwh \
        * econ.battery_embodied_co2_kg_per_kwh / 1000.0
    glider_t = econ.glider_embodied_co2_t
    lifecycle_t = tailpipe_t + wtt_t + embodied_battery_t + glider_t

    return TcoResult(
        body=body,
        lifetime_km=km,
        blended_fuel_l_per_100km=fuel_per_100,
        blended_co2_g_per_km=co2_g_km,
        fuel_cost=fuel_cost,
        maintenance_cost=maintenance_cost,
        battery_cost=battery_cost,
        battery_replacements=replacements,
        total_running_cost=running_cost,
        cost_per_km=running_cost / km if km > 0 else 0.0,
        tailpipe_co2_t=tailpipe_t,
        well_to_tank_co2_t=wtt_t,
        embodied_battery_co2_t=embodied_battery_t,
        glider_co2_t=glider_t,
        lifecycle_co2_t=lifecycle_t,
        lifecycle_co2_g_per_km=lifecycle_t * 1e6 / km if km > 0 else 0.0,
    )


def fleet_tco(cells: list[FleetCell],
              econ: EconomicsConfig | None = None,
              mix: dict[str, float] | None = None) -> list[TcoResult]:
    """Lifetime TCO + lifecycle CO2 for every body in the fleet cells."""
    bodies: list[str] = []
    for c in cells:
        if c.body not in bodies:
            bodies.append(c.body)
    return [tco_for_body(cells, b, econ, mix) for b in bodies]


def tco_table(results: list[TcoResult]) -> str:
    """Compact per-body TCO + lifecycle-CO2 comparison table."""
    lines = [
        "=== Fleet TCO + lifecycle CO2 ===",
        f"  {'Body':<12}{'L/100km':>9}{'Cost/km':>9}"
        f"{'Battery':>9}{'CO2 t':>8}{'g/km*':>8}",
        "  " + "-" * 56,
    ]
    for r in results:
        lines.append(
            f"  {r.body:<12}{r.blended_fuel_l_per_100km:>9.2f}"
            f"{r.cost_per_km:>9.3f}{r.battery_replacements:>8}x"
            f"{r.lifecycle_co2_t:>8.1f}{r.lifecycle_co2_g_per_km:>8.0f}")
    lines.append("  (* g/km = all-in lifecycle CO2; Battery = mid-life replacements)")
    return "\n".join(lines)
