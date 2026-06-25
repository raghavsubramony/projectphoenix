"""Move K -- payload and passenger loading.

The validated figures are quoted at a nominal kerb-plus-driver mass. Real
vehicles carry passengers and cargo, and every extra kilogram raises rolling
resistance, the energy to accelerate, and the grade load. This Move sweeps the
*payload* on top of the existing body sweep: from driver-only to a full cabin of
occupants plus cargo, it re-runs the charge-sustaining fuel economy and checks
that capability still holds.

It is **read-only**: each load point is a fresh *copy* of the validated body
config with only ``mass_kg`` raised by the payload, so no validated (driver-only)
number can regress. Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for
from .drive_cycles import DriveCycle, DriveCycles
from .powertrain import Powertrain
from .simulation import run

#: Representative occupant mass (adult + a little clothing/effects), kg.
OCCUPANT_KG = 75.0


@dataclass(frozen=True)
class LoadConfig:
    """Payload model: how many people and how much cargo to add."""

    occupant_kg: float = OCCUPANT_KG
    occupant_counts: tuple[int, ...] = (1, 2, 3, 5)
    cargo_kg_full: float = 100.0     # cargo carried at the fullest load point
    cycle_grade: bool = True         # include the towing+grade capability check


@dataclass(frozen=True)
class LoadPoint:
    """One payload level for one body."""

    body: str
    occupants: int
    cargo_kg: float
    added_kg: float
    kerb_kg: float
    fuel_l_per_100km: float
    fuel_penalty_pct: float          # vs the driver-only baseline
    grade_shortfalls: int            # capability misses on the climb test


@dataclass(frozen=True)
class LoadSweep:
    """A body's response across a payload sweep."""

    body: str
    cycle: str
    points: tuple[LoadPoint, ...]

    @property
    def baseline(self) -> LoadPoint:
        return self.points[0]

    @property
    def full(self) -> LoadPoint:
        return self.points[-1]

    @property
    def full_penalty_pct(self) -> float:
        return self.full.fuel_penalty_pct

    @property
    def stays_capable(self) -> bool:
        return all(p.grade_shortfalls == 0 for p in self.points)

    def report(self) -> str:
        lines = [
            f"=== {self.body} | {self.cycle}: payload sweep ===",
            "  Occupants  +kg    Fuel    Penalty  Grade",
            "  ------------------------------------------",
        ]
        for p in self.points:
            lines.append(
                f"  {p.occupants:>4d} + {p.cargo_kg:>3.0f}kg {p.added_kg:>5.0f} "
                f"{p.fuel_l_per_100km:6.2f}  +{p.fuel_penalty_pct:4.1f}%  "
                f"{'ok' if p.grade_shortfalls == 0 else 'MISS'}")
        lines.append(
            f"  driver-only -> full load: +{self.full_penalty_pct:.1f}% fuel; "
            f"capability holds: {'yes' if self.stays_capable else 'no'}")
        return "\n".join(lines)


def _config_with_mass(body: BodyStyle, added_kg: float):
    base = phase1_config_for(body)
    veh = replace(base.vehicle, mass_kg=base.vehicle.mass_kg + added_kg)
    batt = replace(base.battery, initial_soc=base.battery.soc_target)
    return replace(base, vehicle=veh, battery=batt)


def _grade_shortfalls(body: BodyStyle, added_kg: float) -> int:
    cfg = _config_with_mass(body, added_kg)
    cyc = DriveCycles.towing_grade()
    acc = cyc.accelerations()
    tw = Powertrain(cfg)
    events = 0
    for i in range(len(cyc.speeds_ms)):
        r = tw.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
        if r.shortfall_w > 1.0:
            events += 1
    return events


def payload_sweep(
    body: BodyStyle = PHASE1_BODIES[0],
    cycle: DriveCycle | None = None,
    load: LoadConfig | None = None,
) -> LoadSweep:
    """Sweep `body` from driver-only to a full payload on one cycle."""
    cfg = load or LoadConfig()
    cyc = cycle or DriveCycles.mixed()
    counts = cfg.occupant_counts
    max_count = max(counts)
    points: list[LoadPoint] = []
    baseline_fuel = 0.0
    for n in counts:
        # Cargo scales with how full the cabin is (zero at driver-only).
        frac = (n - 1) / (max_count - 1) if max_count > 1 else 0.0
        cargo = cfg.cargo_kg_full * frac
        added = n * cfg.occupant_kg + cargo
        res = run(Powertrain(_config_with_mass(body, added)), cyc)
        fuel = res.fuel_l_per_100km
        if n == counts[0]:
            baseline_fuel = fuel
        penalty = (100.0 * (fuel - baseline_fuel) / baseline_fuel
                   if baseline_fuel > 0 else 0.0)
        shortfalls = _grade_shortfalls(body, added) if cfg.cycle_grade else 0
        points.append(LoadPoint(
            body=body.name,
            occupants=n,
            cargo_kg=cargo,
            added_kg=added,
            kerb_kg=body.mass_kg,
            fuel_l_per_100km=fuel,
            fuel_penalty_pct=penalty,
            grade_shortfalls=shortfalls,
        ))
    return LoadSweep(body=body.name, cycle=cyc.name, points=tuple(points))


def fleet_payload(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycle: DriveCycle | None = None,
    load: LoadConfig | None = None,
) -> list[LoadSweep]:
    """Payload sweep for every body on the same cycle."""
    cyc = cycle or DriveCycles.mixed()
    return [payload_sweep(b, cyc, load) for b in bodies]


def payload_table(sweeps: list[LoadSweep]) -> str:
    """Compact driver-only vs full-load fuel comparison across the fleet."""
    lines = [
        "=== Fleet payload sensitivity (driver-only -> full load) ===",
        "  Body          +kg full  Solo L  Full L  Penalty  Capable",
        "  ----------------------------------------------------------",
    ]
    for s in sweeps:
        b = s.baseline
        f = s.full
        lines.append(
            f"  {s.body:<12} {f.added_kg:>7.0f}  {b.fuel_l_per_100km:6.2f}  "
            f"{f.fuel_l_per_100km:6.2f}  +{s.full_penalty_pct:4.1f}%   "
            f"{'yes' if s.stays_capable else 'no'}")
    lines.append("  (charge-sustaining fuel; full load = 5 occupants + cargo)")
    return "\n".join(lines)
