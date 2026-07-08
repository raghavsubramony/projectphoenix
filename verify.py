"""Project Phoenix - one-command verification.

Run with:

    .venv\\Scripts\\python.exe verify.py
    .venv\\Scripts\\python.exe verify.py --quiet     # summary only
    .venv\\Scripts\\python.exe verify.py --quick      # smoke subset (~10 s)

This is the single entry point that proves the whole concept reproduces. It:

  1. Re-derives every headline number from the *live* code and checks it against
     the validated value (so silent drift is caught, not just crashes).
  2. Confirms the key robustness/monotonicity claims hold (buffer scaling kills
     transient shortfalls; heavier vehicles burn more fuel).
  3. Runs the full unit-test suite.

It prints a single PASS/FAIL banner and exits non-zero on any failure, so it is
usable as a CI gate. Pure standard library - no third-party dependencies.
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

from digital_twin import (
    DriveCycles,
    PHASE1_BODIES,
    RotorSet,
    build_body_twins,
    capability_sweep,
    charge_sustaining_bodies,
    closed_loop_rotor_bodies,
    couple_buffer,
    evaluate,
    fleet_tco,
    fleet_battery_sizing,
    fleet_uncertainty,
    fleet_ambient,
    fleet_regulatory,
    RegulatoryCycles,
    build_executive_summary,
    fleet_cold_start,
    fleet_payload,
    fleet_phev,
    GridConfig,
    fleet_degradation,
    monte_carlo_fuel,
    phase1_body_targets,
    phase1_config,
    phase1_config_for,
    rotor_peak_reaction_torque_nm,
    rotor_transient_power_w,
    run,
    run_fleet,
    sensitivity_table,
    simulate_torque_augmentation,
    stress_bodies,
    with_battery_thermal,
    wltp_benchmark,
    run_ice_fleet,
    fleet_graceful_degradation,
    gate1_bench_at_load,
    PHOENIX_X12_STROKE_MM,
    run_gate1_matrix,
    gate1_bench_uncertainty,
    gate1_vehicle_fuel_comparison,
    run_gate4_sweep,
    run_ring_size_study,
    enumerate_ring_layouts,
    DEFAULT_RING_SIZES,
    is_design_aligned,
    count_active_tiers,
    evaluate_layout,
    CylinderLayout,
    REFERENCE_LAYOUT,
)
from dataclasses import replace
from digital_twin.powertrain import Powertrain


@dataclass
class Check:
    """One verification check with its measured vs expected value."""

    name: str
    ok: bool
    detail: str


def _approx(name: str, value: float, expected: float, tol: float,
            unit: str = "") -> Check:
    ok = abs(value - expected) <= tol
    detail = (f"{value:.4g}{unit} vs {expected:.4g}{unit} "
              f"(+/-{tol:g}{unit})")
    return Check(name, ok, detail)


# --- headline-number invariants (re-derived from live code) -----------------

def _check_rotor() -> list[Check]:
    r = simulate_torque_augmentation()
    rotors = RotorSet()
    return [
        _approx("Rotor peak torque", r.peak_nm, 242.8, 0.1, " N.m"),
        _approx("Rotor boost", r.boost_percent, 34.9, 0.1, " %"),
        _approx("Rotor stored energy", r.stored_energy_mj, 0.118, 0.001, " MJ"),
        _approx("Rotor reaction torque",
                rotor_peak_reaction_torque_nm(rotors), 62.8, 0.2, " N.m"),
        _approx("Rotor transient power",
                rotor_transient_power_w(rotors) / 1000, 50.2, 0.3, " kW"),
    ]


def _check_coupling() -> list[Check]:
    base = phase1_config().buffer
    coupled = couple_buffer(base)
    burst_w = coupled.peak_transient_w or 0.0
    checks = [
        _approx("Coupled buffer burst",
                burst_w / 1000, 140.2, 0.3, " kW"),
        Check("Coupling is opt-in (off by default)",
              phase1_config().buffer.peak_transient_w is None,
              "buffer.peak_transient_w is None"),
    ]
    return checks


def _check_fuel_economy() -> list[Check]:
    cells = {(c.body, c.cycle): c
             for c in run_fleet(charge_sustaining_bodies())}
    suv_hwy = next(c for (b, cy), c in cells.items()
                   if b == "AWD SUV" and "Highway" in cy)
    checks = [_approx("SUV highway fuel", suv_hwy.fuel_l_per_100km,
                      4.46, 0.05, " L/100km")]
    # Urban must be pure-EV (zero fuel) for every body.
    urban_ev = all(abs(c.fuel_l_per_100km) < 1e-6
                   for (b, cy), c in cells.items() if "Urban" in cy)
    checks.append(Check("Urban is pure-EV (all bodies)", urban_ev,
                        "all urban cells burn 0 L"))
    # No body shows a capability shortfall on the standard cycles.
    no_short = all(c.shortfall_events == 0 for c in cells.values())
    checks.append(Check("No shortfalls on standard cycles", no_short,
                        "every (body,cycle) shortfall_events == 0"))
    return checks


def _check_acceptance() -> list[Check]:
    builders = build_body_twins()
    targets = phase1_body_targets()
    failed_bodies = []
    for name, build in builders.items():
        failed = [c.name for c in evaluate(build, targets[name]) if not c.passed]
        if failed:
            failed_bodies.append(f"{name}:{failed}")
    return [Check(f"All {len(builders)} bodies pass all ERS checks",
                  not failed_bodies,
                  "9/9 per body" if not failed_bodies
                  else "; ".join(failed_bodies))]


def _check_durability() -> list[Check]:
    cfg = with_battery_thermal(phase1_config_for(PHASE1_BODIES[0]))
    cfg = replace(cfg, battery=replace(
        cfg.battery, initial_soc=cfg.battery.soc_target))
    res = run(Powertrain(cfg), DriveCycles.highway())
    thermal = cfg.battery.thermal
    derate_c = thermal.derate_start_c if thermal else 45.0
    return [
        Check("Charge-sustaining barely cycles pack",
              res.battery_efc_per_100km < 1.0,
              f"{res.battery_efc_per_100km:.3f} EFC/100km"),
        Check("Projected pack life > 500k km",
              res.projected_pack_life_km > 500_000,
              f"{res.projected_pack_life_km/1000:.0f}k km"),
        Check("Pack stays below thermal derate in-cycle",
              res.battery_peak_temp_c < derate_c,
              f"peak {res.battery_peak_temp_c:.1f} C "
              f"< {derate_c:.0f} C"),
    ]


# --- robustness / monotonicity claims ---------------------------------------

def _check_robustness() -> list[Check]:
    checks: list[Check] = []
    # Buffer reservoir scaling must monotonically remove transient shortfalls,
    # and 2x must already eliminate them entirely.
    pts = capability_sweep([1.0, 2.0, 3.0, 4.0], metric="shortfall_events")
    vals = [p.metric for p in pts]
    monotone = all(b <= a + 1e-9 for a, b in zip(vals, vals[1:]))
    checks.append(Check("Bigger buffer never adds shortfalls (monotone)",
                        monotone, f"events: {[int(v) for v in vals]}"))
    checks.append(Check("2x buffer eliminates transient shortfalls",
                        vals[1] == 0, f"{int(vals[1])} events at 2x"))
    # Heavier vehicle must burn more fuel (positive mass elasticity).
    table = sensitivity_table(cycle=DriveCycles.highway())
    mass = next(e for e in table if e.parameter == "Vehicle mass")
    checks.append(Check("Heavier vehicle burns more fuel (elasticity > 0)",
                        mass.elasticity > 0,
                        f"mass elasticity {mass.elasticity:+.2f}"))
    # Aero must dominate mass at highway speed.
    drag = next(e for e in table if e.parameter == "Drag coefficient")
    checks.append(Check("Aero dominates mass at highway speed",
                        drag.abs_elasticity > mass.abs_elasticity,
                        f"Cd {drag.elasticity:+.2f} vs mass {mass.elasticity:+.2f}"))
    return checks


def _check_economics() -> list[Check]:
    checks: list[Check] = []
    results = fleet_tco(run_fleet(charge_sustaining_bodies()))
    # The throughput-bound pack outlasts the 250k km vehicle life: no body needs
    # a mid-life battery replacement.
    replacements = sum(r.battery_replacements for r in results)
    checks.append(Check("Pack never replaced over vehicle life",
                        replacements == 0,
                        f"{replacements} replacements across fleet"))
    # The small 20 kWh pack's embodied CO2 is a minor share of lifecycle CO2.
    suv = next(r for r in results if r.body == "AWD SUV")
    share = suv.embodied_battery_co2_t / suv.lifecycle_co2_t
    checks.append(Check("Embodied battery CO2 is a small lifecycle share",
                        share < 0.15,
                        f"{share*100:.1f}% of {suv.lifecycle_co2_t:.1f} t"))
    return checks


def _check_closed_loop() -> list[Check]:
    checks: list[Check] = []
    # Closed-loop surge arbitration must never worsen capability vs static
    # coupling on the representative stress fleet (it is energy-bound there).
    static = {(c.body, c.cycle): c for c in
              run_fleet(stress_bodies(rotor_coupled=True, battery_derate_w=40_000.0))}
    closed = {(c.body, c.cycle): c for c in
              run_fleet(closed_loop_rotor_bodies(battery_derate_w=40_000.0))}
    worse = sum(1 for k, s in static.items()
                if closed[k].shortfall_events > s.shortfall_events)
    checks.append(Check("Closed-loop surge control never worsens shortfalls",
                        worse == 0, f"{worse}/{len(static)} runs worse"))
    return checks


def _check_sizing() -> list[Check]:
    checks: list[Check] = []
    results = fleet_battery_sizing()
    # Every body must be capable within the swept battery-power range.
    incapable = [r.body for r in results if not r.feasible]
    checks.append(Check("Every body right-sizes to a capable battery power",
                        not incapable, f"incapable: {incapable or 'none'}"))
    # The validated 120 kW default must be over-specced: at least one body needs
    # strictly less, and none needs more.
    over = all(r.recommended_power_w <= r.default_power_w for r in results)
    downsizes = sum(1 for r in results if r.downsize_w > 0)
    checks.append(Check("Default pack power is over-specced (down-sizable)",
                        over and downsizes > 0,
                        f"{downsizes}/{len(results)} bodies down-size"))
    return checks


def _check_uncertainty() -> list[Check]:
    checks: list[Check] = []
    # The validated SUV highway fuel must sit inside its own Monte-Carlo 90%
    # band (the point estimate is the median of the joint-uncertainty spread).
    d = monte_carlo_fuel(trials=64, seed=0)
    inside = d.p05 <= d.nominal <= d.p95 and d.std > 0.0
    checks.append(Check("SUV highway fuel nominal lies in its 90% MC band",
                        inside,
                        f"{d.nominal:.2f} in [{d.p05:.2f}, {d.p95:.2f}]"))
    # Fleet cost/CO2 bands must be well-ordered and positive for every body.
    bands = fleet_uncertainty(trials=24, seed=0)
    ok = all(0.0 < b.cost_per_km.p05 <= b.cost_per_km.p50 <= b.cost_per_km.p95
             and b.lifecycle_co2_g_per_km.p05 <= b.lifecycle_co2_g_per_km.p95
             for b in bands)
    checks.append(Check("Fleet cost + CO2 uncertainty bands are well-formed",
                        ok, f"{len(bands)} bodies banded"))
    return checks


def _check_ambient() -> list[Check]:
    checks: list[Check] = []
    sweeps = fleet_ambient()
    # Cold weather must cost fuel on every body (denser air + cabin heating).
    cold_worse = all(s.points[0].fuel_l_per_100km > s.reference.fuel_l_per_100km
                     for s in sweeps)
    checks.append(Check("Cold ambient raises fuel use on every body",
                        cold_worse, f"{len(sweeps)} bodies swept"))
    # The over-specced pack must never thermally derate under realistic duty,
    # even on a +40 C day.
    no_derate = all(not s.any_derate for s in sweeps)
    checks.append(Check("Pack never thermally derates from -10C to +40C",
                        no_derate, "peak cell temp stays below derate"))
    return checks


def _check_regulatory() -> list[Check]:
    checks: list[Check] = []
    # Reconstructed WLTP / EPA cycles must match the published energy envelope.
    cycles = RegulatoryCycles.all()
    worst = 0.0
    for rc in cycles:
        st, sp = rc.stats(), rc.spec
        for built, pub in ((st.distance_km, sp.distance_km),
                           (st.avg_speed_kmh, sp.avg_speed_kmh)):
            worst = max(worst, abs(built - pub) / pub * 100.0)
    checks.append(Check("WLTP/EPA reconstructions match published envelope",
                        worst <= 10.0, f"worst dist/avg error {worst:.0f}%"))
    # Every body must complete every regulatory cycle without shortfalls.
    econ = fleet_regulatory()
    no_short = all(r.shortfall_events == 0 for r in econ)
    checks.append(Check("Fleet completes every regulatory cycle (no shortfalls)",
                        no_short, f"{len(econ)} body x cycle runs"))
    return checks


def _check_summary() -> list[Check]:
    checks: list[Check] = []
    # The executive summary must faithfully echo the validated headlines.
    s = build_executive_summary(trials=16)
    ok = (abs(s.rotor_peak_nm - 242.8) < 0.5 and s.pack_replacements == 0
          and not s.derates_in_climate and s.regulatory_shortfalls == 0
          and len(s.bodies) == 6 and s.graceful_degraded_pass
          and s.ice_mixed_saving_pct > 0.0)
    checks.append(Check("Executive summary echoes the validated headlines",
                        ok, f"{len(s.bodies)} bodies, rotor "
                        f"{s.rotor_peak_nm:.1f} N.m, ICE saving "
                        f"{s.ice_mixed_saving_pct:.1f}%"))
    suv = s.bodies[0]
    checks.append(Check("Executive summary includes Moves J-M (SUV)",
                        suv.cold_penalty_pct > 0.0 and suv.payload_penalty_pct > 0.0
                        and suv.fuel_drift_pct > 0.0 and suv.range_loss_pct > 0.0,
                        f"cold +{suv.cold_penalty_pct:.1f}%, payload "
                        f"+{suv.payload_penalty_pct:.1f}%, drift "
                        f"+{suv.fuel_drift_pct:.1f}%"))
    return checks


def _check_ice_benchmark() -> list[Check]:
    checks: list[Check] = []
    mixed = DriveCycles.mixed()
    atpe = next(c for c in run_fleet(charge_sustaining_bodies(), [mixed])
                if c.body == "AWD SUV")
    ice = next(c for c in run_ice_fleet(cycles=[mixed])
               if c.body == "AWD SUV")
    saving = (100.0 * (ice.fuel_l_per_100km - atpe.fuel_l_per_100km)
              / ice.fuel_l_per_100km if ice.fuel_l_per_100km > 0 else 0.0)
    checks.append(Check("ATPE beats conventional ICE on mixed cycle (SUV)",
                        atpe.fuel_l_per_100km < ice.fuel_l_per_100km,
                        f"ATPE {atpe.fuel_l_per_100km:.2f} vs ICE "
                        f"{ice.fuel_l_per_100km:.2f} L/100km (-{saving:.0f}%)"))
    wltp = wltp_benchmark()
    checks.append(Check("ICE benchmark report renders",
                        "ATPE vs conventional" in wltp,
                        f"{len(wltp)} chars"))
    return checks


def _check_graceful_degradation() -> list[Check]:
    checks: list[Check] = []
    results = fleet_graceful_degradation()
    all_ok = all(r.all_passed for r in results)
    checks.append(Check("One cylinder offline: all bodies pass ERS",
                        all_ok,
                        f"{sum(1 for r in results if r.all_passed)}/{len(results)} scenarios"))
    tier1 = next(r for r in results if "Tier 1" in r.scenario and r.body == "AWD SUV")
    checks.append(Check("Degraded stack loses peak power (Tier 1 fault)",
                        tier1.peak_power_kw < 230.0,
                        f"{tier1.peak_power_kw:.0f} kW peak"))
    return checks


def _check_gate1_bench() -> list[Check]:
    checks: list[Check] = []
    bench = gate1_bench_at_load(prefer_cantera=False, tier_index=1)
    checks.append(Check("Gate 1 bench simulation produces measurements",
                        bench.measurement.peak_power_kw > 0.0,
                        f"{bench.measurement.peak_power_kw:.1f} kW, "
                        f"stroke {bench.measurement.stroke_mm:.1f} mm"))
    checks.append(Check("PHOENIX-X12 medium-tier stroke matches storyboard",
                        abs(bench.measurement.stroke_mm - PHOENIX_X12_STROKE_MM) < 0.1,
                        f"{bench.measurement.stroke_mm:.1f} mm vs "
                        f"{PHOENIX_X12_STROKE_MM:.1f} mm"))
    passed = sum(1 for c in bench.checks if c.passed)
    checks.append(Check("Gate 1 bench checks are well-formed",
                        len(bench.checks) >= 5,
                        f"{passed}/{len(bench.checks)} criteria pass"))
    return checks


def _check_gate1_matrix() -> list[Check]:
    checks: list[Check] = []
    matrix = run_gate1_matrix(prefer_cantera=False)
    checks.append(Check("Gate 1 virtual bench matrix has expected cells",
                        matrix.total_cells == 48,
                        f"{matrix.total_cells} cells"))
    sweet = matrix.sweet_spot()
    sweet_passed = sum(1 for c in sweet.bench.checks if c.passed)
    checks.append(Check("Gate 1 sweet-spot bench produces checks",
                        sweet_passed >= 5,
                        f"{sweet_passed}/{len(sweet.bench.checks)} criteria pass"))
    checks.append(Check("Gate 1 virtual bench matrix pass rate",
                        matrix.pass_rate_pct >= 80.0,
                        f"{matrix.cells_passed}/{matrix.total_cells} cells "
                        f"({matrix.pass_rate_pct:.0f}%)"))
    bands = gate1_bench_uncertainty(trials=24, seed=0, prefer_cantera=False)
    eta_band = bands["electric_efficiency"]
    checks.append(Check("Gate 1 bench uncertainty band is well-formed",
                        eta_band.p05 <= eta_band.p50 <= eta_band.p95,
                        eta_band.band()))
    vehicle = gate1_vehicle_fuel_comparison(prefer_cantera=False)
    checks.append(Check("Gate 1 vehicle fuel path is finite",
                        vehicle.fuel_l_per_100km_gate1 > 0.0,
                        f"tables {vehicle.fuel_l_per_100km_tables:.2f} vs "
                        f"gate1 {vehicle.fuel_l_per_100km_gate1:.2f} L/100km"))
    return checks


def _check_coldstart() -> list[Check]:
    checks: list[Check] = []
    from digital_twin import DriveCycles
    # Pure-EV urban -> no engine, no cold-start penalty.
    urban = fleet_cold_start(ambient_c=-10, cycle=DriveCycles.urban())
    ok = all(r.penalty_l == 0.0 and r.engine_on_s == 0.0 for r in urban)
    checks.append(Check("Cold-start adds nothing on a pure-EV cycle",
                        ok, f"all {len(urban)} bodies 0 penalty"))
    # Where the engine runs, cold weather adds fuel and beats a warm start.
    warm = fleet_cold_start(ambient_c=20)
    cold = fleet_cold_start(ambient_c=-10)
    ok2 = all(c.penalty_l > w.penalty_l > 0.0
              for w, c in zip(warm, cold))
    suv = cold[0]
    checks.append(Check("Colder start raises the engine-on penalty",
                        ok2, f"SUV -10C +{suv.penalty_pct:.1f}% "
                        f"(engine {suv.engine_on_s:.0f}s)"))
    return checks


def _check_payload() -> list[Check]:
    checks: list[Check] = []
    sweeps = fleet_payload()
    # Fuel rises monotonically with payload for every body.
    ok = all(
        [p.fuel_l_per_100km for p in s.points]
        == sorted(p.fuel_l_per_100km for p in s.points)
        and s.full_penalty_pct > 0.0
        for s in sweeps)
    checks.append(Check("Payload raises fuel monotonically",
                        ok, f"SUV full load +{sweeps[0].full_penalty_pct:.1f}%"))
    # Capability still holds under a full cabin + cargo.
    ok2 = all(s.stays_capable for s in sweeps)
    checks.append(Check("Capability holds under full payload",
                        ok2, f"{len(sweeps)} bodies, 0 grade misses"))
    return checks


def _check_phev() -> list[Check]:
    checks: list[Check] = []
    res = fleet_phev()
    # Every body has a positive CD range and a bounded utility factor.
    ok = all(r.cd_range_km > 0.0 and 0.0 <= r.utility_factor <= 1.0
             for r in res)
    checks.append(Check("PHEV charge-depleting range is sane",
                        ok, f"SUV {res[0].cd_range_km:.0f} km, "
                        f"UF {res[0].utility_factor:.2f}"))
    # A cleaner grid lowers plug-in CO2 for every body.
    dirty = fleet_phev(grid=GridConfig(grid_co2_kg_per_kwh=0.40))
    clean = fleet_phev(grid=GridConfig(grid_co2_kg_per_kwh=0.05))
    ok2 = all(c.phev_co2_g_per_km < d.phev_co2_g_per_km
              for d, c in zip(dirty, clean))
    checks.append(Check("Cleaner grid lowers plug-in CO2",
                        ok2, "all bodies fall with grid intensity"))
    return checks


def _check_degradation() -> list[Check]:
    checks: list[Check] = []
    curves = fleet_degradation()
    # Ageing raises fuel and cuts EV range for every body.
    ok = all(c.fuel_drift_pct > 0.0 and c.range_loss_pct > 0.0
             for c in curves)
    suv = curves[0]
    checks.append(Check("Ageing raises fuel and cuts EV range",
                        ok, f"SUV +{suv.fuel_drift_pct:.1f}% fuel, "
                        f"-{suv.range_loss_pct:.1f}% range"))
    # The new-vehicle point reproduces the validated warm fuel exactly.
    body = PHASE1_BODIES[0]
    base = phase1_config_for(body)
    cs = replace(base, battery=replace(base.battery,
                                       initial_soc=base.battery.soc_target))
    validated = run(Powertrain(cs), DriveCycles.mixed()).fuel_l_per_100km
    ok2 = abs(suv.new.fuel_l_per_100km - validated) < 1e-6
    checks.append(Check("New-vehicle point matches validated fuel",
                        ok2, f"{suv.new.fuel_l_per_100km:.3f} L/100km"))
    return checks


def _check_gate4_scaling() -> list[Check]:
    checks: list[Check] = []
    ref = evaluate_layout(CylinderLayout(*REFERENCE_LAYOUT))
    checks.append(Check("Gate 4 reference layout (4/2/2) passes ERS",
                        ref.all_ers_pass,
                        f"{ref.ers_passed}/{ref.ers_total}, "
                        f"{ref.rated_kw:.0f} kW rated"))
    checks.append(Check("Gate 4 reference highway fuel matches CS headline",
                        abs(ref.highway_fuel_l_per_100km - 4.46) < 0.05,
                        f"{ref.highway_fuel_l_per_100km:.2f} L/100km (soc_target fuel)"))
    summary = run_gate4_sweep(max_total=10, max_per_tier=5)
    checks.append(Check("Gate 4 layout sweep finds ERS-passing configs",
                        len(summary.passing) > 0,
                        f"{len(summary.passing)}/{len(summary.results)} pass"))
    best = summary.best
    checks.append(Check("Gate 4 best-ranked layout passes ERS",
                        best.all_ers_pass,
                        f"{best.layout.label} ({best.layout.total_cylinders} cyl, "
                        f"hwy {best.highway_fuel_l_per_100km:.2f} L/100km)"))
    checks.append(Check("Gate 4 reference is competitive (score within band)",
                        ref.score >= best.score - 20.0,
                        f"ref {ref.score:.1f} vs best {best.score:.1f}"))
    x8_layouts = enumerate_ring_layouts(8)
    checks.append(Check("X8 ring has 45 tier mixes",
                        len(x8_layouts) == 45,
                        f"{len(x8_layouts)} layouts"))
    story = run_ring_size_study((4, 8), rating_profile="storyboard")
    x8_pass = sum(1 for r in story.for_ring(8) if r.all_ers_pass)
    checks.append(Check("X8 storyboard ring has passing mixed layouts",
                        x8_pass > 0,
                        f"{x8_pass}/{len(story.for_ring(8))} pass on X8"))
    x8_best = story.best_per_ring(design_aligned_only=True).get(8)
    checks.append(Check("X8 storyboard sweet spot is identified",
                        x8_best is not None and x8_best.layout.n_medium >= 1,
                        f"best {x8_best.layout.label if x8_best else 'none'} "
                        f"({x8_best.tier_mix_kind if x8_best else ''})"))
    checks.append(Check("X8 all-micro homogeneous is not design-aligned",
                        not is_design_aligned(
                            CylinderLayout(8, 0, 0), ring_size=8,
                            rating_profile="storyboard",
                        ),
                        "8/0/0 excluded from architecture picks"))
    sweep = run_gate4_sweep(max_total=10, max_per_tier=5)
    singles_pass = [
        r for r in sweep.passing if count_active_tiers(r.layout) == 1
    ]
    checks.append(Check("Gate 4 no single-tier layout passes full ERS",
                        len(singles_pass) == 0,
                        f"{len(singles_pass)} single-tier passers"))
    three = sweep.best_with_tier_depth(3, design_aligned_only=True)
    checks.append(Check("Gate 4 three-tier stack passes full ERS",
                        three is not None and three.all_ers_pass,
                        f"{three.layout.label if three else 'none'}"))
    x12_three = run_ring_size_study((12,), rating_profile="storyboard").best_per_ring(
        design_aligned_only=True, all_three_tiers_only=True,
    ).get(12)
    checks.append(Check("X12 storyboard has all-three-tier design-aligned pick",
                        x12_three is not None and x12_three.all_ers_pass,
                        f"{x12_three.layout.label if x12_three else 'none'}"))
    return checks


def run_checks(quick: bool = False) -> list[Check]:
    all_groups = (
        _check_rotor, _check_coupling, _check_fuel_economy,
        _check_acceptance, _check_durability, _check_robustness,
        _check_economics, _check_closed_loop, _check_sizing,
        _check_uncertainty, _check_ambient, _check_regulatory,
        _check_summary, _check_coldstart, _check_payload,
        _check_phev, _check_degradation, _check_ice_benchmark,
        _check_graceful_degradation, _check_gate1_bench, _check_gate1_matrix,
        _check_gate4_scaling,
    )
    quick_groups = (
        _check_rotor, _check_coupling, _check_fuel_economy,
        _check_acceptance, _check_summary, _check_ice_benchmark,
        _check_graceful_degradation, _check_gate1_bench, _check_gate1_matrix,
        _check_gate4_scaling,
    )
    groups = quick_groups if quick else all_groups
    checks: list[Check] = []
    for group in groups:
        checks.extend(group())
    return checks


def run_unit_tests(verbosity: int = 0) -> tuple[int, int]:
    """Run the test suite; return (tests_run, failures+errors)."""
    loader = unittest.TestLoader()
    suite = loader.discover(str(Path(__file__).parent / "tests"))
    runner = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stderr)
    result = runner.run(suite)
    return result.testsRun, len(result.failures) + len(result.errors)


def main(argv: list[str]) -> int:
    quiet = "--quiet" in argv or "-q" in argv
    quick = "--quick" in argv
    print("=" * 64)
    print("  PROJECT PHOENIX - one-command verification")
    if quick:
        print("  (--quick: smoke subset, unit tests skipped)")
    print("=" * 64)

    print("\n[1/2] Headline invariants + robustness claims\n")
    checks = run_checks(quick=quick)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        if not quiet or not c.ok:
            mark = "PASS" if c.ok else "FAIL"
            print(f"  [{mark}] {c.name:<46} {c.detail}")

    print("\n[2/2] Unit-test suite\n")
    if quick:
        tests_run, test_failures = 0, 0
        print("  Skipped in --quick mode (run full verify.py for all tests)")
    else:
        tests_run, test_failures = run_unit_tests(verbosity=0)
        print(f"  Ran {tests_run} tests, {test_failures} failed")

    total_fail = len(failed) + test_failures
    print("\n" + "=" * 64)
    if total_fail == 0:
        print(f"  RESULT: PASS  ({len(checks)} checks + {tests_run} tests)")
        print("=" * 64)
        return 0
    print(f"  RESULT: FAIL  ({len(failed)} checks + "
          f"{test_failures} tests failed)")
    print("=" * 64)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
