"""Project Phoenix - digital twin demo runner.

Runs the integrated ATPE + PCMRITMS powertrain twin over a set of representative
drive cycles and prints an engineering report for each.
"""

from dataclasses import replace

import argparse
from contextlib import contextmanager

from digital_twin import (
    DriveCycles,
    Powertrain,
    build_body_twins,
    build_default_twin,
    build_performance_twin,
    capability_report,
    capability_sweep,
    charge_sustaining_bodies,
    fleet_delta,
    fleet_table,
    fleet_tco,
    phase1_body_targets,
    phase1_config,
    phase1_config_for,
    phase1_targets,
    run,
    run_fleet,
    rotor_transient_power_w,
    sensitivity_report,
    sensitivity_table,
    sizing_table,
    fleet_battery_sizing,
    monte_carlo_fuel,
    fleet_uncertainty,
    uncertainty_table,
    ambient_sweep,
    fleet_ambient,
    ambient_table,
    regulatory_fidelity_table,
    fleet_regulatory,
    regulatory_economy_table,
    build_executive_summary,
    fleet_cold_start,
    cold_start_table,
    fleet_payload,
    payload_table,
    fleet_phev,
    phev_table,
    GridConfig,
    fleet_degradation,
    degradation_table,
    tco_table,
    stress_bodies,
    stress_unmet_launch_kj,
    closed_loop_rotor_bodies,
    ClosedLoopRotorController,
    DriveCycle,
    RotorSet,
    PHASE1_BODIES,
    simulate_torque_augmentation,
)
from digital_twin.acceptance import compare_bodies, recommend_motors, report_bodies, sweep_grid
from digital_twin.acceptance import report as ers_report
from digital_twin.battery import Battery
from digital_twin.config import BatteryConfig, BatteryThermalConfig, KWH_TO_J, KW
from digital_twin.pcmritms import InertialBuffer
from ml_study import describe_ranges, run_study, learned_bodies


@contextmanager
def _section(label: str):
    """Run a demo block in isolation: a failure prints a notice and is skipped
    rather than aborting the entire ~260-line report."""
    try:
        yield
    except Exception as exc:  # pragma: no cover - demo resilience, not logic
        print(f"  [section '{label}' skipped: {type(exc).__name__}: {exc}]\n")


def _charge_sustaining_phase1() -> Powertrain:
    """Phase-1 twin started at its charge-sustaining SoC target.

    This exposes representative *fuel* economy (the engine, not the depleting
    battery, supplies the trip energy) for comparison against the design docs.
    """
    cfg = phase1_config()
    cfg = replace(cfg, battery=replace(cfg.battery, initial_soc=cfg.battery.soc_target))
    return Powertrain(cfg)


def main(quick: bool = False) -> None:
    print("Project Phoenix - ATPE + PCMRITMS powertrain digital twin")
    if quick:
        print("(--quick: expensive Monte Carlo / uncertainty / ML blocks reduced)")
    print()

    with _section("executive summary"):
        print(build_executive_summary().report())
        print()

    print("# Charge-depleting (PHEV, full battery) - real owner behavior\n")
    for cycle in [
        DriveCycles.urban(),
        DriveCycles.highway(),
        DriveCycles.towing_grade(),
        DriveCycles.mixed(),
    ]:
        print(run(build_default_twin(), cycle).report())
        print()

    print("# Charge-sustaining (battery at target) - representative fuel economy\n")
    for cycle in [
        DriveCycles.highway(),
        DriveCycles.towing_grade(),
    ]:
        print(run(_charge_sustaining_phase1(), cycle).report())
        print()

    print("# Phase-2 performance variant\n")
    print(run(build_performance_twin(), DriveCycles.mixed()).report())
    print()

    print("# PCMRITMS rotor model - whitepaper Appendix A reproduction\n")
    tm = simulate_torque_augmentation()
    print(f"  Baseline torque : {tm.baseline_nm:.1f} N.m")
    print(f"  Peak torque     : {tm.peak_nm:.1f} N.m")
    print(f"  Brief boost     : +{tm.boost_percent:.1f} %")
    print(f"  Stored energy   : {tm.stored_energy_mj:.3f} MJ")
    print("  (whitepaper: 242.8 N.m, +34.9%, 0.118 MJ)")
    print()

    print("# ERS acceptance checks - Project PHOENIX P1 targets\n")
    print(ers_report(build_default_twin, phase1_targets()))
    print()

    print("# Vehicle-body comparison - same powertrain, different bodies\n")
    print(compare_bodies(build_body_twins(), phase1_targets()))
    print()

    print("# Per-body ERS acceptance - each body vs its class-appropriate targets\n")
    print(report_bodies(build_body_twins(), phase1_body_targets()))
    print()

    print("# Recommended motor sizing - kW needed to clear each body's targets\n")
    print(recommend_motors(build_body_twins(), phase1_body_targets()))
    print()

    print("# Motor-size sweep study - 60-250 kW in 10 kW steps\n")
    print(sweep_grid(build_body_twins(), phase1_body_targets()))
    print()

    print("# PCMRITMS rotor -> buffer coupling - integration into the step loop\n")
    surge_kw = rotor_transient_power_w(RotorSet()) / 1000.0
    base_buf = phase1_config().buffer
    coup_buf = phase1_config(rotor_coupled=True).buffer
    print(f"  Rotor surge power        : {surge_kw:.1f} kW "
          f"(62.8 N.m x 800 rad/s)")
    print(f"  Buffer continuous cap    : {base_buf.max_discharge_w / 1000:.0f} kW")
    print(f"  Buffer brief-burst cap   : {coup_buf.peak_transient_w / 1000:.1f} kW")
    # Transient probe: 130 kW demand spike against each buffer.
    base_b = InertialBuffer(base_buf)
    coup_b = InertialBuffer(coup_buf)
    base_peak = max(base_b.exchange(130_000, 0.1) for _ in range(10)) / 1000
    coup_peak = max(coup_b.exchange(130_000, 0.1) for _ in range(10)) / 1000
    print(f"  130 kW spike, baseline   : clipped to {base_peak:.1f} kW")
    print(f"  130 kW spike, coupled    : delivers  {coup_peak:.1f} kW")
    print("  (same energy, higher peak: peak-shaping, not extra energy)")
    print()

    print("# Fleet A/B - rotor coupling vs baseline across all bodies/cycles\n")
    before = run_fleet(charge_sustaining_bodies(rotor_coupled=False))
    after = run_fleet(charge_sustaining_bodies(rotor_coupled=True))
    print(fleet_delta(before, after, "fuel_l_per_100km"))
    print()
    print(fleet_delta(before, after, "buffer_peak_kw"))
    print()

    print("# Transient stress - realistic slew-limited generation (buffer matters)\n")
    stress = [DriveCycles.transient_stress(dt_s=0.2)]
    s_base = run_fleet(stress_bodies(rotor_coupled=False), stress)
    s_coup = run_fleet(stress_bodies(rotor_coupled=True), stress)
    print("  Buffer now delivers its full rotor-coupled burst on hard launches:")
    print(fleet_delta(s_base, s_coup, "buffer_peak_kw"))
    print()
    print("  ...drawn from the buffer instead of the battery (current sparing):")
    print(fleet_delta(s_base, s_coup, "battery_throughput_kj"))
    print()

    print("# PCMRITMS scaling - more rotors = burst + reservoir = capability\n")
    print("  Cold 40 kW battery, transient-stress launches, AWD SUV:")
    for label, coupled, scale in [
            ("baseline 3-rotor (90 kW, 118 kJ)", False, 1.0),
            ("coupled  3-rotor (140 kW, 118 kJ)", True, 1.0),
            ("coupled  ~5-rotor (140 kW, 236 kJ)", True, 2.0),
            ("coupled  ~6-rotor (140 kW, 354 kJ)", True, 3.0)]:
        unmet = stress_unmet_launch_kj(coupled=coupled, energy_scale=scale)
        print(f"    {label:34s}: {unmet:7.1f} kJ unmet launch energy")
    print()

    print("# Battery durability + thermal - the pack is throughput-, not heat-bound\n")
    print("  Charge-sustaining keeps the pack a buffer, so it barely cycles.")
    print("  Equivalent full cycles per 100 km (lower = longer life):")
    cs_fleet = run_fleet(charge_sustaining_bodies())
    print(fleet_table(cs_fleet, "battery_efc_per_100km"))
    print()
    print("  Projected pack life at that usage (thousands of km, 4000-EFC LFP):")
    print(fleet_table(cs_fleet, "projected_pack_life_km"))
    print()
    print("  In-cycle the lumped-thermal pack barely warms (engine+buffer carry")
    print("  the transients) - it never reaches its 45 C derate threshold:")
    th_fleet = run_fleet(stress_bodies(thermal=True),
                         [DriveCycles.transient_stress(dt_s=0.2)])
    print(fleet_table(th_fleet, "battery_peak_temp_c"))
    print()
    print("  The derate model is real, though: a small weakly-cooled pack driven")
    print("  at a constant 100 kW heats and clamps its own output -")
    _demo_battery_thermal_runaway()
    print()

    print("# Unified-AI learning study - online policy learned from twin data\n")
    print(describe_ranges())
    print()
    _study, study_report = run_study(epochs=1 if quick else 3)
    print(study_report.report())
    print()

    print("# Closed-loop A/B - learned AI policy DRIVES the twin vs rule-based\n")
    print("  Raw fuel deltas are confounded by SoC drift; the SoC-corrected")
    print("  (charge-sustaining) metric is the fair comparison.\n")
    rule = run_fleet(charge_sustaining_bodies())
    learned = run_fleet(learned_bodies(_study))
    print(fleet_delta(rule, learned, "fuel_l_per_100km"))
    print()
    print(fleet_delta(rule, learned, "equiv_fuel_l_per_100km"))
    print()
    print(fleet_delta(rule, learned, "shortfall_events"))
    print()

    print("# Sensitivity analysis - which physical inputs actually drive fuel?\n")
    print("  Elasticity = % change in fuel per % change in input, ranked.")
    print("  (Highway cruise, charge-sustaining AWD SUV.)\n")
    print(sensitivity_report(sensitivity_table(cycle=DriveCycles.highway())))
    print()
    print("  Capability: scaling the inertial reservoir vs transient shortfall")
    print("  (slew- and battery-limited launches - where the buffer matters):\n")
    print(capability_report(
        capability_sweep([1.0, 1.5, 2.0, 3.0], metric="shortfall_events"),
        "buffer reservoir (x118 kJ)", "shortfall_events"))
    print()
    print("  Reproduce every headline number in one command:  python verify.py")
    print()

    print("# TCO + lifecycle CO2 - small pack, never replaced, fast CO2 payback\n")
    tco = fleet_tco(run_fleet(charge_sustaining_bodies()))
    print(tco_table(tco))
    print()
    print(tco[0].report())
    print()

    with _section("closed-loop rotor surge control"):
        _demo_closed_loop_rotor()

    print("# Component right-sizing - how big does the battery actually need to be?\n")
    print("  Capability is governed by battery DISCHARGE POWER (buffer energy and")
    print("  the rotor surge barely move it). So: the smallest pack power per body")
    print("  that stays capable - i.e. how much the 120 kW default over-specs.\n")
    print(sizing_table(fleet_battery_sizing()))
    print()

    print("# Uncertainty bands - every headline number is a range, not a point\n")
    print("  Vary all uncertain inputs together (physical + economic) and report")
    print("  the spread, so each figure carries an honest confidence interval.\n")
    with _section("uncertainty bands"):
        print(monte_carlo_fuel(trials=40 if quick else 200, seed=0).report())
        print()
        if quick:
            print("  (--quick: fleet uncertainty table skipped)\n")
        else:
            print(uncertainty_table(fleet_uncertainty(trials=64, seed=0)))
            print()

    print("# Ambient temperature stress (-10 C to +40 C)\n")
    print("  Cold air is denser (more drag) and the cabin needs heating;")
    print("  a hot day needs air-conditioning - both raise the DC-bus load.\n")
    print(ambient_sweep().report())
    print()
    print(ambient_table(fleet_ambient()))
    print()

    print("# Standardized regulatory cycles (WLTP / EPA)\n")
    print("  Fuel economy on reconstructions of the named type-approval cycles,")
    print("  matched to each cycle's published distance / speed envelope.\n")
    print(regulatory_fidelity_table())
    print()
    print(regulatory_economy_table(fleet_regulatory()))
    print()

    print("# Cold-start engine penalty (Move J)\n")
    print("  Real engines burn richer while warming up. This rides on top of")
    print("  the validated warm run and decays with engine run time.\n")
    print(cold_start_table(fleet_cold_start(ambient_c=20)))
    print()
    print(cold_start_table(fleet_cold_start(ambient_c=-10)))
    print("  => urban is pure-EV (engine never starts) so the penalty is a")
    print("     long-trip phenomenon; cold weather amplifies it.\n")

    print("# Payload and passenger loading (Move K)\n")
    print("  Adding occupants and cargo on top of the validated kerb mass.\n")
    print(payload_table(fleet_payload()))
    print("  => full load adds 12-41% fuel (biggest % on the light bodies);")
    print("     capability holds on the grade test for every body.\n")

    print("# Grid-charging (PHEV) economics (Move L)\n")
    print("  Driving on grid electricity vs fuel, split by utility factor.\n")
    print(phev_table(fleet_phev()))
    print()
    print("  Clean grid (0.05 kg/kWh):")
    print(phev_table(fleet_phev(grid=GridConfig(grid_co2_kg_per_kwh=0.05)),
                     GridConfig(grid_co2_kg_per_kwh=0.05)))
    print("  => with an efficient hybrid, the CO2 win hinges on grid cleanliness,")
    print("     not merely on plugging in; the heavy fuel users gain the most.\n")

    print("# Drivetrain degradation over life (Move M)\n")
    print("  Ageing the pack and driveline from new to 250,000 km.\n")
    print(degradation_table(fleet_degradation()))
    print("  => EV range fades a consistent ~23% (capacity-led); fuel drifts up,")
    print("     large in % only on the light bodies (small denominator).\n")


def _demo_closed_loop_rotor() -> None:
    """A/B the closed-loop rotor surge controller against static coupling.

    Two views: (1) the representative stress fleet, where surge timing changes
    nothing because the buffer is energy-bound; (2) a controlled power-bound
    launch, where the controller provably holds the buffer to its continuous
    rating and shifts the overflow to the battery, conserving the inertial
    reservoir at equal-or-better capability.
    """
    print("# Closed-loop rotor surge control - is the 140 kW surge the limit?\n")

    static = {(c.body, c.cycle): c for c in
              run_fleet(stress_bodies(rotor_coupled=True, battery_derate_w=40_000.0))}
    closed = {(c.body, c.cycle): c for c in
              run_fleet(closed_loop_rotor_bodies(battery_derate_w=40_000.0))}
    worse = sum(1 for k, s in static.items()
                if closed[k].shortfall_events > s.shortfall_events)
    diff = sum(1 for k, s in static.items()
               if closed[k].shortfall_events != s.shortfall_events)
    print("  Representative stress fleet (24 body x cycle runs):")
    print(f"    closed-loop vs static coupling -> {diff} differ, {worse} worse")
    print("    => surge timing is a no-op here: the buffer is ENERGY-bound, so")
    print("       the 140 kW surge ceiling is never the binding constraint.\n")

    base = phase1_config_for(PHASE1_BODIES[0], rotor_coupled=True)
    buf = replace(base.buffer, max_energy_j=base.buffer.max_energy_j * 3.0)
    atpe = replace(base.atpe, max_slew_w_per_s=40_000.0)
    batt = replace(base.battery, max_discharge_w=80_000.0,
                   initial_soc=base.battery.soc_target)
    cfg = replace(base, buffer=buf, atpe=atpe, battery=batt)
    seg = [0.0, 7.0, 14.0, 21.0, 14.0, 0.0] + [0.0] * 24
    cyc = DriveCycle("Repeated launches", 1.0, seg * 4, [0.0] * len(seg * 4))
    surge = buf.peak_transient_w
    rs = run(Powertrain(cfg), cyc)
    ctrl = ClosedLoopRotorController(
        cfg.control, cfg.battery.soc_target, cfg.battery.soc_ev_floor,
        surge_ceiling_w=surge, continuous_rating_w=buf.max_discharge_w,
        battery_assist_w=batt.max_discharge_w)
    rc = run(Powertrain(cfg, controller=ctrl), cyc)
    print("  Controlled power-bound launch (reservoir has energy to spare):")
    print(f"    static : buffer peak {rs.buffer_peak_kw:5.1f} kW, "
          f"throughput {rs.buffer_throughput_kj:5.0f} kJ, "
          f"{rs.shortfall_events} shortfalls")
    print(f"    closed : buffer peak {rc.buffer_peak_kw:5.1f} kW, "
          f"throughput {rc.buffer_throughput_kj:5.0f} kJ, "
          f"{rc.shortfall_events} shortfalls")
    print("    => closed-loop holds the buffer to its continuous rating and lets")
    print("       the battery absorb the launch overflow, sparing the inertial")
    print("       reservoir at equal-or-better capability.\n")


def _demo_battery_thermal_runaway() -> None:
    """Drive a small, weakly-cooled pack at constant 100 kW to show the lumped
    thermal model heating and self-derating (proves the model is alive)."""
    th = BatteryThermalConfig(thermal_mass_j_per_k=8000.0, cooling_w_per_k=15.0,
                              internal_resistance_ohm=0.08)
    cfg = BatteryConfig(usable_capacity_j=30 * KWH_TO_J, max_discharge_w=120 * KW,
                        max_charge_w=80 * KW, soc_target=0.55, soc_ev_floor=0.25,
                        initial_soc=0.9, thermal=th)
    batt = Battery(cfg)
    delivered = 100 * KW
    for s in range(180):
        delivered = batt.exchange(100 * KW, 1.0)
        if s in (0, 60, 120, 179):
            print(f"    t={s:3d}s  T={batt.temperature_c:5.1f} C  "
                  f"delivered={delivered/1000:5.1f} kW  "
                  f"derate={batt.derate_factor():.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Project Phoenix digital-twin demo runner.")
    parser.add_argument(
        "--quick", action="store_true",
        help="reduce the expensive Monte Carlo / uncertainty / ML blocks "
             "for a fast smoke run.")
    args = parser.parse_args()
    main(quick=args.quick)
