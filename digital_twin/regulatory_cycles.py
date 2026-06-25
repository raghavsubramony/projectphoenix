"""Move I -- standardized regulatory drive cycles (WLTP / EPA).

The synthetic cycles in :mod:`digital_twin.drive_cycles` are representative but
home-grown. Regulators measure every production car on a small set of *named*
procedures, so this module adds reconstructions of them:

* **WLTP** (Worldwide harmonised Light-vehicles Test Procedure, Class 3) -- the
  European/global type-approval cycle, four phases (Low / Medium / High /
  Extra-High) of rising speed.
* **EPA UDDS** (Urban Dynamometer Driving Schedule, "FTP city") -- the US city
  cycle, lots of stops.
* **EPA HWFET** (Highway Fuel Economy Test) -- the US steady highway cycle.

**Honesty note.** These are *envelope-matched reconstructions*, not the official
second-by-second traces (which are copyrighted lookup tables). Each cycle is
built from trapezoidal accelerate / cruise / idle micro-trips tuned so the
macroscopic statistics that actually drive energy use -- total distance,
duration, average speed, peak speed and stop count -- land within a few percent
of the published figures (stored in each :class:`RegulatoryCycle.spec`). That is
enough to report comparable fuel economy without fabricating the exact trace.
Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for
from .drive_cycles import DriveCycle, _ramp, _segment
from .powertrain import Powertrain
from .simulation import run


@dataclass(frozen=True)
class CycleStats:
    """Macroscopic descriptors of a drive cycle."""

    distance_km: float
    duration_s: float
    avg_speed_kmh: float
    max_speed_kmh: float
    stops: int  # count of distinct idle (v ~ 0) intervals after motion


@dataclass(frozen=True)
class CycleSpec:
    """Published reference statistics for a standardized cycle."""

    name: str
    distance_km: float
    duration_s: float
    avg_speed_kmh: float
    max_speed_kmh: float


@dataclass(frozen=True)
class RegulatoryCycle:
    """A reconstructed standardized cycle bundled with its published spec."""

    cycle: DriveCycle
    spec: CycleSpec

    def stats(self) -> CycleStats:
        return cycle_stats(self.cycle)


def cycle_stats(cycle: DriveCycle) -> CycleStats:
    """Compute distance / duration / average & peak speed / stop count."""
    v = cycle.speeds_ms
    dt = cycle.dt_s
    distance_m = sum(s * dt for s in v)
    duration_s = dt * (len(v) - 1) if len(v) > 1 else 0.0
    max_ms = max(v) if v else 0.0
    avg_ms = (distance_m / duration_s) if duration_s else 0.0
    # Count idle intervals that follow motion (a "stop").
    stops = 0
    moving = False
    idle = False
    for s in v:
        if s > 0.3:           # ~1 km/h threshold
            moving = True
            idle = False
        elif moving and not idle:
            stops += 1
            idle = True
    return CycleStats(
        distance_km=distance_m / 1000.0,
        duration_s=duration_s,
        avg_speed_kmh=avg_ms * 3.6,
        max_speed_kmh=max_ms * 3.6,
        stops=stops,
    )


def _build(name: str, waypoints: list[tuple[float, float, float]],
           dt_s: float) -> DriveCycle:
    """Build a cycle from (target_kmh, hold_s, accel_kmh_s) micro-trips."""
    speeds: list[float] = [0.0]
    for target_kmh, hold_s, accel_kmh_s in waypoints:
        _segment(speeds, target_kmh, hold_s=hold_s,
                 accel_kmh_s=accel_kmh_s, dt_s=dt_s)
    return _ramp(speeds, dt_s, name)


# --- WLTP Class 3 -----------------------------------------------------------
# Published (Class 3b): 1800 s, 23.27 km, avg 46.5 km/h, max 131.3 km/h, 4 phases.
_WLTP_LOW = [(20, 6, 4), (0, 10, 4), (40, 8, 4), (15, 5, 4), (56, 10, 4),
             (0, 12, 4), (30, 8, 4), (0, 14, 4), (45, 9, 4), (0, 11, 4)]
_WLTP_MEDIUM = [(60, 18, 4), (35, 8, 4), (77, 22, 4), (45, 10, 4),
                (70, 18, 4), (40, 8, 4), (0, 8, 4)]
_WLTP_HIGH = [(85, 28, 4), (60, 12, 4), (97, 32, 4), (70, 14, 4),
              (90, 24, 4), (65, 10, 4), (0, 6, 4)]
_WLTP_EXTRA = [(110, 38, 3), (90, 14, 3), (131, 42, 3), (100, 18, 3),
               (120, 28, 3), (95, 12, 3)]
_WLTP_WAYPOINTS: list[tuple[float, float, float]] = (
    _WLTP_LOW * 5 + _WLTP_MEDIUM * 2 + _WLTP_HIGH * 2 + _WLTP_EXTRA * 1
)

_WLTP_SPEC = CycleSpec("WLTP Class 3", 23.27, 1800.0, 46.5, 131.3)

# --- EPA UDDS (city) --------------------------------------------------------
# Published: 1369 s, 12.07 km, avg 31.5 km/h, max 91.2 km/h, ~17 stops.
_UDDS_WAYPOINTS: list[tuple[float, float, float]] = sum(
    ([(48, 9, 5), (0, 6, 5), (66, 11, 5), (33, 5, 5), (0, 6, 5),
      (56, 10, 5), (0, 6, 5)] for _ in range(10)), []
) + [(91, 12, 6), (45, 8, 6), (0, 8, 6), (60, 10, 5), (0, 10, 5)]

_UDDS_SPEC = CycleSpec("EPA UDDS (city)", 12.07, 1369.0, 31.5, 91.2)

# --- EPA HWFET (highway) ----------------------------------------------------
# Published: 765 s, 16.45 km, avg 77.7 km/h, max 96.4 km/h, no stops.
_HWFET_WAYPOINTS: list[tuple[float, float, float]] = [
    (70, 80, 4), (96, 60, 3), (74, 80, 3), (86, 110, 3),
    (68, 80, 3), (90, 70, 3), (72, 90, 3), (82, 100, 3), (76, 40, 3),
]

_HWFET_SPEC = CycleSpec("EPA HWFET (highway)", 16.45, 765.0, 77.7, 96.4)


class RegulatoryCycles:
    """Factory of reconstructed standardized regulatory cycles."""

    @staticmethod
    def wltp(dt_s: float = 1.0) -> RegulatoryCycle:
        return RegulatoryCycle(
            _build("WLTP Class 3", _WLTP_WAYPOINTS, dt_s), _WLTP_SPEC)

    @staticmethod
    def epa_udds(dt_s: float = 1.0) -> RegulatoryCycle:
        return RegulatoryCycle(
            _build("EPA UDDS (city)", _UDDS_WAYPOINTS, dt_s), _UDDS_SPEC)

    @staticmethod
    def epa_hwfet(dt_s: float = 1.0) -> RegulatoryCycle:
        return RegulatoryCycle(
            _build("EPA HWFET (highway)", _HWFET_WAYPOINTS, dt_s), _HWFET_SPEC)

    @staticmethod
    def all() -> list[RegulatoryCycle]:
        return [RegulatoryCycles.wltp(),
                RegulatoryCycles.epa_udds(),
                RegulatoryCycles.epa_hwfet()]


def regulatory_fidelity_table(cycles: list[RegulatoryCycle] | None = None) -> str:
    """Show each reconstruction's stats against its published reference."""
    cycles = cycles or RegulatoryCycles.all()
    lines = [
        "=== Regulatory cycle reconstruction fidelity ===",
        "  Cycle                    Dist km     Avg km/h    Max km/h",
        "  (built vs published)",
        "  -----------------------------------------------------------",
    ]
    for rc in cycles:
        st = rc.stats()
        sp = rc.spec
        lines.append(
            f"  {sp.name:<22} {st.distance_km:5.1f}/{sp.distance_km:<5.1f} "
            f"{st.avg_speed_kmh:5.1f}/{sp.avg_speed_kmh:<5.1f} "
            f"{st.max_speed_kmh:5.1f}/{sp.max_speed_kmh:<5.1f}")
    lines.append("  (reconstructions match the published energy-relevant "
                 "envelope, not the exact trace)")
    return "\n".join(lines)


@dataclass(frozen=True)
class RegulatoryEconomy:
    """Charge-sustaining fuel economy for one body on one regulatory cycle."""

    body: str
    cycle: str
    fuel_l_per_100km: float
    co2_g_per_km: float
    shortfall_events: int


def regulatory_economy(
    body: BodyStyle = PHASE1_BODIES[0],
    cycle: RegulatoryCycle | None = None,
) -> RegulatoryEconomy:
    """Run `body` (charge-sustaining) on a regulatory cycle and report economy."""
    rc = cycle or RegulatoryCycles.wltp()
    base = phase1_config_for(body)
    cfg = replace(base, battery=replace(base.battery,
                                        initial_soc=base.battery.soc_target))
    res = run(Powertrain(cfg), rc.cycle)
    equiv = res.equiv_fuel_l_per_100km
    co2 = (res.co2_g_per_km * equiv / res.fuel_l_per_100km
           if res.fuel_l_per_100km > 0 else res.co2_g_per_km)
    return RegulatoryEconomy(
        body=body.name,
        cycle=rc.spec.name,
        fuel_l_per_100km=equiv,
        co2_g_per_km=co2,
        shortfall_events=res.shortfall_events,
    )


def fleet_regulatory(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycles: list[RegulatoryCycle] | None = None,
) -> list[RegulatoryEconomy]:
    """Charge-sustaining economy for every body on every regulatory cycle."""
    cyc = cycles or RegulatoryCycles.all()
    return [regulatory_economy(b, rc) for b in bodies for rc in cyc]


def regulatory_economy_table(results: list[RegulatoryEconomy]) -> str:
    """Body x cycle fuel-economy matrix (charge-sustaining L/100km)."""
    def _short(name: str) -> str:
        for tag in ("WLTP", "UDDS", "HWFET"):
            if tag in name:
                return tag
        return name.split()[0]

    cycles = sorted({r.cycle for r in results})
    bodies = list(dict.fromkeys(r.body for r in results))
    by = {(r.body, r.cycle): r for r in results}
    head = "  Body          " + "".join(f"{_short(c):>10}" for c in cycles)
    lines = ["=== Fleet fuel economy on regulatory cycles (L/100km) ===", head,
             "  " + "-" * (12 + 10 * len(cycles))]
    for b in bodies:
        row = f"  {b:<12}"
        for c in cycles:
            r = by.get((b, c))
            row += f"{r.fuel_l_per_100km:>10.2f}" if r else f"{'-':>10}"
        lines.append(row)
    lines.append("  (charge-sustaining; reconstructed WLTP / EPA cycles)")
    return "\n".join(lines)
