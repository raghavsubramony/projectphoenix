"""Project Phoenix - one-command verification.

Run with:

    .venv\\Scripts\\python.exe verify.py
    .venv\\Scripts\\python.exe verify.py --quiet     # summary only

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
                      4.62, 0.05, " L/100km")]
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
          and len(s.bodies) == 6)
    checks.append(Check("Executive summary echoes the validated headlines",
                        ok, f"{len(s.bodies)} bodies, rotor "
                        f"{s.rotor_peak_nm:.1f} N.m"))
    return checks


def run_checks() -> list[Check]:
    checks: list[Check] = []
    for group in (_check_rotor, _check_coupling, _check_fuel_economy,
                  _check_acceptance, _check_durability, _check_robustness,
                  _check_economics, _check_closed_loop, _check_sizing,
                  _check_uncertainty, _check_ambient, _check_regulatory,
                  _check_summary):
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
    print("=" * 64)
    print("  PROJECT PHOENIX - one-command verification")
    print("=" * 64)

    print("\n[1/2] Headline invariants + robustness claims\n")
    checks = run_checks()
    failed = [c for c in checks if not c.ok]
    for c in checks:
        if not quiet or not c.ok:
            mark = "PASS" if c.ok else "FAIL"
            print(f"  [{mark}] {c.name:<46} {c.detail}")

    print("\n[2/2] Unit-test suite\n")
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
