"""Move J -- cold-start engine penalty.

The validated fuel figures assume a fully warm engine. A real free-piston
generator, like any combustion engine, burns **richer while it warms up**: cold
cylinder walls quench combustion, friction is higher and after-treatment is not
yet at temperature, so the first tens of seconds of engine running consume extra
fuel per unit of electricity generated. This Move adds that penalty.

The model is a **read-only post-processor**: it runs the ordinary
(charge-sustaining) simulation, then walks the per-step telemetry and adds a
fuel surcharge to each engine-on step that decays with the engine's *cumulative
running time* (not wall-clock time, so intermittent running is handled
correctly):

    excess(tau) = excess0 * exp(-tau / warmup_tau)

where ``tau`` is the seconds the engine has been running so far. Because the
penalty rides on top of an existing run, no validated warm-fuel number can
regress. The penalty is also **ambient-aware** (it pairs with Move H): a colder
start both raises the initial excess and lengthens the warm-up. Pure stdlib.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for
from .drive_cycles import DriveCycle, DriveCycles
from .powertrain import Powertrain
from .simulation import run


@dataclass(frozen=True)
class ColdStartConfig:
    """Cold-start fuel-enrichment model (representative values)."""

    base_excess: float = 0.40        # +40% fuel at the instant of a cold start
    warmup_tau_s: float = 25.0       # engine-on time constant toward warm
    reference_c: float = 20.0        # temperature base_excess is calibrated at
    excess_per_deg_c: float = 0.012  # extra fractional excess per K below ref
    tau_per_deg_c: float = 0.6       # extra warm-up seconds per K below ref

    def excess0(self, ambient_c: float) -> float:
        below = max(0.0, self.reference_c - ambient_c)
        return self.base_excess + below * self.excess_per_deg_c

    def tau_s(self, ambient_c: float) -> float:
        below = max(0.0, self.reference_c - ambient_c)
        return self.warmup_tau_s + below * self.tau_per_deg_c


@dataclass(frozen=True)
class ColdStartResult:
    """Cold-start fuel penalty for one body on one cycle at one temperature."""

    body: str
    cycle: str
    ambient_c: float
    warm_fuel_l_per_100km: float     # the sim's steady (warm) figure
    cold_fuel_l_per_100km: float     # with the cold-start surcharge
    penalty_l: float                 # absolute extra litres this trip
    penalty_pct: float               # uplift over the warm figure
    engine_on_s: float               # seconds the engine actually ran

    def report(self) -> str:
        return (f"  {self.body:<12} {self.ambient_c:+5.0f}C  "
                f"warm {self.warm_fuel_l_per_100km:5.2f} -> "
                f"cold {self.cold_fuel_l_per_100km:5.2f} L/100km "
                f"(+{self.penalty_pct:4.1f}%, engine on {self.engine_on_s:4.0f}s)")


def _charge_sustaining(body: BodyStyle):
    base = phase1_config_for(body)
    return replace(base, battery=replace(base.battery,
                                         initial_soc=base.battery.soc_target))


def cold_start_for(
    body: BodyStyle = PHASE1_BODIES[0],
    cycle: DriveCycle | None = None,
    ambient_c: float = 20.0,
    cfg: ColdStartConfig | None = None,
) -> ColdStartResult:
    """Run `body` warm, then add the cold-start surcharge as a post-process."""
    c = cfg or ColdStartConfig()
    cyc = cycle or DriveCycles.mixed()
    res = run(Powertrain(_charge_sustaining(body)), cyc)

    excess0 = c.excess0(ambient_c)
    tau = c.tau_s(ambient_c)
    dt = cyc.dt_s
    engine_on_s = 0.0
    extra_l = 0.0
    for rec in res.records:
        if rec.active_index >= 0 and rec.fuel_l > 0.0:
            # Surcharge uses the enrichment at the engine's current warm-state.
            extra_l += rec.fuel_l * excess0 * math.exp(-engine_on_s / tau)
            engine_on_s += dt

    dist = res.distance_km
    warm = res.fuel_l_per_100km
    cold = (res.fuel_l + extra_l) / dist * 100.0 if dist > 0 else 0.0
    pct = 100.0 * extra_l / res.fuel_l if res.fuel_l > 0 else 0.0
    return ColdStartResult(
        body=body.name,
        cycle=cyc.name,
        ambient_c=ambient_c,
        warm_fuel_l_per_100km=warm,
        cold_fuel_l_per_100km=cold,
        penalty_l=extra_l,
        penalty_pct=pct,
        engine_on_s=engine_on_s,
    )


def fleet_cold_start(
    ambient_c: float = 20.0,
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    cycle: DriveCycle | None = None,
    cfg: ColdStartConfig | None = None,
) -> list[ColdStartResult]:
    """Cold-start penalty for every body on the same cycle/temperature."""
    cyc = cycle or DriveCycles.mixed()
    return [cold_start_for(b, cyc, ambient_c, cfg) for b in bodies]


def cold_start_table(results: list[ColdStartResult]) -> str:
    """Compact warm-vs-cold fuel comparison across the fleet."""
    amb = results[0].ambient_c if results else 0.0
    cyc = results[0].cycle if results else ""
    lines = [
        f"=== Cold-start fuel penalty ({cyc}, {amb:+.0f}C) ===",
        "  Body          Warm   Cold   Penalty  Engine-on",
        "  --------------------------------------------------",
    ]
    for r in results:
        lines.append(
            f"  {r.body:<12} {r.warm_fuel_l_per_100km:5.2f}  "
            f"{r.cold_fuel_l_per_100km:5.2f}  +{r.penalty_pct:4.1f}%   "
            f"{r.engine_on_s:4.0f}s")
    lines.append("  (warm = validated steady figure; cold adds first-minutes "
                 "enrichment)")
    return "\n".join(lines)
