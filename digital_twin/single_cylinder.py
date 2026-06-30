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


def simulate_free_piston(result: SingleCylinderResult,
                         cfg: FreePistonConfig,
                         steps: int = 360,
                         cycle_time_s: float | None = None) -> FreePistonResult:
    """Integrate one free-piston stroke with bounce-chamber coupling."""
    t_end = cycle_time_s if cycle_time_s is not None else 0.02
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


def tier_physics_profile(tier_index: int) -> tuple[float, FreePistonConfig]:
    """Per-tier compression ratio and free-piston geometry (micro / medium / large)."""
    profiles: tuple[tuple[float, FreePistonConfig], ...] = (
        (13.5, FreePistonConfig(mass_kg=1.2, piston_area_m2=0.0035,
                                stroke_m=0.060, bounce_clearance_m=0.006)),
        (12.5, FreePistonConfig(mass_kg=2.0, piston_area_m2=0.0055,
                                stroke_m=0.090, bounce_clearance_m=0.007)),
        (11.8, FreePistonConfig(mass_kg=3.2, piston_area_m2=0.0085,
                                stroke_m=0.120, bounce_clearance_m=0.009)),
    )
    idx = max(0, min(tier_index, len(profiles) - 1))
    return profiles[idx]


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
    fp = simulate_free_piston(comb, fp_cfg)
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
