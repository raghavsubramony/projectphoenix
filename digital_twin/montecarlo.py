"""Monte-Carlo uncertainty bands for the digital twin's headline numbers.

Move C ranked the sensitivity of each input *one at a time*. Real-world honesty
demands the next step: vary **all** the uncertain inputs **together** and report
the resulting spread, so every headline figure becomes a confidence interval
rather than a single deceptively-precise point.

Each trial draws an independent multiplicative perturbation for every uncertain
physical input (mass, drag, frontal area, rolling resistance, aux load,
driveline efficiency, ATPE tier efficiency) - reusing the exact same scalers the
sensitivity study validated - and, for the cost/CO2 rollup, for every uncertain
economic input (fuel price, maintenance, battery cost + embodied CO2,
well-to-tank carbon, glider carbon). Perturbations are median-preserving
log-normals, so the nominal value sits at the median of each input's
distribution and stays strictly positive.

The output is a `Distribution` per metric: mean, standard deviation, and the
5th/50th/95th percentiles (a 90% credible band). Everything is seeded for exact
reproducibility and pure standard library.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, TwinConfig, phase1_config_for
from .drive_cycles import DriveCycle, DriveCycles
from .economics import EconomicsConfig, tco_for_body
from .fleet import run_fleet
from .powertrain import Powertrain
from .simulation import run
from .validation import Parameter, phase1_sensitivity_params

# Default 1-sigma *relative* uncertainty for each physical input, keyed by the
# Parameter.name used in the sensitivity study. These are representative
# build/operating variations (payload, tyre pressure, accessory duty, unit-to-
# unit efficiency spread), not measurement noise.
DEFAULT_PARAM_SIGMA: dict[str, float] = {
    "Vehicle mass": 0.05,          # payload + trim variation
    "Drag coefficient": 0.05,      # roof bars, mirrors, build spread
    "Frontal area": 0.02,
    "Rolling resistance": 0.10,    # tyre, pressure, road, temperature
    "Aux load": 0.20,              # climate + accessories vary widely
    "Driveline efficiency": 0.02,
    "ATPE tier efficiency": 0.03,  # unit-to-unit engine/generator spread
}

# Default 1-sigma relative uncertainty for each economic input.
DEFAULT_ECON_SIGMA: dict[str, float] = {
    "fuel_price_per_l": 0.15,
    "maintenance_per_km": 0.20,
    "battery_cost_per_kwh": 0.20,
    "battery_embodied_co2_kg_per_kwh": 0.20,
    "fuel_wtt_co2_kg_per_l": 0.20,
    "glider_embodied_co2_t": 0.15,
}


@dataclass(frozen=True)
class Distribution:
    """Summary of a metric's Monte-Carlo spread (a 90% band on p05..p95)."""

    name: str
    unit: str
    n: int
    nominal: float          # unperturbed point estimate, for reference
    mean: float
    std: float
    p05: float
    p50: float
    p95: float
    minimum: float
    maximum: float

    @property
    def rel_band_pct(self) -> float:
        """Width of the 90% band as a percentage of the median."""
        return (self.p95 - self.p05) / self.p50 * 100.0 if self.p50 else 0.0

    def band(self) -> str:
        return (f"{self.mean:.2f} +/- {self.std:.2f} {self.unit}  "
                f"90% CI [{self.p05:.2f}, {self.p95:.2f}]")

    def report(self) -> str:
        return "\n".join([
            f"=== {self.name} ({self.n} trials) ===",
            f"  nominal : {self.nominal:.3f} {self.unit}",
            f"  mean+/-sd: {self.mean:.3f} +/- {self.std:.3f} {self.unit}",
            f"  90% band: [{self.p05:.3f}, {self.p95:.3f}] {self.unit} "
            f"(+/-{self.rel_band_pct/2:.1f}% of median)",
            f"  range   : [{self.minimum:.3f}, {self.maximum:.3f}] {self.unit}",
        ])


@dataclass(frozen=True)
class TcoBands:
    """Uncertainty bands for one body's ownership-level headline numbers."""

    body: str
    fuel_l_per_100km: Distribution
    cost_per_km: Distribution
    lifecycle_co2_g_per_km: Distribution


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..1) of an ascending list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[int(pos)]
    frac = pos - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _summarize(name: str, unit: str, nominal: float,
               values: list[float]) -> Distribution:
    ordered = sorted(values)
    n = len(ordered)
    mean = statistics.fmean(ordered) if ordered else 0.0
    std = statistics.stdev(ordered) if n >= 2 else 0.0
    return Distribution(
        name=name, unit=unit, n=n, nominal=nominal, mean=mean, std=std,
        p05=_percentile(ordered, 0.05),
        p50=_percentile(ordered, 0.50),
        p95=_percentile(ordered, 0.95),
        minimum=ordered[0] if ordered else 0.0,
        maximum=ordered[-1] if ordered else 0.0,
    )


def _params_with_sigma(
        sigmas: dict[str, float]) -> list[tuple[Parameter, float]]:
    """Pair each sensitivity Parameter with its relative 1-sigma."""
    pairs: list[tuple[Parameter, float]] = []
    for p in phase1_sensitivity_params():
        sigma = sigmas.get(p.name)
        if sigma:
            pairs.append((p, sigma))
    return pairs


def _sample_config(base: TwinConfig, rng: random.Random,
                   params_sigma: list[tuple[Parameter, float]]) -> TwinConfig:
    """Apply an independent median-preserving log-normal draw to each input."""
    cfg = base
    for param, sigma in params_sigma:
        factor = rng.lognormvariate(0.0, sigma)  # median 1.0, strictly positive
        cfg = param.scale(cfg, factor)
    return cfg


def _sample_econ(base: EconomicsConfig, rng: random.Random,
                 sigmas: dict[str, float]) -> EconomicsConfig:
    kwargs = {field: getattr(base, field) * rng.lognormvariate(0.0, sigma)
              for field, sigma in sigmas.items()}
    return replace(base, **kwargs)


def _charge_sustaining(body: BodyStyle) -> TwinConfig:
    """Body config started at the charge-sustaining SoC (representative fuel)."""
    cfg = phase1_config_for(body)
    return replace(cfg, battery=replace(
        cfg.battery, initial_soc=cfg.battery.soc_target))


def monte_carlo_fuel(body: BodyStyle | None = None,
                     cycle: DriveCycle | None = None,
                     metric: str = "fuel_l_per_100km",
                     trials: int = 200, seed: int = 0,
                     sigmas: dict[str, float] | None = None) -> Distribution:
    """Band a single simulation metric under joint physical-input uncertainty."""
    body = body or PHASE1_BODIES[0]
    cycle = cycle or DriveCycles.highway()
    params_sigma = _params_with_sigma(sigmas or DEFAULT_PARAM_SIGMA)
    base = _charge_sustaining(body)
    nominal = getattr(run(Powertrain(base), cycle), metric)
    rng = random.Random(seed)
    values: list[float] = []
    for _ in range(trials):
        cfg = _sample_config(base, rng, params_sigma)
        values.append(getattr(run(Powertrain(cfg), cycle), metric))
    unit = "L/100km" if "fuel" in metric else ""
    return _summarize(f"{body.name} {cycle.name}: {metric}", unit,
                      nominal, values)


def monte_carlo_tco(body: BodyStyle | None = None,
                    trials: int = 96, seed: int = 0,
                    econ: EconomicsConfig | None = None,
                    param_sigmas: dict[str, float] | None = None,
                    econ_sigmas: dict[str, float] | None = None) -> TcoBands:
    """Band a body's blended fuel, cost/km and lifecycle CO2 jointly.

    Both the physical inputs (affecting fuel + tailpipe CO2 via the sim) and the
    economic inputs (affecting cost + embodied/upstream CO2) are perturbed
    together, so the bands reflect the full uncertainty an owner actually faces.
    """
    body = body or PHASE1_BODIES[0]
    econ = econ or EconomicsConfig()
    params_sigma = _params_with_sigma(param_sigmas or DEFAULT_PARAM_SIGMA)
    econ_sigmas = econ_sigmas or DEFAULT_ECON_SIGMA
    base = _charge_sustaining(body)

    def _tco_for(cfg: TwinConfig, e: EconomicsConfig):
        cells = run_fleet({body.name: lambda c=cfg: Powertrain(c)})
        return tco_for_body(cells, body.name, e)

    nom = _tco_for(base, econ)
    rng = random.Random(seed)
    fuel: list[float] = []
    cost: list[float] = []
    co2: list[float] = []
    for _ in range(trials):
        cfg = _sample_config(base, rng, params_sigma)
        e = _sample_econ(econ, rng, econ_sigmas)
        r = _tco_for(cfg, e)
        fuel.append(r.blended_fuel_l_per_100km)
        cost.append(r.cost_per_km)
        co2.append(r.lifecycle_co2_g_per_km)
    return TcoBands(
        body=body.name,
        fuel_l_per_100km=_summarize(
            f"{body.name}: blended fuel", "L/100km",
            nom.blended_fuel_l_per_100km, fuel),
        cost_per_km=_summarize(
            f"{body.name}: cost/km", "", nom.cost_per_km, cost),
        lifecycle_co2_g_per_km=_summarize(
            f"{body.name}: lifecycle CO2", "g/km",
            nom.lifecycle_co2_g_per_km, co2),
    )


def fleet_uncertainty(trials: int = 64, seed: int = 0,
                      bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
                      econ: EconomicsConfig | None = None) -> list[TcoBands]:
    """Monte-Carlo cost + lifecycle-CO2 bands for every body."""
    return [monte_carlo_tco(b, trials=trials, seed=seed + i, econ=econ)
            for i, b in enumerate(bodies)]


def uncertainty_table(bands: list[TcoBands]) -> str:
    """Compact per-body table of cost/km and lifecycle-CO2 confidence bands."""
    lines = [
        f"=== Fleet uncertainty bands ({bands[0].cost_per_km.n} trials, "
        "90% CI) ===" if bands else "=== Fleet uncertainty bands ===",
        f"  {'Body':<12}{'Cost/km (5-95%)':>22}{'CO2 g/km (5-95%)':>22}",
        "  " + "-" * 54,
    ]
    for b in bands:
        c = b.cost_per_km
        g = b.lifecycle_co2_g_per_km
        cost_s = f"{c.p50:.3f} [{c.p05:.3f}-{c.p95:.3f}]"
        co2_s = f"{g.p50:.0f} [{g.p05:.0f}-{g.p95:.0f}]"
        lines.append(f"  {b.body:<12}{cost_s:>22}{co2_s:>22}")
    lines.append("  (median [5th-95th percentile] under joint physical + "
                 "economic uncertainty)")
    return "\n".join(lines)
