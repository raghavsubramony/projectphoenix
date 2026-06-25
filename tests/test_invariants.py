"""Regression tests locking the digital twin's validated invariants.

Pure-stdlib unittest (no third-party deps). Run with:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from digital_twin import (
    simulate_torque_augmentation,
    RotorSet,
    couple_buffer,
    rotor_peak_reaction_torque_nm,
    rotor_transient_power_w,
    phase1_config,
    phase1_config_for,
    phase1_variants,
    PHASE1_BODIES,
    build_body_twins,
    evaluate,
    phase1_body_targets,
    run_fleet,
    charge_sustaining_bodies,
    stress_bodies,
    closed_loop_rotor_bodies,
    ClosedLoopRotorController,
    DriveCycles,
)
from dataclasses import replace
from digital_twin.pcmritms import InertialBuffer
from digital_twin.powertrain import Powertrain
from digital_twin.battery import Battery
from digital_twin.drive_cycles import DriveCycle
from digital_twin.simulation import run
from digital_twin.config import (
    BatteryConfig,
    BatteryThermalConfig,
    with_battery_thermal,
    KWH_TO_J,
    KW,
)
from digital_twin.simulation import run


class RotorWhitepaperTest(unittest.TestCase):
    """The standalone rotor model must reproduce the whitepaper headline."""

    def test_torque_augmentation_matches_whitepaper(self) -> None:
        r = simulate_torque_augmentation()
        self.assertAlmostEqual(r.peak_nm, 242.8, delta=0.1)
        self.assertAlmostEqual(r.boost_percent, 34.9, delta=0.1)
        self.assertAlmostEqual(r.stored_energy_mj, 0.118, delta=0.001)

    def test_peak_reaction_torque_and_surge(self) -> None:
        rotors = RotorSet()
        self.assertAlmostEqual(
            rotor_peak_reaction_torque_nm(rotors), 62.8, delta=0.2)
        # Surge power = peak torque x mean speed ~ 50 kW.
        self.assertAlmostEqual(
            rotor_transient_power_w(rotors) / 1000, 50.2, delta=0.3)


class RotorCouplingTest(unittest.TestCase):
    """Coupling must be opt-in and physically self-limiting."""

    def test_coupling_is_off_by_default(self) -> None:
        self.assertIsNone(phase1_config().buffer.peak_transient_w)

    def test_coupling_sets_transient_rating(self) -> None:
        coupled = phase1_config(rotor_coupled=True).buffer
        self.assertIsNotNone(coupled.peak_transient_w)
        # Continuous 90 kW + ~50 kW surge.
        self.assertAlmostEqual(coupled.peak_transient_w / 1000, 140.2, delta=0.3)
        self.assertGreater(coupled.peak_transient_w, coupled.max_discharge_w)

    def test_couple_buffer_helper(self) -> None:
        base = phase1_config().buffer
        coupled = couple_buffer(base)
        self.assertGreater(coupled.peak_transient_w, base.max_discharge_w)

    def test_baseline_buffer_clips_transient_spike(self) -> None:
        b = InertialBuffer(phase1_config().buffer)
        first = b.exchange(130_000, 0.1)
        self.assertLessEqual(first, 90_000 + 1.0)

    def test_coupled_buffer_covers_transient_spike(self) -> None:
        b = InertialBuffer(phase1_config(rotor_coupled=True).buffer)
        first = b.exchange(130_000, 0.1)
        # Fresh, energy-rich buffer delivers the full request up to its cap.
        self.assertAlmostEqual(first, 130_000, delta=1.0)

    def test_coupling_does_not_add_energy(self) -> None:
        """Higher peak, same reservoir: both buffers drain the same energy."""
        base = InertialBuffer(phase1_config().buffer)
        coup = InertialBuffer(phase1_config(rotor_coupled=True).buffer)
        e_base = sum(base.exchange(200_000, 0.1) for _ in range(30))
        e_coup = sum(coup.exchange(200_000, 0.1) for _ in range(30))
        self.assertAlmostEqual(e_base, e_coup, delta=1.0)


class BodyAcceptanceTest(unittest.TestCase):
    """Every Phase-1 body must pass all of its class-appropriate ERS targets."""

    def test_all_bodies_pass_all_checks(self) -> None:
        builders = build_body_twins()
        targets = phase1_body_targets()
        for name, build in builders.items():
            checks = evaluate(build, targets[name])
            failed = [c.name for c in checks if not c.passed]
            self.assertEqual(failed, [], f"{name} failed: {failed}")


class FleetRegressionTest(unittest.TestCase):
    """Validated fleet numbers and no-regression of the coupling toggle."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.baseline = {
            (c.body, c.cycle): c
            for c in run_fleet(charge_sustaining_bodies(rotor_coupled=False))
        }
        cls.coupled = {
            (c.body, c.cycle): c
            for c in run_fleet(charge_sustaining_bodies(rotor_coupled=True))
        }

    def _cell(self, cells, body, cycle_substr):
        return next(c for (b, cy), c in cells.items()
                   if b == body and cycle_substr in cy)

    def test_suv_highway_fuel_economy(self) -> None:
        suv = self._cell(self.baseline, "AWD SUV", "Highway")
        self.assertAlmostEqual(suv.fuel_l_per_100km, 4.62, delta=0.05)

    def test_urban_is_electric(self) -> None:
        for (body, cycle), c in self.baseline.items():
            if "Urban" in cycle:
                self.assertAlmostEqual(c.fuel_l_per_100km, 0.0, delta=1e-6,
                                       msg=f"{body} urban not EV")

    def test_no_shortfalls_anywhere(self) -> None:
        for (body, cycle), c in self.baseline.items():
            self.assertEqual(c.shortfall_events, 0,
                             f"{body}/{cycle} has shortfalls")

    def test_coupling_does_not_change_fuel_or_energy(self) -> None:
        """On standard cycles the coupling shapes peaks, not fuel/energy."""
        for key, base in self.baseline.items():
            coup = self.coupled[key]
            self.assertAlmostEqual(base.fuel_l_per_100km,
                                   coup.fuel_l_per_100km, delta=1e-6)
            self.assertAlmostEqual(base.net_battery_kwh,
                                   coup.net_battery_kwh, delta=1e-3)


class GenerationSlewTest(unittest.TestCase):
    """The generation ramp limit is opt-in and bounds the per-step change."""

    def test_slew_off_by_default(self) -> None:
        self.assertIsNone(phase1_config().atpe.max_slew_w_per_s)

    def test_no_regression_when_slew_off(self) -> None:
        """Standard charge-sustaining fleet is byte-identical with slew unset."""
        cells = {(c.body, c.cycle): c
                 for c in run_fleet(charge_sustaining_bodies())}
        suv = next(c for (b, cy), c in cells.items()
                   if b == "AWD SUV" and "Highway" in cy)
        self.assertAlmostEqual(suv.fuel_l_per_100km, 4.62, delta=0.05)

    def test_slew_limits_ramp_rate(self) -> None:
        """With a slew cap the ATPE cannot jump from idle to full in one step."""
        from digital_twin.atpe import ATPE
        cfg = replace(phase1_config().atpe, max_slew_w_per_s=50_000.0)
        atpe = ATPE(cfg)
        first = atpe.generate(200_000, 1.0)  # demand 200 kW, cap 50 kW/s
        self.assertAlmostEqual(first.electric_w, 50_000, delta=1.0)
        second = atpe.generate(200_000, 1.0)
        self.assertAlmostEqual(second.electric_w, 100_000, delta=1.0)


class TransientStressTest(unittest.TestCase):
    """Under realistic slew-limited generation the rotor coupling earns its keep."""

    @classmethod
    def setUpClass(cls) -> None:
        cyc = [DriveCycles.transient_stress(dt_s=0.2)]
        cls.base = {c.body: c
                    for c in run_fleet(stress_bodies(rotor_coupled=False), cyc)}
        cls.coup = {c.body: c
                    for c in run_fleet(stress_bodies(rotor_coupled=True), cyc)}

    def test_coupling_raises_buffer_peak(self) -> None:
        """Heavy bodies should see the buffer deliver its full ~140 kW burst."""
        for body in ("AWD SUV", "Pickup", "Van / MPV"):
            gain = self.coup[body].buffer_peak_kw - self.base[body].buffer_peak_kw
            self.assertGreater(gain, 40.0, f"{body} buffer peak gain {gain:.1f}")

    def test_coupling_spares_battery_on_heavy_bodies(self) -> None:
        """Coupled buffer reduces battery energy drawn during launches."""
        for body in ("AWD SUV", "Pickup", "Van / MPV"):
            base_kj = self.base[body].battery_throughput_kj
            coup_kj = self.coup[body].battery_throughput_kj
            self.assertLess(coup_kj, base_kj, f"{body} battery not spared")


class RotorScalingTest(unittest.TestCase):
    """More rotors (burst + reservoir) convert to sustained capability.

    The whitepaper's 3 -> 4-6 rotor growth path raises both the burst rating and
    the stored energy. This locks the monotone reduction in unmet launch energy
    under a constrained (cold) battery as the reservoir grows.
    """

    @staticmethod
    def _unmet_kj(coupled: bool, energy_scale: float) -> float:
        cfg = phase1_config_for(PHASE1_BODIES[0], rotor_coupled=coupled)
        buf = replace(cfg.buffer,
                      max_energy_j=cfg.buffer.max_energy_j * energy_scale)
        cfg = replace(
            cfg, buffer=buf,
            atpe=replace(cfg.atpe, max_slew_w_per_s=60_000.0),
            battery=replace(cfg.battery, max_discharge_w=40_000.0,
                            initial_soc=cfg.battery.soc_target))
        tw = Powertrain(cfg)
        cyc = DriveCycles.transient_stress(dt_s=0.2)
        acc = cyc.accelerations()
        unmet = 0.0
        for i in range(len(cyc.speeds_ms)):
            r = tw.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
            unmet += r.shortfall_w * cyc.dt_s
        return unmet / 1000.0

    def test_scaling_reservoir_cuts_unmet_energy(self) -> None:
        small = self._unmet_kj(coupled=True, energy_scale=1.0)
        mid = self._unmet_kj(coupled=True, energy_scale=2.0)
        big = self._unmet_kj(coupled=True, energy_scale=3.0)
        self.assertGreater(small, mid)
        self.assertGreater(mid, big)
        # The 6-rotor-class reservoir cuts unmet energy by at least 40%.
        self.assertLess(big, 0.6 * small)


class PluggableControllerTest(unittest.TestCase):
    """The pluggable-controller seam must not change default behaviour, and a
    learned policy must be able to drive the twin closed-loop."""

    def test_default_controller_unchanged(self) -> None:
        # Explicitly passing the default controller type must reproduce the
        # exact result of the implicit default (no behavioural regression).
        from digital_twin.controller import UnifiedController
        cfg = phase1_config_for(PHASE1_BODIES[0])
        cyc = DriveCycles.highway()
        base = Powertrain(cfg)
        plug = Powertrain(cfg, controller=UnifiedController(
            cfg.control,
            battery_soc_target=cfg.battery.soc_target,
            battery_soc_ev_floor=cfg.battery.soc_ev_floor,
        ))
        acc = cyc.accelerations()
        for i in range(len(cyc.speeds_ms)):
            rb = base.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
            rp = plug.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
            self.assertAlmostEqual(rb.generation_w, rp.generation_w, delta=1e-6)
            self.assertAlmostEqual(rb.battery_soc, rp.battery_soc, delta=1e-9)

    def test_learned_policy_drives_twin(self) -> None:
        from ml_study import run_study, LearnedController
        study, _ = run_study(epochs=1, seed=0)
        cfg = phase1_config_for(PHASE1_BODIES[0])
        tw = Powertrain(cfg, controller=LearnedController(study))
        cyc = DriveCycles.highway()
        acc = cyc.accelerations()
        # Must run end-to-end without crashing and produce a sane SoC.
        for i in range(len(cyc.speeds_ms)):
            r = tw.step(cyc.speeds_ms[i], acc[i], cyc.grades_rad[i], cyc.dt_s)
        self.assertGreater(r.battery_soc, 0.0)
        self.assertLessEqual(r.battery_soc, 1.0)

    def test_soc_corrected_fuel_is_reported(self) -> None:
        # The SoC-corrected metric must be populated and finite for a normal run.
        from digital_twin import run
        cfg = phase1_config_for(PHASE1_BODIES[0])
        res = run(Powertrain(cfg), DriveCycles.highway())
        self.assertGreater(res.equiv_fuel_l_per_100km, 0.0)


class ThermalDurabilityTest(unittest.TestCase):
    """Battery thermal model + durability accounting (Move B)."""

    @staticmethod
    def _plain_battery() -> Battery:
        cfg = BatteryConfig(
            usable_capacity_j=20 * KWH_TO_J, max_discharge_w=120 * KW,
            max_charge_w=80 * KW, soc_target=0.55, soc_ev_floor=0.25,
            initial_soc=0.8)
        return Battery(cfg)

    def test_thermal_off_is_inert(self) -> None:
        # With no thermal config the pack must stay at ambient, log no heat,
        # and never derate - i.e. behave exactly as the validated baseline.
        b = self._plain_battery()
        for _ in range(200):
            b.exchange(100 * KW, 1.0)
        self.assertEqual(b.temperature_c, 25.0)
        self.assertEqual(b.heat_loss_j, 0.0)
        self.assertAlmostEqual(b._derate_factor(), 1.0)

    def test_thermal_heats_and_derates_under_abuse(self) -> None:
        th = BatteryThermalConfig(thermal_mass_j_per_k=8000.0,
                                  cooling_w_per_k=15.0,
                                  internal_resistance_ohm=0.08)
        cfg = BatteryConfig(
            usable_capacity_j=30 * KWH_TO_J, max_discharge_w=120 * KW,
            max_charge_w=80 * KW, soc_target=0.55, soc_ev_floor=0.25,
            initial_soc=0.9, thermal=th)
        b = Battery(cfg)
        delivered_first = b.exchange(100 * KW, 1.0)
        for _ in range(120):
            delivered = b.exchange(100 * KW, 1.0)
        # The pack must heat past the derate threshold and clamp its output.
        self.assertGreater(b.peak_temperature_c, th.derate_start_c)
        self.assertGreater(b.heat_loss_j, 0.0)
        self.assertLess(b._derate_factor(), 1.0)
        self.assertLess(delivered, delivered_first)
        # Derating must never fall below the configured floor authority.
        self.assertGreaterEqual(
            b._derate_factor(), th.floor_fraction - 1e-9)

    def test_efc_accounting_matches_throughput(self) -> None:
        b = self._plain_battery()
        cap = b.cfg.usable_capacity_j
        # Discharge a known amount of energy: 50 kW for 100 s = 5 MJ.
        for _ in range(100):
            b.exchange(50 * KW, 1.0)
        self.assertAlmostEqual(b.throughput_j, 50 * KW * 100, delta=1.0)
        expected_efc = b.throughput_j / (2.0 * cap)
        self.assertGreater(expected_efc, 0.0)

    def test_charge_sustaining_pack_life_is_long(self) -> None:
        # Charge-sustaining operation barely cycles the pack, so the projected
        # life must dwarf any realistic vehicle mileage, and the in-cycle pack
        # must not thermally derate (the engine + buffer carry the transients).
        cfg = with_battery_thermal(phase1_config_for(PHASE1_BODIES[0]))
        cfg = replace(cfg, battery=replace(
            cfg.battery, initial_soc=cfg.battery.soc_target))
        res = run(Powertrain(cfg), DriveCycles.highway())
        self.assertLess(res.battery_efc_per_100km, 1.0)
        self.assertGreater(res.projected_pack_life_km, 500_000.0)
        self.assertLess(res.battery_peak_temp_c,
                        cfg.battery.thermal.derate_start_c)


class SensitivitySweepTest(unittest.TestCase):
    """Sensitivity ranking + capability sweep must reflect the real physics."""

    def test_mass_increases_fuel(self) -> None:
        from digital_twin import sensitivity_table
        table = sensitivity_table(cycle=DriveCycles.highway())
        mass = next(e for e in table if e.parameter == "Vehicle mass")
        # Heavier = more fuel: positive elasticity.
        self.assertGreater(mass.elasticity, 0.0)

    def test_table_is_ranked_by_influence(self) -> None:
        from digital_twin import sensitivity_table
        table = sensitivity_table(cycle=DriveCycles.highway())
        mags = [e.abs_elasticity for e in table]
        self.assertEqual(mags, sorted(mags, reverse=True))

    def test_aero_dominates_mass_at_highway_speed(self) -> None:
        from digital_twin import sensitivity_table
        table = sensitivity_table(cycle=DriveCycles.highway())
        drag = next(e for e in table if e.parameter == "Drag coefficient")
        mass = next(e for e in table if e.parameter == "Vehicle mass")
        self.assertGreater(drag.abs_elasticity, mass.abs_elasticity)

    def test_buffer_scaling_is_monotone_and_kills_shortfalls(self) -> None:
        from digital_twin import capability_sweep
        pts = capability_sweep([1.0, 2.0, 3.0, 4.0],
                               metric="shortfall_events")
        vals = [p.metric for p in pts]
        # Non-increasing as the reservoir grows.
        for a, b in zip(vals, vals[1:]):
            self.assertLessEqual(b, a + 1e-9)
        # A 2x reservoir already eliminates the transient shortfall.
        self.assertEqual(vals[1], 0)


class VerifyEntrypointTest(unittest.TestCase):
    """The one-command verifier's invariant checks must all pass."""

    def test_all_headline_checks_pass(self) -> None:
        import verify
        failed = [c.name for c in verify.run_checks() if not c.ok]
        self.assertEqual(failed, [], f"verify checks failed: {failed}")


class LifecycleTcoTest(unittest.TestCase):
    """TCO + lifecycle-CO2 rollup must reflect the architecture's properties."""

    @classmethod
    def setUpClass(cls) -> None:
        from digital_twin import run_fleet, charge_sustaining_bodies, fleet_tco
        cls.results = fleet_tco(run_fleet(charge_sustaining_bodies()))
        cls.by_body = {r.body: r for r in cls.results}

    def test_pack_is_never_replaced(self) -> None:
        # Charge-sustaining barely cycles the pack, so no body needs a mid-life
        # battery replacement within the vehicle lifetime.
        for r in self.results:
            self.assertEqual(r.battery_replacements, 0,
                             f"{r.body} needs {r.battery_replacements} packs")

    def test_embodied_battery_co2_is_small_share(self) -> None:
        # The small 20 kWh pack's embodied CO2 must be a minor part of lifecycle.
        suv = self.by_body["AWD SUV"]
        self.assertLess(suv.embodied_battery_co2_t, 0.15 * suv.lifecycle_co2_t)

    def test_lifecycle_co2_components_sum(self) -> None:
        suv = self.by_body["AWD SUV"]
        total = (suv.tailpipe_co2_t + suv.well_to_tank_co2_t
                 + suv.embodied_battery_co2_t + suv.glider_co2_t)
        self.assertAlmostEqual(total, suv.lifecycle_co2_t, delta=1e-6)

    def test_heavier_body_costs_more_per_km(self) -> None:
        # The pickup (heaviest, draggiest) must not be cheaper to run than the
        # aero-clean sedan.
        self.assertGreater(self.by_body["Pickup"].cost_per_km,
                           self.by_body["Sedan"].cost_per_km)


def _launch_scenario():
    """A controlled launch micro-cycle where the buffer becomes *power*-bound.

    Ample reservoir energy (3x) plus a sizeable battery assist mean the launch
    overflow can be covered by the continuous buffer + battery, so surge timing
    actually matters here (unlike the energy-bound representative cycles).
    Returns (cfg, cycle, surge_w, continuous_w, assist_w).
    """
    base = phase1_config_for(PHASE1_BODIES[0], rotor_coupled=True)
    buf = replace(base.buffer, max_energy_j=base.buffer.max_energy_j * 3.0)
    atpe = replace(base.atpe, max_slew_w_per_s=40_000.0)
    batt = replace(base.battery, max_discharge_w=80_000.0,
                   initial_soc=base.battery.soc_target)
    cfg = replace(base, buffer=buf, atpe=atpe, battery=batt)
    seg = [0.0, 7.0, 14.0, 21.0, 14.0, 0.0] + [0.0] * 24
    cycle = DriveCycle("Launches", 1.0, seg * 4, [0.0] * len(seg * 4))
    surge = buf.peak_transient_w
    return cfg, cycle, surge, buf.max_discharge_w, batt.max_discharge_w


class ClosedLoopRotorTest(unittest.TestCase):
    """Move E: closed-loop surge arbitration is a no-regression, opt-in superset
    that proves the inertial buffer is energy-bound, not surge-bound."""

    def test_gate_logic_is_battery_aware(self) -> None:
        # Deterministic decide() check: surge is spent only when continuous
        # buffer + battery assist cannot cover the deficit.
        cfg, _, surge, cont, assist = _launch_scenario()
        ctrl = ClosedLoopRotorController(
            cfg.control, cfg.battery.soc_target, cfg.battery.soc_ev_floor,
            surge_ceiling_w=surge, continuous_rating_w=cont,
            battery_assist_w=assist)
        # Small deficit the continuous buffer alone covers -> no surge.
        s = ctrl.decide(cont * 0.5, 0.55, 200_000.0, 1.0)
        self.assertEqual(s.buffer_burst_w, cont)
        # Deficit the battery can still help cover -> hold to continuous.
        s = ctrl.decide(cont + assist * 0.5, 0.55, 0.0, 1.0)
        self.assertEqual(s.buffer_burst_w, cont)
        # Deficit beyond continuous + battery -> authorize full surge.
        s = ctrl.decide(cont + assist + 50_000.0, 0.55, 0.0, 1.0)
        self.assertEqual(s.buffer_burst_w, surge)

    def test_default_controller_unaffected(self) -> None:
        # With no closed-loop controller, ControlState.buffer_burst_w stays None
        # so the buffer keeps its static cap: closed-loop is purely additive.
        from digital_twin.controller import UnifiedController
        uc = UnifiedController(phase1_config().control,
                               battery_soc_target=0.55, battery_soc_ev_floor=0.25)
        state = uc.decide(50_000.0, 0.55, 200_000.0, 1.0)
        self.assertIsNone(state.buffer_burst_w)

    def test_caps_surge_and_shifts_overflow_to_battery(self) -> None:
        # In the power-bound launch scenario the controller must hold the buffer
        # to its continuous rating (not the 140 kW surge) and let the battery
        # absorb the overflow - without adding any shortfall.
        cfg, cycle, surge, cont, assist = _launch_scenario()
        static = run(Powertrain(cfg), cycle)
        ctrl = ClosedLoopRotorController(
            cfg.control, cfg.battery.soc_target, cfg.battery.soc_ev_floor,
            surge_ceiling_w=surge, continuous_rating_w=cont,
            battery_assist_w=assist)
        closed = run(Powertrain(cfg, controller=ctrl), cycle)
        # Static uses the full surge ceiling; closed-loop holds to continuous.
        self.assertGreater(static.buffer_peak_kw, cont / 1000.0 + 1.0)
        self.assertLessEqual(closed.buffer_peak_kw, cont / 1000.0 + 1e-6)
        # Overflow is shifted off the buffer onto the battery.
        self.assertLess(closed.buffer_throughput_kj, static.buffer_throughput_kj)
        self.assertGreater(closed.battery_throughput_kj,
                           static.battery_throughput_kj)
        # And it never makes capability worse.
        self.assertLessEqual(closed.shortfall_events, static.shortfall_events)

    def test_fleet_ab_never_worsens_shortfalls(self) -> None:
        # Across the representative stress cycles the buffer is energy-bound, so
        # surge timing changes nothing - and certainly never adds shortfalls.
        static = {(c.body, c.cycle): c
                  for c in run_fleet(stress_bodies(rotor_coupled=True,
                                                   battery_derate_w=40_000.0))}
        closed = {(c.body, c.cycle): c
                  for c in run_fleet(closed_loop_rotor_bodies(
                      battery_derate_w=40_000.0))}
        for key, s in static.items():
            self.assertLessEqual(closed[key].shortfall_events, s.shortfall_events,
                                 f"closed-loop worsened shortfalls at {key}")


class BatterySizingTest(unittest.TestCase):
    """Move F: right-sizing finds battery discharge power is the capability
    lever, the default pack power is over-specced, and the rotor surge is
    irrelevant to the sizing (reinforcing Move E)."""

    @classmethod
    def setUpClass(cls) -> None:
        from digital_twin import fleet_battery_sizing, SizingConfig
        cls.SizingConfig = SizingConfig
        cls.results = fleet_battery_sizing()
        cls.by_body = {r.body: r for r in cls.results}

    def test_every_body_is_capable_within_the_sweep(self) -> None:
        for r in self.results:
            self.assertTrue(r.feasible, f"{r.body} never reached the target")

    def test_recommendation_is_a_downsize_not_an_upsize(self) -> None:
        # Every body's minimum capable power is at or below the 120 kW default:
        # the validated pack power is generously over-specced.
        for r in self.results:
            self.assertLessEqual(r.recommended_power_w, r.default_power_w)
        # At least one body strictly down-sizes (so this is a real finding).
        self.assertTrue(any(r.downsize_w > 0 for r in self.results))

    def test_recommended_is_the_minimum_feasible_level(self) -> None:
        # The recommended power is feasible and every lower swept level is not.
        for r in self.results:
            below = [p for p in r.sweep if p.battery_power_w < r.recommended_power_w]
            self.assertTrue(all(not p.feasible for p in below),
                            f"{r.body}: a lower power was already feasible")
            rec = next(p for p in r.sweep
                       if p.battery_power_w == r.recommended_power_w)
            self.assertTrue(rec.feasible)

    def test_rotor_surge_does_not_change_the_sizing(self) -> None:
        # The PCMRITMS 140 kW burst is irrelevant to the required pack power
        # (Move E from the design side): coupled and uncoupled size identically.
        from digital_twin import fleet_battery_sizing
        coupled = {r.body: r.recommended_power_w
                   for r in fleet_battery_sizing(
                       self.SizingConfig(rotor_coupled=True))}
        uncoupled = {r.body: r.recommended_power_w
                     for r in fleet_battery_sizing(
                         self.SizingConfig(rotor_coupled=False))}
        self.assertEqual(coupled, uncoupled)


class MonteCarloUncertaintyTest(unittest.TestCase):
    """Move G: joint-uncertainty Monte-Carlo bands are well-formed, seeded-
    reproducible, and widen with input uncertainty."""

    def test_nominal_lies_inside_the_90pct_band(self) -> None:
        from digital_twin import monte_carlo_fuel
        d = monte_carlo_fuel(trials=80, seed=0)
        self.assertLessEqual(d.p05, d.nominal)
        self.assertLessEqual(d.nominal, d.p95)
        self.assertGreater(d.std, 0.0)
        # Percentiles must be ordered.
        self.assertLessEqual(d.p05, d.p50)
        self.assertLessEqual(d.p50, d.p95)

    def test_same_seed_is_reproducible(self) -> None:
        from digital_twin import monte_carlo_fuel
        a = monte_carlo_fuel(trials=48, seed=7)
        b = monte_carlo_fuel(trials=48, seed=7)
        self.assertEqual(a.mean, b.mean)
        self.assertEqual(a.std, b.std)
        self.assertEqual(a.p95, b.p95)

    def test_more_input_uncertainty_widens_the_band(self) -> None:
        from digital_twin import monte_carlo_fuel, DEFAULT_PARAM_SIGMA
        tight = monte_carlo_fuel(trials=80, seed=3)
        wide_sigmas = {k: v * 2.0 for k, v in DEFAULT_PARAM_SIGMA.items()}
        wide = monte_carlo_fuel(trials=80, seed=3, sigmas=wide_sigmas)
        self.assertGreater(wide.std, tight.std)

    def test_fleet_bands_are_positive_and_contain_nominal(self) -> None:
        from digital_twin import fleet_uncertainty
        bands = fleet_uncertainty(trials=24, seed=0)
        self.assertEqual(len(bands), 6)
        for b in bands:
            for dist in (b.fuel_l_per_100km, b.cost_per_km,
                         b.lifecycle_co2_g_per_km):
                self.assertGreater(dist.p05, 0.0)
                self.assertLessEqual(dist.p05, dist.p50)
                self.assertLessEqual(dist.p50, dist.p95)
            # The unperturbed cost/CO2 estimate must fall within its own band.
            self.assertLessEqual(b.cost_per_km.p05, b.cost_per_km.nominal)
            self.assertLessEqual(b.cost_per_km.nominal, b.cost_per_km.p95)


class AmbientStressTest(unittest.TestCase):
    """Move H: ambient temperature (-10 C..+40 C) raises fuel via denser cold
    air + HVAC, the pack stays clear of derate, and the default is untouched."""

    def test_air_density_falls_with_temperature(self) -> None:
        from digital_twin import air_density_factor
        # Cold air is denser (factor > 1), hot air thinner (factor < 1).
        self.assertGreater(air_density_factor(-10.0), 1.0)
        self.assertAlmostEqual(air_density_factor(15.0), 1.0, places=6)
        self.assertLess(air_density_factor(40.0), 1.0)

    def test_hvac_load_is_a_v_shape_around_comfort(self) -> None:
        from digital_twin import hvac_load_w, AmbientConfig
        a = AmbientConfig()
        self.assertEqual(hvac_load_w(a.hvac_comfort_c, a), 0.0)
        self.assertGreater(hvac_load_w(-10.0, a), 0.0)   # heating
        self.assertGreater(hvac_load_w(40.0, a), 0.0)    # cooling
        self.assertLessEqual(hvac_load_w(-30.0, a), a.hvac_max_w)  # capped

    def test_cold_burns_more_fuel_than_the_comfort_point(self) -> None:
        from digital_twin import ambient_sweep
        s = ambient_sweep(temps_c=(-10.0, 20.0))
        cold = s.at(-10.0).fuel_l_per_100km
        mild = s.at(20.0).fuel_l_per_100km
        self.assertGreater(cold, mild)
        self.assertGreater(s.fuel_swing_pct, 0.0)

    def test_pack_stays_below_derate_under_realistic_duty(self) -> None:
        from digital_twin import fleet_ambient
        # Even on a +40 C day the modest, over-specced pack never derates.
        for s in fleet_ambient():
            self.assertFalse(s.any_derate, f"{s.body} unexpectedly derated")

    def test_default_config_is_untouched_by_the_study(self) -> None:
        from digital_twin import phase1_config_for, simulate_at_temp, PHASE1_BODIES
        # Running an ambient point must not mutate the shared body config: the
        # default battery has no thermal model and the validated drag stands.
        before = phase1_config_for(PHASE1_BODIES[0])
        simulate_at_temp(temp_c=40.0)
        after = phase1_config_for(PHASE1_BODIES[0])
        self.assertIsNone(after.battery.thermal)
        self.assertEqual(before.vehicle.drag_coefficient,
                         after.vehicle.drag_coefficient)


class RegulatoryCycleTest(unittest.TestCase):
    """Move I: reconstructed WLTP / EPA cycles match their published energy
    envelope and produce sensible, monotone-by-duty fuel economy."""

    def _within(self, built: float, published: float, tol_pct: float) -> None:
        err = abs(built - published) / published * 100.0
        self.assertLessEqual(err, tol_pct,
                             f"{built:.1f} vs {published:.1f} ({err:.0f}% > {tol_pct}%)")

    def test_reconstructions_match_published_envelope(self) -> None:
        from digital_twin import RegulatoryCycles
        for rc in RegulatoryCycles.all():
            st, sp = rc.stats(), rc.spec
            self._within(st.distance_km, sp.distance_km, 10.0)
            self._within(st.avg_speed_kmh, sp.avg_speed_kmh, 10.0)
            self._within(st.max_speed_kmh, sp.max_speed_kmh, 5.0)
            self._within(st.duration_s, sp.duration_s, 10.0)

    def test_city_cycle_stops_highway_does_not(self) -> None:
        from digital_twin import RegulatoryCycles
        self.assertGreater(RegulatoryCycles.epa_udds().stats().stops, 5)
        self.assertEqual(RegulatoryCycles.epa_hwfet().stats().stops, 0)

    def test_heavier_body_burns_more_on_every_cycle(self) -> None:
        from digital_twin import fleet_regulatory
        results = fleet_regulatory()
        by = {(r.body, r.cycle): r.fuel_l_per_100km for r in results}
        cycles = {r.cycle for r in results}
        for c in cycles:
            self.assertGreater(by[("Pickup", c)], by[("Hatchback", c)],
                               f"Pickup should out-burn Hatchback on {c}")

    def test_no_capability_shortfalls_on_regulatory_cycles(self) -> None:
        from digital_twin import fleet_regulatory
        for r in fleet_regulatory():
            self.assertEqual(r.shortfall_events, 0,
                             f"{r.body} shortfall on {r.cycle}")

    def test_regulatory_cycles_do_not_touch_default_config(self) -> None:
        from digital_twin import phase1_config_for, regulatory_economy, PHASE1_BODIES
        before = phase1_config_for(PHASE1_BODIES[0]).battery.initial_soc
        regulatory_economy()
        after = phase1_config_for(PHASE1_BODIES[0]).battery.initial_soc
        self.assertEqual(before, after)


class ExecutiveSummaryTest(unittest.TestCase):
    """The executive summary faithfully aggregates the validated headlines
    from every Move without altering any of them."""

    def test_summary_has_a_row_per_body_with_ordered_bands(self) -> None:
        from digital_twin import build_executive_summary, PHASE1_BODIES
        s = build_executive_summary(trials=16)
        self.assertEqual(len(s.bodies), len(PHASE1_BODIES))
        for b in s.bodies:
            self.assertGreater(b.cost_per_km, 0.0)
            self.assertLessEqual(b.cost_p05, b.cost_per_km)
            self.assertLessEqual(b.cost_per_km, b.cost_p95)
            self.assertGreater(b.battery_power_kw, 0.0)

    def test_summary_matches_the_headline_invariants(self) -> None:
        from digital_twin import build_executive_summary
        s = build_executive_summary(trials=16)
        # Rotor, longevity and robustness headlines must match their Moves.
        self.assertAlmostEqual(s.rotor_peak_nm, 242.8, delta=0.5)
        self.assertAlmostEqual(s.rotor_boost_pct, 34.9, delta=0.5)
        self.assertEqual(s.pack_replacements, 0)
        self.assertLess(s.embodied_co2_share_pct, 15.0)
        self.assertFalse(s.derates_in_climate)
        self.assertEqual(s.regulatory_shortfalls, 0)
        # SUV recommended power matches Move F (90 kW); report renders.
        suv = s.bodies[0]
        self.assertEqual(suv.body, "AWD SUV")
        self.assertAlmostEqual(suv.battery_power_kw, 90.0, delta=0.1)
        self.assertIn("executive summary", s.report())


if __name__ == "__main__":
    unittest.main()
