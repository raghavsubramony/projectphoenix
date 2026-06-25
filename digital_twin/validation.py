"""Sensitivity analysis + capability sweeps for the digital twin.

This module answers "how robust are the headline conclusions?" by perturbing
the physical inputs and measuring the response, instead of trusting a single
point estimate. Two complementary tools:

* `sensitivity_table` ranks the design parameters by **elasticity** - the
  percentage change in a headline output (e.g. fuel economy) per percentage
  change in an input. It tells you *what actually matters*.
* `capability_sweep` traces a single parameter (buffer size, generation slew,
  battery power) against the transient launch shortfall, showing the monotone
  trade the PCMRITMS reservoir buys.

Everything is pure-stdlib and side-effect free: each evaluation builds a fresh
twin from a perturbed config, so runs never contaminate each other.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from .config import PHASE1_BODIES, TwinConfig, phase1_config_for
from .drive_cycles import DriveCycle, DriveCycles
from .powertrain import Powertrain
from .simulation import run

# A scaler multiplies one physical parameter by a dimensionless factor (1.0 =
# nominal) and returns a new config. Using a uniform multiplicative form lets
# every parameter share one elasticity definition regardless of its units.
Scaler = Callable[[TwinConfig, float], TwinConfig]


@dataclass(frozen=True)
class Parameter:
    """One perturbable physical input, expressed as a multiplicative scaler."""

    name: str
    unit: str
    nominal: float          # human-readable nominal value (for display)
    scale: Scaler           # (cfg, factor) -> cfg with the param multiplied
    note: str = ""


# --- scaler builders --------------------------------------------------------

def _scale_vehicle(field: str) -> Scaler:
    def f(cfg: TwinConfig, k: float) -> TwinConfig:
        v = cfg.vehicle
        return replace(cfg, vehicle=replace(v, **{field: getattr(v, field) * k}))
    return f


def _scale_driveline(cfg: TwinConfig, k: float) -> TwinConfig:
    # Efficiencies are bounded by 1.0; clamp so a +scale stays physical.
    v = cfg.vehicle
    return replace(cfg, vehicle=replace(
        v, driveline_efficiency=min(0.999, v.driveline_efficiency * k)))


def _scale_tier_efficiency(cfg: TwinConfig, k: float) -> TwinConfig:
    tiers = tuple(
        replace(t, thermal_efficiency=min(0.99, t.thermal_efficiency * k))
        for t in cfg.atpe.tiers
    )
    return replace(cfg, atpe=replace(cfg.atpe, tiers=tiers))


def phase1_sensitivity_params() -> list[Parameter]:
    """The physical inputs whose elasticity is worth ranking for fuel economy."""
    suv = PHASE1_BODIES[0]
    return [
        Parameter("Vehicle mass", "kg", suv.mass_kg,
                  _scale_vehicle("mass_kg"),
                  "inertia + rolling load"),
        Parameter("Drag coefficient", "Cd", suv.drag_coefficient,
                  _scale_vehicle("drag_coefficient"),
                  "aero load (dominates at highway speed)"),
        Parameter("Frontal area", "m^2", suv.frontal_area_m2,
                  _scale_vehicle("frontal_area_m2"),
                  "aero load"),
        Parameter("Rolling resistance", "Crr", suv.rolling_resistance,
                  _scale_vehicle("rolling_resistance"),
                  "tyre/road loss"),
        Parameter("Aux load", "W", suv.aux_load_w,
                  _scale_vehicle("aux_load_w"),
                  "constant accessory draw"),
        Parameter("Driveline efficiency", "-", suv.driveline_efficiency,
                  _scale_driveline,
                  "bus->wheel loss (inverse effect)"),
        Parameter("ATPE tier efficiency", "-", 0.44,
                  _scale_tier_efficiency,
                  "fuel->electrical conversion (inverse effect)"),
    ]


# --- elasticity / sensitivity ----------------------------------------------

@dataclass(frozen=True)
class Elasticity:
    """Normalized sensitivity of a metric to one parameter, around nominal."""

    parameter: str
    unit: str
    nominal: float
    base_metric: float
    elasticity: float       # d(ln metric) / d(ln param), central difference
    note: str

    @property
    def abs_elasticity(self) -> float:
        return abs(self.elasticity)


def _metric_at(base: TwinConfig, param: Parameter, factor: float,
               metric: str, cycle: DriveCycle) -> float:
    cfg = param.scale(base, factor)
    res = run(Powertrain(cfg), cycle)
    return getattr(res, metric)


def elasticity(base: TwinConfig, param: Parameter, metric: str,
               cycle: DriveCycle, frac: float = 0.05) -> Elasticity:
    """Central-difference elasticity of `metric` w.r.t. `param` at nominal.

    `frac` is the relative perturbation (default +/-5%). The elasticity is the
    percentage change in the metric per one-percent change in the input, so it
    is directly comparable across parameters with different units.
    """
    base_metric = _metric_at(base, param, 1.0, metric, cycle)
    hi = _metric_at(base, param, 1.0 + frac, metric, cycle)
    lo = _metric_at(base, param, 1.0 - frac, metric, cycle)
    if base_metric == 0.0:
        e = 0.0
    else:
        e = ((hi - lo) / base_metric) / (2.0 * frac)
    return Elasticity(param.name, param.unit, param.nominal, base_metric, e,
                      param.note)


def sensitivity_table(metric: str = "fuel_l_per_100km",
                      cycle: DriveCycle | None = None,
                      base: TwinConfig | None = None,
                      params: list[Parameter] | None = None,
                      frac: float = 0.05) -> list[Elasticity]:
    """Rank every parameter by |elasticity| for one metric on one cycle.

    Defaults: the charge-sustaining Phase-1 SUV on the mixed cycle, fuel
    economy. Returns the elasticities sorted most- to least-influential.
    """
    cycle = cycle or DriveCycles.mixed()
    if base is None:
        base = phase1_config_for(PHASE1_BODIES[0])
        base = replace(base, battery=replace(
            base.battery, initial_soc=base.battery.soc_target))
    params = params or phase1_sensitivity_params()
    results = [elasticity(base, p, metric, cycle, frac) for p in params]
    results.sort(key=lambda e: e.abs_elasticity, reverse=True)
    return results


def sensitivity_report(elasticities: list[Elasticity],
                       metric: str = "fuel_l_per_100km") -> str:
    """Human-readable elasticity ranking table."""
    lines = [
        f"=== Sensitivity ranking: {metric} (elasticity = % out / % in) ===",
        f"  {'Parameter':<22}{'Nominal':>12}  {'Elasticity':>11}  Driver",
        "  " + "-" * 70,
    ]
    for e in elasticities:
        arrow = "up" if e.elasticity > 0 else ("dn" if e.elasticity < 0 else "--")
        lines.append(
            f"  {e.parameter:<22}{e.nominal:>10.3g} {e.unit:<2}"
            f"{e.elasticity:>+10.2f} {arrow}  {e.note}")
    lines.append("  (|elasticity| > 1 = amplifying; < 1 = damping)")
    return "\n".join(lines)


# --- capability sweep (transient shortfall vs a design lever) ---------------

@dataclass(frozen=True)
class SweepPoint:
    factor: float
    value: float
    metric: float


def capability_sweep(
    factors: list[float],
    metric: str = "max_shortfall_kw",
    scaler: Scaler | None = None,
    base: TwinConfig | None = None,
    cycle: DriveCycle | None = None,
    nominal: float = 1.0,
) -> list[SweepPoint]:
    """Trace a metric as one design lever is scaled across `factors`.

    Defaults sweep the inertial-buffer energy reservoir against the transient
    launch shortfall on a slew-limited, power-limited SUV - the configuration
    where the PCMRITMS reservoir actually matters. Returns one point per factor.
    """
    cycle = cycle or DriveCycles.transient_stress()
    if base is None:
        cfg = phase1_config_for(PHASE1_BODIES[0], rotor_coupled=True)
        base = replace(
            cfg,
            atpe=replace(cfg.atpe, max_slew_w_per_s=60_000.0),
            battery=replace(cfg.battery, max_discharge_w=40_000.0,
                            initial_soc=cfg.battery.soc_target),
        )
    if scaler is None:
        def scaler(c: TwinConfig, k: float) -> TwinConfig:  # buffer reservoir
            return replace(c, buffer=replace(
                c.buffer, max_energy_j=c.buffer.max_energy_j * k))
    points: list[SweepPoint] = []
    for k in factors:
        res = run(Powertrain(scaler(base, k)), cycle)
        points.append(SweepPoint(k, nominal * k, getattr(res, metric)))
    return points


def capability_report(points: list[SweepPoint], lever: str,
                      metric: str = "max_shortfall_kw") -> str:
    """Human-readable capability-sweep table."""
    lines = [
        f"=== Capability sweep: {metric} vs {lever} ===",
        f"  {'Factor':>8}{'Value':>14}{'  ' + metric:>18}",
        "  " + "-" * 42,
    ]
    for p in points:
        lines.append(f"  {p.factor:>7.2f}x{p.value:>14.0f}{p.metric:>18.2f}")
    return "\n".join(lines)
