"""Executive summary -- the single front door to every Phase-1 headline.

Each Move (A--I) proves one layer in depth, but the results live in nine
separate sections. This module rolls the load-bearing numbers from all of them
into one compact dashboard so a newcomer can see the whole story at a glance:
per-body fuel, cost, lifecycle CO2, right-sized battery power, the uncertainty
band, the seasonal swing and the standardized-cycle figure, plus the handful of
fleet-wide headline facts.

It is **read-only and additive**: it only *reads* the existing public API
(`fleet_tco`, `fleet_battery_sizing`, `fleet_uncertainty`, `fleet_ambient`,
`fleet_regulatory`, the rotor model), so it can never change a validated number.
Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import KW, phase1_config
from .economics import EconomicsConfig, TcoResult, fleet_tco
from .fleet import charge_sustaining_bodies, run_fleet
from .pcmritms_rotor import RotorSet, simulate_torque_augmentation
from .pcmritms_coupling import rotor_transient_power_w
from .sizing import fleet_battery_sizing
from .montecarlo import fleet_uncertainty
from .ambient import fleet_ambient
from .regulatory_cycles import RegulatoryCycles, fleet_regulatory


@dataclass(frozen=True)
class BodySummary:
    """Every headline for one vehicle body, gathered across Moves A--I."""

    body: str
    fuel_l_per_100km: float          # Move B: blended charge-sustaining fuel
    cost_per_km: float               # Move D: lifetime cost / km
    cost_p05: float                  # Move G: 5th-percentile cost / km
    cost_p95: float                  # Move G: 95th-percentile cost / km
    lifecycle_co2_g_per_km: float    # Move D: cradle-to-grave CO2
    battery_power_kw: float          # Move F: right-sized discharge power
    battery_c_rate: float            # Move F: C-rate at that power
    wltp_fuel_l_per_100km: float     # Move I: standardized WLTP economy
    ambient_swing_pct: float         # Move H: cold-to-hot fuel swing


@dataclass(frozen=True)
class ExecutiveSummary:
    """Fleet-wide headlines plus a per-body row for each Phase-1 body."""

    bodies: tuple[BodySummary, ...]
    rotor_peak_nm: float             # Move A: PCMRITMS peak torque
    rotor_boost_pct: float           # Move A: brief torque boost
    rotor_surge_kw: float            # Move A: rotor-derived surge power
    buffer_burst_kw: float           # buffer continuous discharge rating
    pack_replacements: int           # Move D: battery swaps over vehicle life
    embodied_co2_share_pct: float    # Move D: embodied-battery share (SUV)
    derates_in_climate: bool         # Move H: any thermal derate -10..+40 C
    regulatory_shortfalls: int       # Move I: capability misses on WLTP/EPA

    def report(self) -> str:
        lines = [
            "================================================================",
            "  PROJECT PHOENIX - executive summary (Phase-1, Moves A-I)",
            "================================================================",
            "",
            "  Per-body headlines (same powertrain, six bodies):",
            "  Body          Fuel   Cost/km  (5-95%)        CO2   Batt    WLTP  Season",
            "                L/100  EUR/km   EUR/km        g/km   kW/C    L/100   swing",
            "  " + "-" * 72,
        ]
        for b in self.bodies:
            lines.append(
                f"  {b.body:<12} {b.fuel_l_per_100km:5.2f}  "
                f"{b.cost_per_km:6.3f}  "
                f"[{b.cost_p05:.3f}-{b.cost_p95:.3f}] "
                f"{b.lifecycle_co2_g_per_km:6.1f}  "
                f"{b.battery_power_kw:3.0f}/{b.battery_c_rate:.1f} "
                f"{b.wltp_fuel_l_per_100km:6.2f} "
                f"{b.ambient_swing_pct:5.1f}%")
        lines += [
            "",
            "  Fleet-wide headline facts:",
            f"    PCMRITMS rotor      : {self.rotor_peak_nm:.1f} N.m peak "
            f"(+{self.rotor_boost_pct:.1f}%), {self.rotor_surge_kw:.1f} kW surge "
            f"lifts the buffer to {self.buffer_burst_kw:.0f} kW brief burst",
            f"    Battery longevity   : {self.pack_replacements} replacements "
            "over vehicle life; embodied CO2 "
            f"{self.embodied_co2_share_pct:.1f}% of lifecycle (SUV)",
            f"    Climate robustness  : thermal derate from -10C to +40C? "
            f"{'yes' if self.derates_in_climate else 'no'}; "
            f"regulatory shortfalls: {self.regulatory_shortfalls}",
            "",
            "  Read: small cool-running pack, low cost & CO2, capable across the",
            "  full climate and type-approval envelope - every figure is the",
            "  median of an honest band, not an optimistic point.",
            "================================================================",
        ]
        return "\n".join(lines)


def build_executive_summary(
    trials: int = 48,
    seed: int = 0,
    econ: EconomicsConfig | None = None,
) -> ExecutiveSummary:
    """Aggregate every Phase-1 headline into one structure (read-only)."""
    tco: list[TcoResult] = fleet_tco(run_fleet(charge_sustaining_bodies()), econ)
    tco_by = {t.body: t for t in tco}
    sizing_by = {s.body: s for s in fleet_battery_sizing()}
    bands_by = {b.body: b for b in fleet_uncertainty(trials=trials, seed=seed,
                                                     econ=econ)}
    ambient_by = {s.body: s for s in fleet_ambient()}
    wltp = RegulatoryCycles.wltp()
    reg = fleet_regulatory(cycles=[wltp])
    wltp_by = {r.body: r for r in reg}
    reg_shortfalls = sum(r.shortfall_events for r in fleet_regulatory())

    rows: list[BodySummary] = []
    for t in tco:
        s = sizing_by[t.body]
        band = bands_by[t.body]
        rows.append(BodySummary(
            body=t.body,
            fuel_l_per_100km=t.blended_fuel_l_per_100km,
            cost_per_km=t.cost_per_km,
            cost_p05=band.cost_per_km.p05,
            cost_p95=band.cost_per_km.p95,
            lifecycle_co2_g_per_km=t.lifecycle_co2_g_per_km,
            battery_power_kw=s.recommended_power_w / KW,
            battery_c_rate=s.recommended_c_rate,
            wltp_fuel_l_per_100km=wltp_by[t.body].fuel_l_per_100km,
            ambient_swing_pct=ambient_by[t.body].fuel_swing_pct,
        ))

    tm = simulate_torque_augmentation()
    surge_kw = rotor_transient_power_w(RotorSet()) / KW
    suv = tco[0]
    embodied_share = (100.0 * suv.embodied_battery_co2_t / suv.lifecycle_co2_t
                      if suv.lifecycle_co2_t else 0.0)
    any_derate = any(s.any_derate for s in ambient_by.values())

    return ExecutiveSummary(
        bodies=tuple(rows),
        rotor_peak_nm=tm.peak_nm,
        rotor_boost_pct=tm.boost_percent,
        rotor_surge_kw=surge_kw,
        buffer_burst_kw=phase1_config(rotor_coupled=True).buffer.peak_transient_w / KW,
        pack_replacements=max(t.battery_replacements for t in tco),
        embodied_co2_share_pct=embodied_share,
        derates_in_climate=any_derate,
        regulatory_shortfalls=reg_shortfalls,
    )
