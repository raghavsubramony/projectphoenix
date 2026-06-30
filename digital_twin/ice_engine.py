"""Conventional ICE: fixed-displacement turbo petrol engine-generator benchmark.

ATPE's entire efficiency claim rests on a comparison: "a free-piston tiered
generator beats a conventional engine at part load." Until that comparison
exists *inside the same harness*, the claim is asserted, not measured. This
module is the other half of the experiment.

To isolate *engine efficiency strategy* as the only variable, `ICEEngine`
implements the identical contract as :class:`digital_twin.atpe.ATPE` --
`generate(setpoint_w, dt_s) -> GenerationResult` -- so it drops into
:class:`digital_twin.powertrain.Powertrain` unchanged. This means the vehicle
model, inertial buffer, battery, and unified controller are *exactly* the same
for both engines; only the fuel->electrical conversion differs. That is the
correct experimental control: it answers "is the tiered free-piston combustion
strategy better?", not "is a generic hybrid better than a generic ICE?" (a much
less interesting and already well-answered question).

Physical model
--------------
A real turbocharged SI engine does not have a single thermal efficiency; it has
a *BSFC map* (brake-specific fuel consumption, g/kWh, as a function of speed and
load) shaped like a bowl, with a "sweet spot" at moderate-to-high load and
2000-3500 rpm, and steep efficiency losses at low load (pumping/throttling
losses) and at very high load (enrichment for knock/thermal protection).

Modelling full speed-load BSFC requires an rpm axis the rest of this twin does
not carry (ATPE's tiers are purely power-based, with no engine speed concept).
So this module uses the load-axis *projection* of a BSFC map -- thermal
efficiency as a function of fractional load only, evaluated at the engine's
best-efficiency operating line (the speed/gear a real ECU would choose for that
load, e.g. via a CVT-like ratio or a torque-converter lockup strategy seeking
the BSFC island). This is the standard simplification used for series-hybrid
range-extender sizing studies, and it is the most favorable honest assumption
for the conventional engine (it assumes perfect gearing always finds the best
BSFC point for the requested load -- ATPE gets no "we picked a bad gear"
advantage here).

Efficiency curve (representative 2.0L turbo, ~110 kW peak):
    fractional load    indicated/brake thermal efficiency
    0.05 (idle/crawl)        ~0.12  (heavy throttling loss)
    0.20                     ~0.24
    0.40                     ~0.32
    0.65 (BSFC sweet spot)   ~0.36  (peak)
    1.00 (full load)         ~0.31  (knock-limited enrichment)

These figures are representative published BSFC-map shapes for a modern
turbocharged direct-injection SI engine (peak BTE ~36-38% is consistent with
2020s Atkinson/Miller-leaning turbo-petrol units); they are not a specific
vendor's homologation data. Like ATPE's tier efficiencies, this is a
parametric model, not first-principles combustion -- which is the right level
of fidelity to compare against ATPE's own parametric tier efficiencies
apples-to-apples. Neither engine in this twin is modelled from Cantera-level
chemistry yet (see Gate 1).

The generator coupling (engine -> alternator -> rectifier -> DC bus) uses the
same `generator_efficiency` concept as ATPE for a fair comparison.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import (
    GASOLINE_CO2_KG_PER_L,
    GASOLINE_DENSITY_KG_PER_L,
    GASOLINE_LHV_MJ_PER_KG,
)

_LHV_J_PER_KG = GASOLINE_LHV_MJ_PER_KG * 1e6


@dataclass(frozen=True)
class BsfcPoint:
    """One (fractional load, thermal efficiency) point on the BSFC curve."""

    load_frac: float
    efficiency: float


@dataclass(frozen=True)
class ICEConfig:
    """Conventional turbo-petrol engine-generator, sized to match ATPE."""

    name: str = "2.0L Turbo ICE"
    peak_electric_w: float = 230_000.0  # matches ATPE's 30+80+120 kW stack
    # Best-efficiency-line BSFC curve: thermal efficiency vs fractional load.
    # Representative modern turbo-petrol DI engine (see module docstring).
    bsfc_curve: tuple[BsfcPoint, ...] = (
        BsfcPoint(0.00, 0.00),
        BsfcPoint(0.05, 0.12),
        BsfcPoint(0.10, 0.19),
        BsfcPoint(0.20, 0.24),
        BsfcPoint(0.40, 0.32),
        BsfcPoint(0.65, 0.36),   # BSFC sweet spot
        BsfcPoint(0.85, 0.34),
        BsfcPoint(1.00, 0.31),   # full-load enrichment penalty
    )
    generator_efficiency: float = 0.94  # conventional alternator, slightly
    # lower than ATPE's linear-generator (0.96) -- a rotating alternator with
    # brushes/slip losses is the honest conventional-tech comparison point.
    # Optional ramp-rate limit, identical concept to ATPE's, for apples-to-
    # apples transient comparisons. A geared ICE can usually ramp faster than
    # a free-piston unit (no combustion-stability constraint on stroke), so
    # the default is more permissive than ATPE's typical stress-test value.
    max_slew_w_per_s: float | None = None


@dataclass
class GenerationResult:
    """Identical shape to atpe.GenerationResult, for drop-in compatibility."""

    electric_w: float
    fuel_power_w: float
    fuel_l: float
    co2_kg: float
    active_tier: str
    active_index: int
    efficiency: float


def _interp_efficiency(load_frac: float, curve: tuple[BsfcPoint, ...]) -> float:
    """Piecewise-linear interpolation of the BSFC curve at `load_frac`."""
    load_frac = max(0.0, min(1.0, load_frac))
    if load_frac <= curve[0].load_frac:
        return curve[0].efficiency
    for lo, hi in zip(curve, curve[1:]):
        if lo.load_frac <= load_frac <= hi.load_frac:
            span = hi.load_frac - lo.load_frac
            if span <= 0.0:
                return hi.efficiency
            frac = (load_frac - lo.load_frac) / span
            return lo.efficiency + frac * (hi.efficiency - lo.efficiency)
    return curve[-1].efficiency


class ICEEngine:
    """Fixed-displacement turbo-petrol engine-generator.

    Implements the same `generate(setpoint_w, dt_s) -> GenerationResult`
    contract as `atpe.ATPE`, so it is a drop-in alternative inside
    `Powertrain`. Unlike ATPE there are no discrete tiers to select -- the
    single engine runs at whatever fractional load the setpoint implies,
    which is precisely the "always-on, can't shed displacement" behaviour
    that motivates ATPE's tiered architecture in the first place.
    """

    def __init__(self, cfg: ICEConfig) -> None:
        self.cfg = cfg
        self._prev_electric_w = 0.0

    @property
    def max_electric_w(self) -> float:
        return self.cfg.peak_electric_w

    def generate(self, setpoint_w: float, dt_s: float) -> GenerationResult:
        setpoint_w = max(0.0, min(setpoint_w, self.max_electric_w))
        slew = self.cfg.max_slew_w_per_s
        if slew is not None:
            max_step = slew * dt_s
            if setpoint_w > self._prev_electric_w + max_step:
                setpoint_w = self._prev_electric_w + max_step
            elif setpoint_w < self._prev_electric_w - max_step:
                setpoint_w = self._prev_electric_w - max_step
        self._prev_electric_w = setpoint_w

        if setpoint_w <= 0.0:
            return GenerationResult(0.0, 0.0, 0.0, 0.0, "engine off", -1, 0.0)

        load_frac = setpoint_w / self.cfg.peak_electric_w
        brake_thermal_eff = _interp_efficiency(load_frac, self.cfg.bsfc_curve)
        # Combined fuel->electrical efficiency includes the generator coupling,
        # mirroring how ATPE's TierSpec.thermal_efficiency is already a bundled
        # fuel->electrical figure (see config.ATPEConfig.best_brake_thermal_efficiency).
        combined_eff = brake_thermal_eff * self.cfg.generator_efficiency

        electric_w = setpoint_w
        fuel_power_w = electric_w / combined_eff if combined_eff > 0 else 0.0
        fuel_energy_j = fuel_power_w * dt_s
        fuel_kg = fuel_energy_j / _LHV_J_PER_KG
        fuel_l = fuel_kg / GASOLINE_DENSITY_KG_PER_L
        co2_kg = fuel_l * GASOLINE_CO2_KG_PER_L

        return GenerationResult(
            electric_w=electric_w,
            fuel_power_w=fuel_power_w,
            fuel_l=fuel_l,
            co2_kg=co2_kg,
            active_tier=f"ICE ({load_frac*100:.0f}% load)",
            active_index=0,
            efficiency=combined_eff,
        )
