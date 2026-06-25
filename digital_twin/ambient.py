"""Move H -- ambient temperature stress (-10 C to +40 C).

The validated fuel / CO2 / durability figures are all quoted at a benign
reference temperature. Real vehicles live between a -10 C winter morning and a
+40 C summer afternoon, and three physical effects move the numbers:

1. **Air density.** Cold air is denser, so aerodynamic drag rises in winter and
   falls in summer. Drag force is ``0.5 * rho * Cd * A * v^2`` and density scales
   as ``rho(T) = rho_ref * T_ref / T`` (ideal gas, absolute temperature), so the
   density ratio folds exactly into an equivalent drag-coefficient scaling.
2. **HVAC.** A cabin must be heated when it is cold and cooled when it is hot, so
   the accessory (aux) load on the DC bus is a V-shape around a comfort point --
   zero at ~21 C, rising in both directions (heating is modelled as cheaper per
   degree than air-conditioning).
3. **Battery thermal.** The pack starts and sits at ambient, so a hot day raises
   the cell temperature toward the battery-management derate threshold while a
   cold day keeps it comfortably clear.

This is a **read-only, opt-in** study: every run is built on a *copy* of the
validated body config (charge-sustaining, so fuel is compared cleanly), and the
default configuration is never touched -- so no validated number can regress.
Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import (
    PHASE1_BODIES,
    BatteryThermalConfig,
    BodyStyle,
    phase1_config_for,
)
from .drive_cycles import DriveCycle, DriveCycles
from .powertrain import Powertrain
from .simulation import run

_KELVIN = 273.15

#: Representative ambient sweep, a cold winter morning to a hot summer afternoon.
DEFAULT_TEMPS_C: tuple[float, ...] = (-10.0, 0.0, 10.0, 20.0, 30.0, 40.0)


@dataclass(frozen=True)
class AmbientConfig:
    """Tunable ambient-effect model (all representative real-world values)."""

    hvac_comfort_c: float = 21.0      # cabin null point: no heating or cooling
    hvac_heat_w_per_k: float = 60.0   # resistive/heat-pump cabin heating
    hvac_cool_w_per_k: float = 90.0   # air-conditioning compressor (costlier)
    hvac_max_w: float = 2500.0        # accessory load ceiling
    air_reference_c: float = 15.0     # density reference (matches AIR_DENSITY)
    model_battery_thermal: bool = True


@dataclass(frozen=True)
class AmbientPoint:
    """One body on one cycle at one ambient temperature."""

    body: str
    temp_c: float
    fuel_l_per_100km: float
    co2_g_per_km: float
    hvac_load_w: float
    air_density_factor: float
    battery_peak_temp_c: float
    derate_start_c: float
    shortfall_events: int

    @property
    def derated(self) -> bool:
        """True if the pack reached its battery-management derate threshold."""
        return self.battery_peak_temp_c >= self.derate_start_c


@dataclass(frozen=True)
class AmbientSweep:
    """A body's response across an ambient-temperature sweep on one cycle."""

    body: str
    cycle: str
    reference_c: float
    points: tuple[AmbientPoint, ...]

    def at(self, temp_c: float) -> AmbientPoint:
        return min(self.points, key=lambda p: abs(p.temp_c - temp_c))

    @property
    def reference(self) -> AmbientPoint:
        return self.at(self.reference_c)

    @property
    def fuel_swing_pct(self) -> float:
        """Cold-to-hot fuel spread as a percentage of the reference point."""
        lo = min(p.fuel_l_per_100km for p in self.points)
        hi = max(p.fuel_l_per_100km for p in self.points)
        ref = self.reference.fuel_l_per_100km
        return 100.0 * (hi - lo) / ref if ref else 0.0

    @property
    def any_derate(self) -> bool:
        return any(p.derated for p in self.points)

    def report(self) -> str:
        lines = [
            f"=== {self.body} | {self.cycle}: ambient sweep ===",
            "  Temp   Fuel        CO2     HVAC   Air    Pack pk  Derate",
            "  ----------------------------------------------------------",
        ]
        for p in self.points:
            lines.append(
                f"  {p.temp_c:+5.0f}C {p.fuel_l_per_100km:6.2f} L "
                f"{p.co2_g_per_km:6.1f} {p.hvac_load_w/1000:5.2f}kW "
                f"{p.air_density_factor:5.2f}x {p.battery_peak_temp_c:6.1f}C "
                f"{'  yes' if p.derated else '   no'}")
        lines.append(
            f"  cold->hot fuel swing: {self.fuel_swing_pct:.1f}% of reference "
            f"({self.reference_c:+.0f}C); thermal derate ever: "
            f"{'yes' if self.any_derate else 'no'}")
        return "\n".join(lines)


def air_density_factor(temp_c: float, reference_c: float = 15.0) -> float:
    """Air-density ratio rho(T)/rho(ref) via the ideal-gas law (absolute T)."""
    return (reference_c + _KELVIN) / (temp_c + _KELVIN)


def hvac_load_w(temp_c: float, ambient: AmbientConfig | None = None) -> float:
    """V-shaped cabin HVAC accessory load (W) around the comfort point."""
    a = ambient or AmbientConfig()
    if temp_c < a.hvac_comfort_c:
        load = (a.hvac_comfort_c - temp_c) * a.hvac_heat_w_per_k
    else:
        load = (temp_c - a.hvac_comfort_c) * a.hvac_cool_w_per_k
    return min(load, a.hvac_max_w)


def _config_at_temp(body: BodyStyle, temp_c: float, ambient: AmbientConfig):
    """Build a charge-sustaining copy of `body`'s config at `temp_c`."""
    base = phase1_config_for(body)
    factor = air_density_factor(temp_c, ambient.air_reference_c)
    hvac = hvac_load_w(temp_c, ambient)
    veh = replace(
        base.vehicle,
        drag_coefficient=base.vehicle.drag_coefficient * factor,
        aux_load_w=base.vehicle.aux_load_w + hvac,
    )
    batt = replace(base.battery, initial_soc=base.battery.soc_target)
    if ambient.model_battery_thermal:
        batt = replace(
            batt,
            thermal=BatteryThermalConfig(
                ambient_temp_c=temp_c, initial_temp_c=temp_c),
        )
    return replace(base, vehicle=veh, battery=batt)


def simulate_at_temp(
    body: BodyStyle = PHASE1_BODIES[0],
    cycle: DriveCycle | None = None,
    temp_c: float = 20.0,
    ambient: AmbientConfig | None = None,
) -> AmbientPoint:
    """Run `body` on `cycle` at ambient `temp_c` and summarise the response."""
    a = ambient or AmbientConfig()
    cyc = cycle or DriveCycles.mixed()
    cfg = _config_at_temp(body, temp_c, a)
    res = run(Powertrain(cfg), cyc)
    derate_start = (cfg.battery.thermal.derate_start_c
                    if cfg.battery.thermal else float("inf"))
    # Report the charge-sustaining-equivalent fuel (SoC-corrected) and scale
    # CO2 to match it, so both columns describe the same energy balance.
    equiv = res.equiv_fuel_l_per_100km
    co2 = (res.co2_g_per_km * equiv / res.fuel_l_per_100km
           if res.fuel_l_per_100km > 0 else res.co2_g_per_km)
    return AmbientPoint(
        body=body.name,
        temp_c=temp_c,
        fuel_l_per_100km=equiv,
        co2_g_per_km=co2,
        hvac_load_w=hvac_load_w(temp_c, a),
        air_density_factor=air_density_factor(temp_c, a.air_reference_c),
        battery_peak_temp_c=res.battery_peak_temp_c,
        derate_start_c=derate_start,
        shortfall_events=res.shortfall_events,
    )


def ambient_sweep(
    body: BodyStyle = PHASE1_BODIES[0],
    cycle: DriveCycle | None = None,
    temps_c: tuple[float, ...] = DEFAULT_TEMPS_C,
    ambient: AmbientConfig | None = None,
) -> AmbientSweep:
    """Sweep `body` across `temps_c` on one cycle."""
    a = ambient or AmbientConfig()
    cyc = cycle or DriveCycles.mixed()
    points = tuple(
        simulate_at_temp(body, cyc, t, a) for t in temps_c)
    return AmbientSweep(
        body=body.name,
        cycle=cyc.name,
        reference_c=a.air_reference_c,
        points=points,
    )


def fleet_ambient(
    temps_c: tuple[float, ...] = DEFAULT_TEMPS_C,
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycle: DriveCycle | None = None,
    ambient: AmbientConfig | None = None,
) -> list[AmbientSweep]:
    """Ambient sweep for every body on the same cycle."""
    cyc = cycle or DriveCycles.mixed()
    return [ambient_sweep(b, cyc, temps_c, ambient) for b in bodies]


def ambient_table(sweeps: list[AmbientSweep]) -> str:
    """Compact cold/reference/hot fuel comparison across the fleet."""
    cold = min(s.points[0].temp_c for s in sweeps) if sweeps else 0.0
    hot = max(s.points[-1].temp_c for s in sweeps) if sweeps else 0.0
    lines = [
        f"=== Fleet ambient sensitivity ({cold:+.0f}C to {hot:+.0f}C) ===",
        f"  Body          Cold L  Ref L   Hot L   Swing  Derate",
        "  -------------------------------------------------------",
    ]
    for s in sweeps:
        c = s.points[0].fuel_l_per_100km
        h = s.points[-1].fuel_l_per_100km
        r = s.reference.fuel_l_per_100km
        lines.append(
            f"  {s.body:<12} {c:6.2f}  {r:6.2f}  {h:6.2f}  "
            f"{s.fuel_swing_pct:5.1f}%  {'yes' if s.any_derate else 'no'}")
    lines.append("  (charge-sustaining fuel L/100km; cold air + heating raise "
                 "winter demand)")
    return "\n".join(lines)
