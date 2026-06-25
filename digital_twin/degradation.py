"""Move M -- drivetrain degradation over life.

Every figure so far describes a *new* vehicle. Over a quarter-million-kilometre
life the battery loses usable capacity, its internal resistance grows, and the
mechanical driveline loses a little efficiency to wear. This Move ages the
drivetrain and re-measures: how much does fuel economy drift and how much
charge-depleting range is lost from new to end-of-life?

The ageing model is deliberately simple and transparent, scaled by a *life
fraction* (0 = new, 1 = end of life):

* **Capacity fade** -- usable pack energy shrinks linearly toward
  ``capacity_fade_eol`` at end of life.
* **Resistance growth** -- internal resistance rises, raising the EV-mode
  consumption a little.
* **Driveline wear** -- driveline efficiency drops by ``driveline_drop_eol``
  toward end of life, so more energy reaches the wheels as loss.

It is **read-only**: each life point is a fresh copy of the validated config, so
the new-vehicle numbers (life fraction 0) reproduce the validated figures
exactly. Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for
from .drive_cycles import DriveCycles
from .economics import EconomicsConfig
from .powertrain import Powertrain
from .simulation import run


@dataclass(frozen=True)
class DegradationConfig:
    """How the drivetrain ages from new (0) to end-of-life (1)."""

    capacity_fade_eol: float = 0.20      # usable kWh lost at end of life
    resistance_growth_eol: float = 0.50  # internal resistance +50% at EOL
    driveline_drop_eol: float = 0.03     # driveline efficiency loss at EOL
    life_fractions: tuple[float, ...] = (0.0, 0.5, 1.0)
    soc_start: float = 0.80
    soc_floor: float = 0.25


@dataclass(frozen=True)
class DegradationPoint:
    """The drivetrain's condition at one point in its life."""

    body: str
    life_fraction: float
    odometer_km: float
    capacity_kwh: float
    driveline_efficiency: float
    fuel_l_per_100km: float
    ev_kwh_per_100km: float
    ev_range_km: float


@dataclass(frozen=True)
class DegradationCurve:
    """A body's drift from new to end-of-life."""

    body: str
    points: tuple[DegradationPoint, ...]

    @property
    def new(self) -> DegradationPoint:
        return self.points[0]

    @property
    def eol(self) -> DegradationPoint:
        return self.points[-1]

    @property
    def fuel_drift_pct(self) -> float:
        n = self.new.fuel_l_per_100km
        return 100.0 * (self.eol.fuel_l_per_100km - n) / n if n > 0 else 0.0

    @property
    def range_loss_pct(self) -> float:
        n = self.new.ev_range_km
        return 100.0 * (n - self.eol.ev_range_km) / n if n > 0 else 0.0

    def report(self) -> str:
        lines = [
            f"=== {self.body}: drivetrain ageing (new -> end of life) ===",
            "  Life   Odo km   Cap kWh  D-eff   Fuel   EV km",
            "  ------------------------------------------------",
        ]
        for p in self.points:
            lines.append(
                f"  {p.life_fraction:4.0%}  {p.odometer_km:>7.0f}  "
                f"{p.capacity_kwh:6.1f}  {p.driveline_efficiency:5.3f}  "
                f"{p.fuel_l_per_100km:5.2f}  {p.ev_range_km:5.0f}")
        lines.append(
            f"  new -> EOL: fuel +{self.fuel_drift_pct:.1f}%, "
            f"EV range -{self.range_loss_pct:.1f}%")
        return "\n".join(lines)


def _aged_config(body: BodyStyle, frac: float, deg: DegradationConfig,
                 econ: EconomicsConfig):
    base = phase1_config_for(body)
    cap = base.battery.usable_capacity_j * (1.0 - deg.capacity_fade_eol * frac)
    batt_kwargs = dict(usable_capacity_j=cap, initial_soc=base.battery.soc_target)
    if base.battery.thermal is not None:
        r = base.battery.thermal.internal_resistance_ohm * (
            1.0 + deg.resistance_growth_eol * frac)
        thermal = replace(base.battery.thermal, internal_resistance_ohm=r)
        batt_kwargs["thermal"] = thermal
    batt = replace(base.battery, **batt_kwargs)
    deff = base.vehicle.driveline_efficiency * (1.0 - deg.driveline_drop_eol * frac)
    veh = replace(base.vehicle, driveline_efficiency=deff)
    return replace(base, battery=batt, vehicle=veh)


def degradation_for(
    body: BodyStyle = PHASE1_BODIES[0],
    deg: DegradationConfig | None = None,
    econ: EconomicsConfig | None = None,
) -> DegradationCurve:
    """Age `body` across its life and report fuel/range drift."""
    d = deg or DegradationConfig()
    e = econ or EconomicsConfig()
    points: list[DegradationPoint] = []
    for frac in d.life_fractions:
        # Fuel economy: charge-sustaining mixed cycle on the aged config.
        cs_cfg = _aged_config(body, frac, d, e)
        fuel = run(Powertrain(cs_cfg), DriveCycles.mixed()).fuel_l_per_100km

        # EV consumption + range: charge-depleting urban on the aged config.
        ev_cfg = replace(cs_cfg, battery=replace(cs_cfg.battery,
                                                 initial_soc=d.soc_start))
        ev_res = run(Powertrain(ev_cfg), DriveCycles.urban())
        ev_kwh = (-ev_res.net_battery_kwh / ev_res.distance_km * 100.0
                  if ev_res.distance_km > 0 else 0.0)
        cap_kwh = cs_cfg.battery.usable_capacity_j / 3.6e6
        usable = (d.soc_start - d.soc_floor) * cap_kwh
        ev_range = usable / ev_kwh * 100.0 if ev_kwh > 0 else 0.0

        points.append(DegradationPoint(
            body=body.name,
            life_fraction=frac,
            odometer_km=e.lifetime_km * frac,
            capacity_kwh=cap_kwh,
            driveline_efficiency=cs_cfg.vehicle.driveline_efficiency,
            fuel_l_per_100km=fuel,
            ev_kwh_per_100km=ev_kwh,
            ev_range_km=ev_range,
        ))
    return DegradationCurve(body=body.name, points=tuple(points))


def fleet_degradation(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    deg: DegradationConfig | None = None,
    econ: EconomicsConfig | None = None,
) -> list[DegradationCurve]:
    """Ageing curve for every body."""
    return [degradation_for(b, deg, econ) for b in bodies]


def degradation_table(curves: list[DegradationCurve]) -> str:
    """Compact new vs end-of-life fuel and range comparison across the fleet."""
    lines = [
        "=== Fleet drivetrain ageing (new -> end of life) ===",
        "  Body          Fuel new->EOL    Range new->EOL   Drift",
        "  ----------------------------------------------------------",
    ]
    for c in curves:
        lines.append(
            f"  {c.body:<12} {c.new.fuel_l_per_100km:5.2f}->"
            f"{c.eol.fuel_l_per_100km:5.2f}    "
            f"{c.new.ev_range_km:5.0f}->{c.eol.ev_range_km:5.0f} km   "
            f"+{c.fuel_drift_pct:4.1f}% / -{c.range_loss_pct:4.1f}%")
    lines.append("  (fuel = charge-sustaining mixed; range = charge-depleting "
                 "urban)")
    return "\n".join(lines)
