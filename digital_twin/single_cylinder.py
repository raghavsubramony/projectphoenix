"""Gate 1 single-cylinder physics helpers.

This module upgrades fixed tier efficiencies with optional physics-derived
combustion and free-piston estimates while remaining dependency-light:

- Pure-Python 1D cycle surrogate (always available)
- Optional Cantera-backed chemistry path (if cantera is installed)
- Free-piston motion model for crankless TDC prediction

The public API is intentionally small and deterministic so it can feed map-style
lookups from the main ATPE model without pulling in heavy runtime coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SingleCylinderInputs:
    """Inputs for a single-cylinder thermodynamic cycle estimate."""

    speed_rpm: float
    load_fraction: float
    displacement_m3: float
    compression_ratio: float = 12.0
    intake_pressure_pa: float = 101_325.0
    intake_temp_k: float = 330.0
    equivalence_ratio: float = 1.0
    gamma: float = 1.35
    soc_deg: float = -15.0
    burn_duration_deg: float = 45.0
    generator_efficiency: float = 0.96
    mechanical_efficiency: float = 0.95


@dataclass(frozen=True)
class PressureTrace:
    """Cycle-resolved cylinder pressure trace and derived heat release."""

    crank_deg: tuple[float, ...]
    pressure_pa: tuple[float, ...]
    volume_m3: tuple[float, ...]
    heat_release_j: tuple[float, ...]


@dataclass(frozen=True)
class SingleCylinderResult:
    """Combustion outputs consumable by the ATPE macro model."""

    indicated_efficiency: float
    electric_efficiency: float
    imep_pa: float
    knock_index: float
    ca50_deg: float
    peak_pressure_pa: float
    trace: PressureTrace


@dataclass(frozen=True)
class FreePistonConfig:
    """Lumped free-piston dynamics parameters (single moving mass)."""

    mass_kg: float = 2.8
    piston_area_m2: float = 0.0065
    stroke_m: float = 0.11
    bounce_clearance_m: float = 0.008
    bounce_gamma: float = 1.35
    bounce_reference_pa: float = 180_000.0
    damping_n_per_ms: float = 80.0


@dataclass(frozen=True)
class FreePistonResult:
    """Predicted piston motion and top dead center location."""

    t_s: tuple[float, ...]
    x_m: tuple[float, ...]
    v_ms: tuple[float, ...]
    cylinder_pressure_pa: tuple[float, ...]
    bounce_pressure_pa: tuple[float, ...]
    predicted_tdc_m: float


@dataclass(frozen=True)
class Gate1Point:
    """Compact map point used by the ATPE model."""

    electric_efficiency: float
    imep_bar: float
    knock_index: float
    peak_pressure_bar: float
    predicted_tdc_mm: float


@dataclass(frozen=True)
class Gate1BenchTargets:
    """PHOENIX-X12 storyboard specs mapped to Gate 1 bench acceptance criteria.

    Seed-phase lab rig tests a *single* power cartridge first; the X12 ring
    targets (12 cartridges, 954 kW total) are the production design reference.
    Tolerances are initial bench-go/no-go bands, not homologation limits.
    """

    stroke_mm: float = 50.0          # dual-sided ±25 mm opposed motion
    stroke_tolerance_mm: float = 2.0
    peak_power_kw: float = 78.0        # per-cartridge linear generator output
    peak_power_tolerance_kw: float = 8.0
    core_temp_c: float = 800.0         # combustion chamber peak (thermal map)
    core_temp_tolerance_c: float = 100.0
    bearing_runout_mm: float = 0.03    # magnetic bearing centre stability
    bearing_runout_tolerance_mm: float = 0.02
    exhaust_temp_c: float = 620.0        # exhaust collector ring
    exhaust_temp_tolerance_c: float = 80.0
    generator_efficiency: float = 0.95   # ERS §4.5 piston -> electrical
    min_electric_efficiency: float = 0.28  # fuel -> electrical at sweet spot


@dataclass(frozen=True)
class Gate1BenchMeasurement:
    """Quantities a Gate 1 lab rig would instrument and log."""

    stroke_mm: float
    peak_power_kw: float
    core_temp_c: float
    bearing_runout_mm: float
    exhaust_temp_c: float
    electric_efficiency: float
    imep_bar: float
    peak_pressure_bar: float
    knock_index: float
    peak_piston_speed_ms: float


@dataclass(frozen=True)
class Gate1BenchCheck:
    """One measured quantity checked against its bench target."""

    name: str
    measured: float
    target: float
    tolerance: float
    unit: str
    passed: bool


@dataclass(frozen=True)
class Gate1BenchResult:
    """Full Gate 1 bench acceptance outcome for one operating point."""

    measurement: Gate1BenchMeasurement
    checks: tuple[Gate1BenchCheck, ...]

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))


def _slider_crank_volume(crank_deg: float, displacement_m3: float,
                         compression_ratio: float,
                         rod_ratio: float = 3.6) -> float:
    """Return cylinder volume at a crank angle using a slider-crank geometry."""
    theta = math.radians(crank_deg)
    v_clearance = displacement_m3 / (compression_ratio - 1.0)
    l = rod_ratio
    piston_norm = 0.5 * (1.0 - math.cos(theta)) + (
        l - math.sqrt(max(1e-12, l * l - (math.sin(theta) ** 2)))
    )
    return v_clearance + displacement_m3 * piston_norm


def _wiebe_fraction(theta_deg: float, soc_deg: float, dur_deg: float,
                    a: float = 5.0, m: float = 2.0) -> float:
    """Simple Wiebe burn fraction from start-of-combustion to burn end."""
    if theta_deg <= soc_deg:
        return 0.0
    if theta_deg >= soc_deg + dur_deg:
        return 1.0
    x = (theta_deg - soc_deg) / max(1e-9, dur_deg)
    return 1.0 - math.exp(-a * (x ** (m + 1.0)))


def _ca50_from_trace(crank: tuple[float, ...], hrr: tuple[float, ...]) -> float:
    total = sum(hrr)
    if total <= 0.0:
        return 0.0
    csum = 0.0
    for deg, dq in zip(crank, hrr):
        csum += dq
        if csum >= 0.5 * total:
            return deg
    return crank[-1] if crank else 0.0


def _surrogate_cycle(inp: SingleCylinderInputs) -> SingleCylinderResult:
    """Pure-Python 1D cycle surrogate (Otto + Wiebe heat release shaping)."""
    load = _clamp(inp.load_fraction, 0.05, 1.0)
    phi = _clamp(inp.equivalence_ratio, 0.75, 1.25)

    crank = tuple(float(d) for d in range(-180, 181))
    volumes = tuple(
        _slider_crank_volume(d, inp.displacement_m3, inp.compression_ratio)
        for d in crank
    )

    v_max = max(volumes)
    p_comp = [inp.intake_pressure_pa * ((v_max / v) ** inp.gamma)
              for v in volumes]

    # Fuel energy scaled by load and mild equivalence-ratio penalty.
    lambda_penalty = 1.0 - 0.07 * abs(phi - 1.0)
    gross_heat_j = (520.0 * inp.displacement_m3 * 1e6 / 500.0) * load * lambda_penalty

    burn_frac = tuple(
        _wiebe_fraction(d, inp.soc_deg, inp.burn_duration_deg)
        for d in crank
    )
    heat_release = []
    prev = 0.0
    for f in burn_frac:
        now = gross_heat_j * f
        heat_release.append(max(0.0, now - prev))
        prev = now

    # Pressure uplift from released heat (scaled to avoid unrealistically high peaks).
    p = []
    pressure_gain_scale = 0.23
    for base, q_cum, v in zip(p_comp, (gross_heat_j * f for f in burn_frac), volumes):
        thermal_uplift = pressure_gain_scale * q_cum / max(v, 1e-10)
        p.append(max(30_000.0, base + thermal_uplift))

    work_j = 0.0
    for i in range(len(crank) - 1):
        dp = 0.5 * (p[i] + p[i + 1])
        dv = volumes[i + 1] - volumes[i]
        work_j += dp * dv

    imep = work_j / max(inp.displacement_m3, 1e-12)
    fuel_energy_j = gross_heat_j / max(0.5, lambda_penalty)
    indicated = _clamp(work_j / max(1.0, fuel_energy_j), 0.15, 0.58)
    electric = _clamp(
        indicated * inp.mechanical_efficiency * inp.generator_efficiency,
        0.10,
        0.54,
    )

    # Knock surrogate: higher peak pressure, rich operation, and low speed worsen it.
    peak_p = max(p)
    speed_factor = _clamp(2500.0 / max(1200.0, inp.speed_rpm), 0.6, 1.5)
    knock = _clamp(
        0.6 * (peak_p / 8.0e6) + 0.5 * max(0.0, phi - 1.0) + 0.2 * speed_factor,
        0.0,
        2.0,
    )

    trace = PressureTrace(
        crank_deg=crank,
        pressure_pa=tuple(p),
        volume_m3=volumes,
        heat_release_j=tuple(heat_release),
    )
    return SingleCylinderResult(
        indicated_efficiency=indicated,
        electric_efficiency=electric,
        imep_pa=imep,
        knock_index=knock,
        ca50_deg=_ca50_from_trace(trace.crank_deg, trace.heat_release_j),
        peak_pressure_pa=peak_p,
        trace=trace,
    )


def _try_cantera_cycle(inp: SingleCylinderInputs) -> SingleCylinderResult | None:
    """Optional Cantera-backed estimate; returns None if unavailable/failed."""
    try:
        import cantera as ct  # type: ignore
    except Exception:
        return None

    try:
        # Lightweight chemistry signal: equilibrium adiabatic flame temperature.
        gas = ct.Solution("gri30.yaml")
        gas.TP = inp.intake_temp_k, inp.intake_pressure_pa
        gas.set_equivalence_ratio(inp.equivalence_ratio, "CH4:1", "O2:1, N2:3.76")
        gas.equilibrate("HP")
        dT = max(0.0, gas.T - inp.intake_temp_k)
        base = _surrogate_cycle(inp)

        # Blend surrogate with chemistry-derived sensitivity.
        chem_factor = _clamp(1.0 + 0.00008 * (dT - 1200.0), 0.9, 1.1)
        electric = _clamp(base.electric_efficiency * chem_factor, 0.10, 0.58)
        imep = base.imep_pa * chem_factor
        knock = _clamp(base.knock_index * (1.0 + 0.00005 * max(0.0, dT - 1500.0)), 0.0, 2.0)
        indicated = _clamp(electric / max(1e-6, inp.generator_efficiency * inp.mechanical_efficiency),
                           0.15, 0.62)
        return SingleCylinderResult(
            indicated_efficiency=indicated,
            electric_efficiency=electric,
            imep_pa=imep,
            knock_index=knock,
            ca50_deg=base.ca50_deg,
            peak_pressure_pa=base.peak_pressure_pa * chem_factor,
            trace=base.trace,
        )
    except Exception:
        return None


def simulate_1d_combustion(inp: SingleCylinderInputs,
                           prefer_cantera: bool = True) -> SingleCylinderResult:
    """Compute a single-cylinder cycle using Cantera if available, else fallback."""
    if prefer_cantera:
        r = _try_cantera_cycle(inp)
        if r is not None:
            return r
    return _surrogate_cycle(inp)


def _cycle_period_s(speed_rpm: float) -> float:
    """Map engine speed to one combustion cycle period (Gate 2 timing)."""
    return 60.0 / max(600.0, speed_rpm)


def simulate_free_piston(result: SingleCylinderResult,
                         cfg: FreePistonConfig,
                         steps: int = 360,
                         cycle_time_s: float | None = None,
                         speed_rpm: float | None = None) -> FreePistonResult:
    """Integrate one free-piston stroke with bounce-chamber coupling."""
    if cycle_time_s is not None:
        t_end = cycle_time_s
    elif speed_rpm is not None:
        t_end = _cycle_period_s(speed_rpm)
    else:
        t_end = 0.02
    dt = t_end / max(steps, 1)

    x = 0.5 * cfg.stroke_m
    v = 0.0
    t_hist: list[float] = []
    x_hist: list[float] = []
    v_hist: list[float] = []
    p_c_hist: list[float] = []
    p_b_hist: list[float] = []

    p_min = min(result.trace.pressure_pa)
    p_max = max(result.trace.pressure_pa)

    for i in range(steps + 1):
        t = i * dt
        frac = i / max(steps, 1)
        # Replay one pressure pulse over the integration horizon.
        idx = min(int(frac * (len(result.trace.pressure_pa) - 1)),
                  len(result.trace.pressure_pa) - 1)
        p_cyl = result.trace.pressure_pa[idx]

        # Bounce chamber pressure rises sharply near minimum clearance.
        bounce_len = max(1e-6, cfg.bounce_clearance_m + x)
        p_bounce = cfg.bounce_reference_pa * ((cfg.bounce_clearance_m + cfg.stroke_m)
                                              / bounce_len) ** cfg.bounce_gamma

        force = (p_cyl - p_bounce) * cfg.piston_area_m2 - cfg.damping_n_per_ms * v
        a = force / max(cfg.mass_kg, 1e-6)
        v += a * dt
        x += v * dt
        x = _clamp(x, 0.0, cfg.stroke_m)

        t_hist.append(t)
        x_hist.append(x)
        v_hist.append(v)
        p_c_hist.append(_clamp(p_cyl, p_min, p_max))
        p_b_hist.append(p_bounce)

    return FreePistonResult(
        t_s=tuple(t_hist),
        x_m=tuple(x_hist),
        v_ms=tuple(v_hist),
        cylinder_pressure_pa=tuple(p_c_hist),
        bounce_pressure_pa=tuple(p_b_hist),
        predicted_tdc_m=min(x_hist) if x_hist else 0.0,
    )


# PHOENIX-X12 storyboard reference (designs/create the scenes... concept PNG).
PHOENIX_X12_STROKE_MM = 50.0       # ±25 mm dual-sided opposed motion
PHOENIX_X12_CARTRIDGE_KW = 78.0
PHOENIX_X12_CORE_TEMP_C = 800.0
PHOENIX_X12_BEARING_MM = 0.03
PHOENIX_X12_EXHAUST_TEMP_C = 620.0

# Per-tier cartridge nameplate peak (kW) for bench load-bank rating checks.
TIER_CARTRIDGE_PEAK_KW: tuple[float, ...] = (20.0, PHOENIX_X12_CARTRIDGE_KW, 120.0)
# Reference fuel→electrical efficiency at 75 % load for bench power normalisation.
TIER_BENCH_REF_EFFICIENCY: tuple[float, ...] = (0.51, 0.50, 0.50)


def tier_physics_profile(tier_index: int) -> tuple[float, FreePistonConfig]:
    """Per-tier compression ratio and free-piston geometry (micro / medium / large)."""
    x12_stroke_m = PHOENIX_X12_STROKE_MM / 1000.0
    profiles: tuple[tuple[float, FreePistonConfig], ...] = (
        (13.5, FreePistonConfig(mass_kg=1.2, piston_area_m2=0.0035,
                                stroke_m=0.060, bounce_clearance_m=0.006)),
        (12.5, FreePistonConfig(mass_kg=2.0, piston_area_m2=0.0055,
                                stroke_m=x12_stroke_m, bounce_clearance_m=0.007)),
        (11.8, FreePistonConfig(mass_kg=3.2, piston_area_m2=0.0085,
                                stroke_m=0.120, bounce_clearance_m=0.009)),
    )
    idx = max(0, min(tier_index, len(profiles) - 1))
    return profiles[idx]


def _estimate_core_temp_c(peak_pressure_bar: float, imep_bar: float) -> float:
    """Surrogate peak gas temperature from cycle severity (bench planning metric)."""
    return _clamp(450.0 + 55.0 * peak_pressure_bar + 12.0 * imep_bar, 400.0, 950.0)


def _estimate_exhaust_temp_c(core_temp_c: float) -> float:
    """Exhaust collector temperature after expansion and wall loss."""
    return _clamp(core_temp_c * 0.78, 350.0, 750.0)


def _bearing_runout_mm(fp: FreePistonResult, cfg: FreePistonConfig,
                       knock_index: float = 0.0) -> float:
    """Mag-bearing centre stability proxy (sub-mm runout, not stroke travel)."""
    if len(fp.v_ms) < 2:
        return PHOENIX_X12_BEARING_MM
    v_mean = sum(abs(v) for v in fp.v_ms) / len(fp.v_ms)
    v_ripple = max(abs(abs(v) - v_mean) for v in fp.v_ms) if v_mean > 1e-6 else 0.0
    ripple_frac = v_ripple / max(v_mean, 1e-6)
    base = 0.008 + 0.010 * min(knock_index, 1.5)
    return _clamp(base + 0.018 * ripple_frac, 0.005, 0.048)


def _peak_piston_speed_ms(fp: FreePistonResult) -> float:
    return max((abs(v) for v in fp.v_ms), default=0.0)


def _bench_electrical_power_kw(
    comb: SingleCylinderResult,
    inp: SingleCylinderInputs,
    tier_index: int,
) -> float:
    """Load-bank electrical power at the operating point (bench rating surrogate).

    Uses tier nameplate × load × efficiency normalisation so the metric tracks
    combustion physics without the free-piston integrator's peak-velocity spikes.
    Vehicle fuel uses ``electric_efficiency`` directly and is unaffected.
    """
    idx = max(0, min(tier_index, len(TIER_CARTRIDGE_PEAK_KW) - 1))
    nameplate_kw = TIER_CARTRIDGE_PEAK_KW[idx]
    ref_eff = TIER_BENCH_REF_EFFICIENCY[idx]
    return nameplate_kw * inp.load_fraction * (comb.electric_efficiency / ref_eff)


def measure_gate1_bench(comb: SingleCylinderResult, fp: FreePistonResult,
                        cfg: FreePistonConfig,
                        inp: SingleCylinderInputs,
                        tier_index: int = 1,
                        generator_efficiency: float = 0.96) -> Gate1BenchMeasurement:
    """Derive instrumented bench quantities from a simulated cycle."""
    core = _estimate_core_temp_c(comb.peak_pressure_pa / 1e5, comb.imep_pa / 1e5)
    return Gate1BenchMeasurement(
        stroke_mm=cfg.stroke_m * 1000.0,
        peak_power_kw=_bench_electrical_power_kw(comb, inp, tier_index),
        core_temp_c=core,
        bearing_runout_mm=_bearing_runout_mm(fp, cfg, comb.knock_index),
        exhaust_temp_c=_estimate_exhaust_temp_c(core),
        electric_efficiency=comb.electric_efficiency,
        imep_bar=comb.imep_pa / 1e5,
        peak_pressure_bar=comb.peak_pressure_pa / 1e5,
        knock_index=comb.knock_index,
        peak_piston_speed_ms=_peak_piston_speed_ms(fp),
    )


def _bench_check(name: str, measured: float, target: float, tolerance: float,
                 unit: str, *, higher_is_better: bool = True) -> Gate1BenchCheck:
    if higher_is_better:
        passed = measured + tolerance >= target
    else:
        passed = measured <= target + tolerance
    return Gate1BenchCheck(name, measured, target, tolerance, unit, passed)


def evaluate_gate1_bench(
    measurement: Gate1BenchMeasurement,
    targets: Gate1BenchTargets | None = None,
    *,
    load_fraction: float = 1.0,
    tier_index: int = 1,
) -> Gate1BenchResult:
    """Compare simulated bench measurements against PHOENIX-X12 acceptance bands."""
    t = targets or Gate1BenchTargets()
    idx = max(0, min(tier_index, len(TIER_CARTRIDGE_PEAK_KW) - 1))
    peak_target_kw = TIER_CARTRIDGE_PEAK_KW[idx] * load_fraction
    peak_tol_kw = t.peak_power_tolerance_kw * load_fraction
    checks = (
        _bench_check("Stroke", measurement.stroke_mm, t.stroke_mm,
                     t.stroke_tolerance_mm, "mm"),
        _bench_check("Peak power", measurement.peak_power_kw, peak_target_kw,
                     peak_tol_kw, "kW"),
        _bench_check("Core temperature", measurement.core_temp_c, t.core_temp_c,
                     t.core_temp_tolerance_c, "C"),
        _bench_check("Bearing runout", measurement.bearing_runout_mm,
                     t.bearing_runout_mm, t.bearing_runout_tolerance_mm, "mm",
                     higher_is_better=False),
        _bench_check("Exhaust temperature", measurement.exhaust_temp_c,
                     t.exhaust_temp_c, t.exhaust_temp_tolerance_c, "C"),
        _bench_check("Electric efficiency", measurement.electric_efficiency,
                     t.min_electric_efficiency, 0.0, "", higher_is_better=True),
    )
    return Gate1BenchResult(measurement=measurement, checks=checks)


def gate1_bench_at_load(
    speed_rpm: float = 2600.0,
    load_fraction: float = 0.75,
    displacement_cc: float = 300.0,
    generator_efficiency: float = 0.96,
    prefer_cantera: bool = True,
    tier_index: int = 1,
    targets: Gate1BenchTargets | None = None,
) -> Gate1BenchResult:
    """Simulate one cartridge at load and evaluate Gate 1 bench acceptance."""
    compression_ratio, fp_cfg = tier_physics_profile(tier_index)
    inp = SingleCylinderInputs(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_m3=max(1e-7, displacement_cc * 1e-6),
        compression_ratio=compression_ratio,
        generator_efficiency=generator_efficiency,
    )
    comb = simulate_1d_combustion(inp, prefer_cantera=prefer_cantera)
    fp = simulate_free_piston(comb, fp_cfg, speed_rpm=speed_rpm)
    meas = measure_gate1_bench(comb, fp, fp_cfg, inp, tier_index, generator_efficiency)
    return evaluate_gate1_bench(
        meas, targets, load_fraction=load_fraction, tier_index=tier_index,
    )


def gate1_point_from_load(speed_rpm: float, load_fraction: float,
                          displacement_cc: float,
                          generator_efficiency: float,
                          prefer_cantera: bool = True,
                          tier_index: int = 0) -> Gate1Point:
    """Convenience adapter: load/speed -> map point for the ATPE tier model."""
    compression_ratio, fp_cfg = tier_physics_profile(tier_index)
    inp = SingleCylinderInputs(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_m3=max(1e-7, displacement_cc * 1e-6),
        compression_ratio=compression_ratio,
        generator_efficiency=generator_efficiency,
    )
    comb = simulate_1d_combustion(inp, prefer_cantera=prefer_cantera)
    fp = simulate_free_piston(comb, fp_cfg, speed_rpm=speed_rpm)
    return Gate1Point(
        electric_efficiency=comb.electric_efficiency,
        imep_bar=comb.imep_pa / 1e5,
        knock_index=comb.knock_index,
        peak_pressure_bar=comb.peak_pressure_pa / 1e5,
        predicted_tdc_mm=fp.predicted_tdc_m * 1000.0,
    )


def gate1_cycle_trace(speed_rpm: float, load_fraction: float,
                      displacement_cc: float,
                      generator_efficiency: float,
                      prefer_cantera: bool = True,
                      tier_index: int = 0) -> SingleCylinderResult:
    """Full cycle trace for one tier at a load point (dashboard / comparison)."""
    compression_ratio, _ = tier_physics_profile(tier_index)
    inp = SingleCylinderInputs(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_m3=max(1e-7, displacement_cc * 1e-6),
        compression_ratio=compression_ratio,
        generator_efficiency=generator_efficiency,
    )
    return simulate_1d_combustion(inp, prefer_cantera=prefer_cantera)
