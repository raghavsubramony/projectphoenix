# ATPE — Additional Insights & Engine Requirements Specification (ERS)

Source: ATPE design conversation (ChatGPT, "Project PHOENIX"). This document folds new
technical detail into the existing concept set and records the formal requirements spec the
digital twin should be measured against.

Cross-references: [01-atpe-concept.md](01-atpe-concept.md),
[07-core-concept-refinements.md](07-core-concept-refinements.md),
[08-digital-twin-design.md](08-digital-twin-design.md).

## 1. Framing Refinement — Torque, Power, Acceleration Are Coupled

The core engineering premise: torque, power, and acceleration are **not independently
controllable**. Power follows from torque and speed ($P = \tau \cdot \omega$), so the
controller's real job is to decide *how much torque to produce at a given operating point*,
which then fixes power and acceleration. The ATPE's free-piston + series-hybrid architecture
decouples this at the wheels (traction motor) while the engine optimizes purely for generation
efficiency — reinforcing the series-hybrid choice in [03-integration-viability.md](03-integration-viability.md).

## 2. Driver-Intent Recognition (new input layer)

Beyond throttle position, the controller infers **intent** from pedal *rate* and context:

| Driver action | Inferred mode |
|---------------|---------------|
| Pedal slowly to ~20% | Economy |
| Pedal quickly to ~70% | Overtake |
| Pedal floored | Maximum acceleration |
| Climbing / loaded | Maximum torque |
| Steady cruise | Minimum fuel consumption |

Full input set the supervisory controller should monitor: accelerator position, **pedal
movement speed**, steering angle, vehicle speed, road gradient, vehicle weight, wheel slip,
battery state, GPS/terrain prediction, driver profile, weather, traction conditions.

> Twin mapping: our `spike_threshold_w` override in [controller.py](../digital_twin/controller.py)
> is a first proxy for "Overtake/Max-accel" intent. A future `DriverIntent` enum would make
> this explicit (see roadmap §7).

## 3. Variable-Geometry Combustion Mechanisms (new detail)

The ATPE achieves per-cylinder torque shaping through electronically controlled geometry —
feasible precisely because there is no crankshaft or camshaft:

- **Variable Valve Timing** — fully electronic valves; more overlap & cylinder pressure at low speed (torque), optimized breathing at high speed (power).
- **Variable Compression Ratio** — continuously variable (cf. Infiniti VC-Turbo); high CR for efficiency, low CR for high-boost power.
- **Variable Stroke** — free piston enables on-demand expansion ratio (Atkinson/Miller-like), already noted as a key efficiency lever.
- **Variable Intake Geometry** — long runners for torque, short runners for power.
- **Cylinder Deactivation** — generalized here into the multi-tier sizing concept.

These are the physical knobs behind the flat per-tier efficiency assumption in
[07-core-concept-refinements.md](07-core-concept-refinements.md) §2.

## 4. Engine Requirements Specification (ERS) — Project PHOENIX v0.1

### 4.1 Vision
A next-generation **crankless** internal combustion engine that: uses independently controlled
free-piston combustion modules; generates electricity via integrated linear generators;
delivers only the power demanded; maximizes fuel efficiency; is modular, scalable, and
software-defined.

### 4.2 Design Philosophy
- Simpler than a conventional ICE where practical.
- Replace mechanical complexity with electronic control where it gives a clear advantage.
- Operate each cylinder **independently**.
- **Graceful degradation** — keep running with one cylinder offline.
- Support future AI-based optimization (deterministic control first).

### 4.3 Reference Vehicle (P1 demonstrator)
| Attribute | Value |
|-----------|-------|
| Vehicle type | Mid-size SUV / Sedan |
| Kerb weight | 1600–1900 kg |
| Seating | 5 |
| Drivetrain | Electric traction motor fed by engine-generated electricity (series hybrid) |
| Drive | FWD or AWD |

### 4.4 Performance Targets (P1)
| Metric | Target |
|--------|--------|
| Peak power | 150 kW |
| Continuous power | 100 kW |
| Peak torque (traction motor) | ≥ 350 N·m |
| 0–100 km/h | < 8 s |
| Top speed | 180 km/h (P1 baseline) |
| Gradeability | 20% incline at highway speed |

### 4.5 Efficiency Targets
| Metric | Target |
|--------|--------|
| Brake thermal efficiency (fuel → piston work) | > 45% |
| Generator efficiency (piston → electrical) | > 95% |
| Overall fuel-to-wheel efficiency | > 38% |
| Idle fuel consumption | ≈ 0 (combustion stops when no power needed) |

### 4.6 Mechanical Requirements
| Item | Value |
|------|-------|
| Number of modules (prototype) | 4 |
| Crankshaft | None |
| Camshaft | None |
| Valve control | Fully electronic |
| Variable compression | Yes |
| Variable stroke | Yes |
| Linear generator | Integrated per cylinder |

### 4.7 Electrical System
| Item | Value |
|------|-------|
| DC bus | 400–800 V |
| Battery | Small buffer battery or supercapacitor |
| Power electronics | Bidirectional |
| Regenerative capability | Supported |

### 4.8 Reliability
| Item | Value |
|------|-------|
| Design life | 10,000 operating hours |
| MTBF | Competitive with modern automotive engines |
| Fault tolerance | Operate with one cylinder offline |

### 4.9 Primary Engineering Objective (testable)
> Develop a modular free-piston combustion engine with AI-ready electronic control that
> achieves **higher real-world efficiency than a conventional crankshaft ICE** while preserving
> comparable performance, scalability, and reliability.

## 5. Two-Phase Program (reconciles with our Phase-1 / Phase-2)

- **PHOENIX-P1 (Technology Demonstrator):** validate the crankless free-piston concept at 150–200 kW; prove stable operation, efficiency, and control.
- **PHOENIX-P2 (Performance Version):** scale the validated architecture to 600 kW+, targeting a 200 mph (322 km/h) capable vehicle (hypercar/GT class, ≥300 kW/tonne, 0–100 km/h < 3.0 s).

This matches the modular "same software, different parameters" approach already in
[config.py](../digital_twin/config.py) (`phase1_config` / `phase2_config`). The P2 numbers
above are more aggressive than our current performance config — see reconciliation §8.

## 6. Development Gates (success criteria before advancing)

1. **Gate 1** — Single-cylinder digital twin
2. **Gate 2** — Stable free-piston operation
3. **Gate 3** — Linear generator integration
4. **Gate 4** — Multi-cylinder synchronization
5. **Gate 5** — Vehicle integration
6. **Gate 6** — AI optimization
7. **Gate 7** — Mechanical design and CAD

Our current twin sits at roughly **Gate 5** (vehicle integration, deterministic control), with
single-cylinder combustion fidelity (Gates 1–2) still abstracted.

## 7. What This Adds to the Digital Twin (backlog)

- ✅ **Acceptance harness (done):** P1 targets encoded as objective pass/fail checks — see §9 and [digital_twin/acceptance.py](../digital_twin/acceptance.py).
- ✅ **Split efficiency chain (done):** generator efficiency (`ATPEConfig.generator_efficiency`, >95%) is now separated from brake thermal efficiency (>45%), so results map directly to ERS §4.5.
- **`DriverIntent` layer:** derive intent from pedal-rate, feeding the controller (replaces the raw spike threshold).
- **Graceful degradation:** allow a tier/cylinder to be flagged offline and confirm the twin continues within reduced limits (ERS §4.8).
- **Variable compression/stroke knob:** expose an efficiency-vs-power trade per tier rather than a fixed point.
- **Benchmark harness:** compare against a conventional 2.0 L turbo baseline on identical cycles (validation per the source's Stage 6).

## 8. Reconciliation Notes vs. Existing Docs

| Topic | Existing docs | ERS insight | Resolution |
|-------|---------------|-------------|------------|
| Efficiency definition | single fuel→electrical η (0.38–0.44) | BTE >45% + gen >95% (≈ >42% combined) | Consistent; split is a v1 fidelity upgrade |
| Module count | 8 cylinders across 3 tiers | 4 modules (prototype) | Prototype is smaller; our 3-tier stack is the productionized form |
| P2 output | ~700–900 kW (docs/04) | 600 kW+ | Same ballpark; align on a single P2 target before P2 modeling |
| Battery | 15–25 kWh LFP | "small buffer / supercapacitor" | Docs favor a usable EV range; ERS is generation-only. Keep PHEV sizing, note range-extender variant |

## 9. ERS Acceptance Checks (implemented in the twin)

The P1 targets above are now encoded as **objective, simulation-backed pass/fail checks** in
[digital_twin/acceptance.py](../digital_twin/acceptance.py). Run them with `python main.py`
(final section) or:

```python
from digital_twin import build_default_twin, phase1_targets
from digital_twin.acceptance import report
print(report(build_default_twin, phase1_targets()))
```

Each check derives its "actual" value from the same physics models the rest of the twin uses
(draining the real buffer/battery state), so the figures stay honest. Performance checks run
dedicated capability simulations:

| Check | How it is computed |
|-------|--------------------|
| Peak power | Burst sum of all sources (gen + buffer + battery) in one step |
| Continuous power | ATPE total continuous generation capacity |
| Peak motor torque | Traction-motor spec ([config.py](../digital_twin/config.py) `TractionConfig`) |
| 0–100 km/h | Full-throttle longitudinal integration, motor torque- & power-limited |
| Top speed | Speed where sustained power balances road load |
| Gradeability | Max grade at target speed under peak power |
| Brake thermal eff | Best tier's fuel→electrical ÷ generator efficiency |
| Generator efficiency | `ATPEConfig.generator_efficiency` |
| Fuel-to-wheel eff | Best tier's fuel→electrical × driveline efficiency |

### Result: 9 / 9 pass (AWD SUV, after motor sizing)

The primary AWD SUV now carries a **160 kW** traction motor (sized by the harness, see below) and
clears every target:

```
[PASS] Peak power               150 kW   ->  440 kW
[PASS] Continuous power         100 kW   ->  230 kW
[PASS] Peak motor torque        350 Nm   ->  380 Nm
[PASS] 0-100 km/h               <= 8.0 s ->  7.9 s
[PASS] Top speed                180 km/h ->  192.6 km/h
[PASS] Gradeability @100 km/h   20 %     ->  22.0 %
[PASS] Brake thermal eff (best) 45 %     ->  45.8 %
[PASS] Generator efficiency     95 %     ->  96.0 %
[PASS] Fuel-to-wheel eff (best) 38 %     ->  40.5 %
```

> **History — the original 150 kW shortfall.** With the first 150 kW motor the SUV missed 0–100 by
> a hair (8.2 vs 8.0 s). That was a genuine, honest finding, not a modelling bug: the ERS reference
> vehicle (§4.3) is a 1,600–1,900 kg sedan, but the twin models a **2,200 kg AWD SUV**, and the
> 150 kW motor was the true wheel-power ceiling. The harness quantified the gap and the fix
> (+10 kW) was applied — exactly the value of an acceptance harness: surface a real spec/config
> tension early, with a number attached.

### Body sweep + per-body targets

`compare_bodies()` runs the powertrain across body styles; the 0–100 spread is pure chassis
physics. A single SUV target list is misleading for other bodies — a pickup is not expected to
match a hatchback's 0–100. So powertrain-level targets (power, torque, efficiency) are held
**identical** across bodies (same engine), while the three chassis-dependent targets reflect
realistic **class expectations**:

| Body | Mass (kg) | 0–100 (s) | 0–100 target | Top-speed target | Gradeability target |
|------|----------:|----------:|:-----------:|:----------------:|:-------------------:|
| **AWD SUV** (primary) | 2200 | 7.9 | ≤ 8.0 s | ≥ 180 km/h | ≥ 20 % |
| Sedan | 1650 | 5.8 | ≤ 7.5 s | ≥ 200 km/h | ≥ 20 % |
| Hatchback | 1400 | 4.8 | ≤ 9.5 s | ≥ 175 km/h | ≥ 18 % |
| Crossover | 1850 | 6.7 | ≤ 8.5 s | ≥ 185 km/h | ≥ 20 % |
| Pickup | 2500 | 9.5 | ≤ 11.0 s | ≥ 160 km/h | ≥ 18 % |
| Van / MPV | 2300 | 8.6 | ≤ 10.0 s | ≥ 170 km/h | ≥ 18 % |

These live in `phase1_targets_for(body)` / `phase1_body_targets()`
([acceptance.py](../digital_twin/acceptance.py)); `report_bodies()` evaluates each body against its
own list. **Result: all six bodies now pass 9/9.**

### Why the heavy bodies needed sizing (and by how much)

The two original FAILs (SUV 0–100, pickup gradeability) were **power-ceiling** effects — the
150 kW motor was the wheel-power cap, not a model artifact:

- **AWD SUV → 0–100:** acceleration is power-limited. At 2,200 kg, 150 kW gives ~68 W/kg → 8.2 s;
  it is the heaviest body asked to hit a brisk number.
- **Pickup → gradeability:** climbing at 100 km/h is also power-limited, and the pickup is both the
  heaviest (2,500 kg) **and** highest-drag (Cd 0.42, 3.2 m²) body, so aero eats a large share of
  the 150 kW before any reaches the grade.

### Recommended motor sizing (auto-suggested → applied)

`recommend_motor(build, targets)` / `recommend_motors()`
([acceptance.py](../digital_twin/acceptance.py)) bisect the minimum motor power that clears each
body's targets: **peak** power sizes 0–100 + gradeability, **continuous** power sizes top speed
(capped by the engine's own output). The recommendation drove the per-body `motor_peak_kw` in
[config.py](../digital_twin/config.py):

| Body | Motor (applied) | Min peak to pass | Continuous (top speed) | Headroom |
|------|:--------------:|:----------------:|:---------------------:|---------|
| **AWD SUV** | **160 kW** | 160 kW | 95 kW | sized exactly (+10 kW vs 150) |
| Sedan | 150 kW | 110 kW | 80 kW | 40 kW spare |
| Hatchback | 150 kW | 85 kW | 55 kW | 65 kW spare (lightest) |
| Crossover | 150 kW | 125 kW | 80 kW | 25 kW spare |
| **Pickup** | **160 kW** | 160 kW | 95 kW | sized exactly (+10 kW vs 150) |
| Van / MPV | 150 kW | 140 kW | 95 kW | 10 kW spare (thin) |

The takeaway: both heavy bodies close with a **modest +10 kW (→160 kW)** bump — still a "150 kW
class" motor — while the lighter bodies keep 25–65 kW of headroom. The SUV and pickup now carry
160 kW in their configs; every body passes its class targets 9/9.

### Motor-size sweep study (60 → 250 kW)

Rather than a single recommendation, `sweep_grid()` / `sweep_motor()`
([acceptance.py](../digital_twin/acceptance.py)) evaluate every body across the **60–250 kW** range
in **10 kW steps**, so you can read where each body crosses from fail (`x`) to pass (`.`) against
its own class targets (here both peak and continuous are set to the swept value — a single-rating
motor):

```
  kW   AWD SUV  Sedan  Hatchback  Crossover  Pickup  Van / MPV
  ------------------------------------------------------------
   60         x      x          x          x       x          x
   80         x      x          x          x       x          x
   90         x      x          .          x       x          x
  110         x      .          .          x       x          x
  130         x      .          .          .       x          x
  140         x      .          .          .       x          .
  150         x      .          .          .       x          .
  160         .      .          .          .       .          .
  ...        (all pass through 250 kW)
```

**Minimum passing motor per body (the comparative result):**

| Body | Min motor | Binding target | Reading |
|------|:--------:|:--------------:|---------|
| Hatchback | 90 kW | gradeability | lightest body — needs the least |
| Sedan | 110 kW | 0–100 | aero-clean, low mass |
| Crossover | 130 kW | gradeability | mid-weight |
| Van / MPV | 140 kW | gradeability | tall/draggy but modest target |
| **AWD SUV** | **160 kW** | 0–100 | heavy + brisk accel target |
| **Pickup** | **160 kW** | gradeability | heaviest + highest drag |

How to read it:
- **Where a body flips to `.`** is the smallest motor that satisfies *all three* chassis targets.
- **Binding target** names which check is tightest at that size — accel for the light/fast bodies,
  gradeability for the heavy/draggy ones. This tells you *why* each body needs what it needs.
- The grid's 10 kW granularity rounds up to the next step (e.g. Hatchback shows 90 kW here vs the
  bisected 85 kW from `recommend_motor()`); use `recommend_motors()` for the exact watt-level figure
  and `sweep_grid()` for the comparative picture.

**Design conclusion:** a **two-motor family covers the whole range** — a ~150 kW unit for the light
and mid bodies (hatch/sedan/crossover/van, all ≤140 kW) and a **160 kW unit for the heavy
SUV/pickup**. Nothing in the Phase-1 lineup needs more than 160 kW; the larger ratings only become
relevant for the Phase-2 performance stack.

---

## Closed-loop AI control: a learned policy that drives the twin

The rule-based `UnifiedController` is deliberately simple (three nested loops + a spike override).
A natural question is *whether a learned controller can match it*. The `ml_study/` package answers
this end-to-end:

1. **Collect** — drive the charge-sustaining twin over the standard cycles and record, at every
   step, the observation `(speed, demand, battery_soc, buffer_soc)`, the tier decision the
   rule-based controller made, and its generation setpoint
   ([ml_study/collect.py](../ml_study/collect.py)).
2. **Learn online** — an incremental softmax classifier (tier choice) plus a linear regressor
   (setpoint) are trained *prequentially* — each sample is predicted before it is learned, so the
   reported accuracy is honest out-of-sample ([ml_study/study.py](../ml_study/study.py)). The policy
   reaches **~94.5 % per-step tier accuracy** and **~6.1 kW setpoint MAE**.
3. **Close the loop** — `LearnedController` ([ml_study/policy.py](../ml_study/policy.py)) wraps the
   fitted study behind the *same* `decide()` interface as the rule-based controller, so it can be
   dropped straight into `Powertrain(cfg, controller=...)`. `learned_bodies(study)` builds one
   learned twin per body for a fleet A/B.

### Why raw fuel deltas are a trap (and the fix)

Running the learned policy closed-loop and comparing raw fuel against the rule-based baseline looks
dramatic — swings of **±8 L/100 km**:

| Body | Highway | Tow+grade | Mixed |
|------|:------:|:--------:|:----:|
| AWD SUV | +4.52 | −7.16 | +8.22 |
| Pickup | +3.71 | −8.78 | +7.00 |

But those numbers are **meaningless on their own**: the learned policy ends each cycle at a
*different battery SoC* than it started, and the raw fuel delta tracks that SoC drift almost
exactly. A "−7 L saving" on towing is just the policy quietly **draining the battery**
(charge-depleting cheat); a "+8 L loss" on mixed is it **over-charging** by ~13 kWh and dumping the
energy as fuel.

The fix is the standard **charge-sustaining correction** (cf. SAE J1711): convert the net battery
drift back into the fuel that *would* have been needed to generate it, at the best tier efficiency,
and fold it into an **energy-equivalent** fuel figure (`equiv_fuel_l_per_100km`,
[simulation.py](../digital_twin/simulation.py)). Once SoC-corrected, the same A/B collapses to a
**±0.3–1.0 L/100 km** spread:

| Body | Highway | Tow+grade | Mixed |
|------|:------:|:--------:|:----:|
| AWD SUV | +0.37 | −0.52 | +0.52 |
| Pickup | +0.26 | −0.99 | +0.30 |

### What this proves (honestly)

- **Imitation works at the energy level.** The wild raw swings were SoC accounting, not real
  efficiency; corrected, the learned policy reproduces the teacher's energy behaviour to within
  ~1 L/100 km across every body and cycle.
- **Per-step accuracy ≠ charge-sustaining.** 94.5 % tier accuracy still lets small, *correlated*
  setpoint errors integrate into SoC drift. The learned policy is a faithful-but-imperfect mimic,
  not a free lunch.
- **The robustness ceiling shows up on the hardest body.** Only the **Pickup** picks up shortfalls
  under the learned policy (+55 on tow+grade, +1 on mixed) — the heaviest, most transient-stressed
  body is where pure imitation first cracks.

**Conclusion:** a tiny online learner *can* drive the full series-hybrid twin closed-loop and track
the engineered controller's energy budget — but matching it to the last litre (and the last
shortfall on the pickup) needs **SoC-aware / closed-loop training**, not pure step-wise imitation.
That is the motivation for the next iteration of the AI controller.

---

## Battery durability & thermal realism: the pack is throughput-bound, not heat-bound

A fair question for any hybrid is "what happens to the battery over a vehicle's life?" Two physical
effects were added — both **opt-in** so the validated thermodynamically-ideal figures are untouched
(`cfg.battery.thermal is None` reproduces the baseline exactly):

1. **Lumped single-node thermal model** ([battery.py](../digital_twin/battery.py)). Internal
   resistance generates I²R heat referred to a nominal bus voltage; Newtonian cooling removes it;
   above `derate_start_c` the usable power ramps linearly down to a `floor_fraction` at
   `derate_end_c` (battery-management thermal protection).
2. **Durability accounting** — cumulative bidirectional throughput is converted to **equivalent full
   cycles (EFC)** and, given a cycle-life rating, projected to a pack service life
   ([simulation.py](../digital_twin/simulation.py)).

### Charge-sustaining barely cycles the pack

Because the engine (not the battery) supplies trip energy, the pack works as a *buffer* and turns
over a tiny fraction of its capacity per km. Measured **EFC per 100 km** and the implied pack life
(at a conservative 4000-EFC LFP rating):

| Body | EFC / 100 km (mixed) | Projected life |
|------|:-------------------:|:--------------:|
| Hatchback | 0.31 | ~1.28 M km |
| AWD SUV | 0.45 | ~0.89 M km |
| Pickup | 0.45 | ~0.88 M km |

Even the worst body projects **~0.6 M km** of pack life — far beyond any realistic vehicle mileage.
The architecture's battery-durability advantage is real and quantified: **it never deep-cycles.**

### The pack also never gets hot in-cycle

Running the lumped-thermal model over the transient-stress launches, the pack peak temperature
rises **~1 °C** (from 25 °C ambient) and **never approaches the 45 °C derate threshold** — the
engine and inertial buffer absorb the transients, so the battery is shielded from sustained
high-current abuse. Thermal derating simply never engages inside the architecture.

### …but the derate model is real

To confirm the thermal model is alive (not inert by construction), a small, weakly-cooled pack
driven at a constant 100 kW heats and **clamps its own output**:

| t (s) | Temp | Delivered | Derate |
|:----:|:----:|:--------:|:-----:|
| 0 | 25.8 °C | 100.0 kW | 1.00 |
| 60 | 58.3 °C | 46.4 kW | 0.38 |
| 120 | 61.4 °C | 36.0 kW | 0.30 |

The model heats, crosses the threshold, and rolls power back to its floor — exactly as a real BMS
would. **The point is precisely that the Phoenix architecture never puts the pack in that regime.**

**Conclusion:** the battery's life in this architecture is governed by **throughput (cycle count),
not peak temperature**, and charge-sustaining operation keeps throughput so low that projected pack
life (≥0.6 M km) dwarfs vehicle life. The PCMRITMS buffer + engine carry the transients that would
otherwise heat and cycle the pack.

---

## Validation: sensitivity ranking + one-command verification

A model is only as trustworthy as its robustness to its own assumptions. Two tools close that gap
([validation.py](../digital_twin/validation.py), [verify.py](../verify.py)).

### Which inputs actually drive the result?

Rather than trusting a single point estimate, `sensitivity_table` perturbs each physical input by
±5 % and reports its **elasticity** — the percentage change in fuel economy per percentage change in
the input. This makes the design levers directly comparable across units (kg, Cd, efficiency):

**Highway cruise (charge-sustaining AWD SUV):**

| Parameter | Elasticity | Reading |
|-----------|:---------:|---------|
| Driveline efficiency | −1.02 | every lost % in the bus→wheel path is ~1 % more fuel |
| ATPE tier efficiency | −1.00 | fuel scales inversely with conversion efficiency, as expected |
| Drag coefficient / frontal area | +0.56 | **aero dominates at cruise** |
| Vehicle mass | +0.44 | secondary at steady speed |
| Rolling resistance | +0.26 | tertiary |
| Aux load | +0.02 | negligible |

The ranking *shifts with the duty cycle*: on the **mixed** cycle (with its urban stop-go) mass and
driveline rise sharply (mass +1.14, driveline −2.8) because acceleration energy and mode-switching
dominate, while aero recedes. That cycle-dependence is itself the finding: **there is no single
"most important" parameter — it depends on how the vehicle is driven**, which is exactly why a
sensitivity sweep beats a single sensitivity claim.

### Capability scales monotonically with the reservoir

`capability_sweep` traces the transient launch shortfall as the inertial reservoir is scaled, on the
slew- and battery-limited launch cycle where the buffer actually matters:

| Reservoir | Transient shortfall events |
|:--------:|:--------------------------:|
| 1× (118 kJ) | 7 |
| 2× | 0 |
| 3× | 0 |
| 4× | 0 |

The response is **monotone and saturating** — a 2× reservoir already eliminates every shortfall, and
more never hurts. This is the quantitative version of the PCMRITMS thesis: *burst rating sets the
peak, reservoir size sets the duration.*

### One command reproduces everything

[verify.py](../verify.py) is the single entry point that re-derives **every headline number from the
live code** and checks it against its validated value, then runs the full test suite:

```
.venv\Scripts\python.exe verify.py
```

```
  [PASS] Rotor peak torque         242.8 N.m vs 242.8 N.m
  [PASS] Coupled buffer burst      140.2 kW  vs 140.2 kW
  [PASS] SUV highway fuel          4.62 L/100km
  [PASS] All 6 bodies pass all ERS checks       9/9 per body
  [PASS] Projected pack life > 500k km          858k km
  [PASS] 2x buffer eliminates transient shortfalls
  [PASS] Aero dominates mass at highway speed
  ...
  RESULT: PASS  (18 checks + 43 tests)
```

Because the checks recompute from the model (not from cached constants), any silent drift in the
physics, control law, or configuration is caught immediately — the concept stays honest as the code
evolves.

---

## Move D — Total cost of ownership & true lifecycle CO₂

Fuel economy alone does not decide whether a powertrain is worth building. The decision an owner (or
a fleet buyer, or a regulator) actually makes is about **cost per kilometre over the vehicle's life**
and **all-in CO₂ from cradle to road** — including the carbon baked into the battery before it ever
turns a wheel. `digital_twin/economics.py` rolls the validated per-cycle fleet results up into both.

Each body's fuel and durability numbers are blended across a representative usage mix (30 % urban,
35 % highway, 30 % mixed, 5 % tow+grade), then projected over a 250 000 km lifetime:

```
=== Fleet TCO + lifecycle CO2 ===
  Body          L/100km  Cost/km  Battery   CO2 t   g/km*
  --------------------------------------------------------
  AWD SUV          2.70    0.073       0x    26.2     105
  Sedan            0.67    0.041       0x    11.9      48
  Hatchback        0.57    0.039       0x    11.2      45
  Crossover        1.15    0.048       0x    15.2      61
  Pickup           4.03    0.094       0x    35.5     142
  Van / MPV        3.06    0.079       0x    28.7     115
  (* g/km = all-in lifecycle CO2; Battery = mid-life replacements)
```

Two results fall straight out of the architecture and matter more than the absolute costs:

- **The pack is never replaced (`0x` for every body).** Move B already showed the battery is
  throughput-bound, not heat-bound, with 0.6–1.6 M km of projected life. Over a 250 k km lifetime the
  charge-sustaining duty barely cycles it, so there is **no mid-life battery replacement cost** — the
  single largest hidden cost in most electrified powertrains simply does not exist here.

- **Embodied battery CO₂ is a rounding error, not the headline.** The 20 kWh pack carries only
  ~1.2 t of embodied CO₂ — under 5 % of the SUV's 26 t lifecycle total, which is dominated by tailpipe
  (15.6 t), the glider itself (6 t), and well-to-tank fuel (3.4 t). A small, hard-working pack pays its
  carbon debt back almost immediately; it is not the thing to optimise.

The honest takeaway: this architecture's economic and carbon story is carried by the **small pack that
never needs replacing**, not by chasing the last fraction of a litre. The full rollup
(`fleet_tco`, `tco_table`, `TcoResult.report()`) is reproduced in `main.py` and locked by
`LifecycleTcoTest`.

---

## Move E — Closed-loop rotor surge control: the buffer is energy-bound, not surge-bound

The PCMRITMS rotor coupling gave the inertial buffer a 140 kW brief-burst ceiling (Move "coupling"),
versus its 90 kW continuous rating. The obvious follow-up question: is that surge headroom the thing
that decides whether a launch is met — and can a smarter, closed-loop controller spend it more
intelligently? `ClosedLoopRotorController` is built to test exactly this. It wraps the rule-based
`UnifiedController` (so fuel economy and mode logic are untouched) and gates the buffer's per-step
surge authority: it authorizes the full 140 kW burst **only** when the launch deficit beyond
generation genuinely exceeds what the continuous buffer *plus* the available battery can supply, and
the reservoir is above a reserve floor. Otherwise it holds the buffer to 90 kW and lets the battery
absorb the overflow, conserving the irreplaceable inertial reserve. With its per-step ceiling left
unset the buffer behaves exactly as before, so the controller is a strict, opt-in superset — the 51
locked invariants are unchanged.

**The result on representative duty is a clean negative — and that is the finding.** Across all 24
body × cycle stress runs, the closed-loop controller produces *identical* shortfalls, buffer
throughput, and reserve to static coupling:

```
Representative stress fleet (24 body x cycle runs):
  closed-loop vs static coupling -> 0 differ, 0 worse
```

The reason is structural: on every realistic and stress cycle the buffer is **energy-bound**. Its
reservoir empties to 0 % before — and independently of — the 140 kW surge ceiling ever becoming the
binding constraint (observed buffer peaks top out at the 90 kW continuous rating, never the surge
ceiling). This is the same lesson the Move C capability sweep reached from the other direction:
scaling buffer *energy* eliminates transient shortfalls, while the surge *power* from rotor coupling
does not. Surge timing cannot help when surge power was never the bottleneck.

To prove the controller is sound (and not silently inert), a controlled *power-bound* launch — ample
reservoir energy, a sizeable battery assist, repeated moderate launches — puts the buffer into the one
regime where surge is actually reachable:

```
Controlled power-bound launch (reservoir has energy to spare):
  static : buffer peak 140.2 kW, throughput  1359 kJ, 2 shortfalls
  closed : buffer peak  90.0 kW, throughput  1301 kJ, 1 shortfalls
```

Here the gate visibly engages: it caps the buffer at its continuous 90 kW, shifts the launch overflow
onto the battery, and so spends *less* inertial reserve — at equal-or-better capability (the shortfall
count does not rise; here it even falls). This is genuinely smarter reservoir use, but it only matters
in a regime this architecture never naturally enters.

The honest conclusion that closes the rotor-coupling thread: the 140 kW surge number is real, but it
is **not** the capability lever. Transient capability in this powertrain is governed by buffer energy
(and generation slew), not by surge power or how cleverly it is timed. The controller and its A/B are
reproduced in `main.py` and locked by `ClosedLoopRotorTest`; `verify.py` additionally asserts the
closed loop never worsens capability versus static coupling.

---

## Move F — Component right-sizing: how big does the battery actually need to be?

Five Moves of analysis kept circling one question without answering it directly: *given everything we
now know, what should we actually build?* Move F answers it. It sweeps each capability lever in
isolation and then reports, per body, the **smallest battery discharge power that keeps the vehicle
capable** (`digital_twin/sizing.py`).

The first result is the one that reframes everything. Sweeping the three candidate levers — buffer
energy, generation slew, and battery discharge power — shows a single dominant lever:

| Lever swept (others fixed) | Effect on total shortfalls |
|----------------------------|----------------------------|
| Buffer energy (1× → 8×) | marginal (SUV 35 → 25; never reaches 0) |
| Generation slew (40 → 250 kW/s) | marginal (SUV 39 → 33) |
| **Battery discharge power (40 → 120 kW)** | **decisive (SUV 35 → 1; Pickup 69 → 1)** |

Transient capability is governed, at the system level, by **battery discharge power**. This refines
the Move E conclusion rather than contradicting it: Move E showed the inertial buffer's *surge* is not
the limit; Move F shows what the limit actually is. Concretely, the PCMRITMS rotor surge makes **zero**
difference to the required pack power — coupled and uncoupled size identically, exactly as Move E
predicted from the other direction.

With the lever identified, the right-sizing recommendation falls out:

```
=== Battery power right-sizing (target <= 2 shortfall steps, 20 kWh pack) ===
  Body          Min kW  C-rate  vs 120kW  Capable
  -----------------------------------------------
  AWD SUV           90    4.5C      -25%      yes
  Sedan             60    3.0C      -50%      yes
  Hatchback         60    3.0C      -50%      yes
  Crossover         70    3.5C      -42%      yes
  Pickup            60    3.0C      -50%      yes
  Van / MPV         60    3.0C      -50%      yes
```

The headline is a **down-sizing** one. The validated default pack power (120 kW = 6C on the 20 kWh
pack) is generously over-specced. Most bodies stay fully capable at **3C (60 kW)** — half the default —
and only the heavy AWD SUV needs as much as **4.5C (90 kW)**. A lower-C-rate cell is cheaper and more
energy-dense per kg, so right-sizing the *power* is a genuine cost-and-mass lever that the earlier
fuel/CO₂ Moves could not see.

Crucially, this down-sizing leaves the rest of the story intact: the pack stays **20 kWh** (so Move D's
small-pack, never-replaced, low-embodied-CO₂ economics are unchanged), and the energy/durability and
fuel numbers are untouched — this is a read-only design sweep that changes none of the validated
configuration. The study, its lever ranking, and the recommendation are reproduced in `main.py` and
locked by `BatterySizingTest`; `verify.py` asserts every body right-sizes to a capable power and that
the default is provably over-specced.

**The synthesised "what to build" answer:** a 20 kWh pack rated for ~3C on the light bodies and ~4.5C
on the heavy SUV, paired with the inertial buffer for sub-second spikes — not the 6C pack the optimistic
default assumed. Capability comes from modest, well-matched battery power, not from oversized cells or
flashy peak-power hardware.

---



## Move G — Uncertainty bands: every headline number is a distribution, not a point

Moves A–F report **point estimates** — single numbers like "SUV highway 4.62 L/100 km" or
"Pickup 0.094 €/km". But every input that feeds those numbers is itself uncertain: vehicle mass,
drag, rolling resistance and aux load are tolerances; fuel price, maintenance, battery cost and the
embodied-CO₂ factors are forecasts. Move G asks the honest follow-up question — **how wide is each
headline number once you admit that uncertainty?** — and answers it by Monte-Carlo.

Where Move C perturbed inputs **one at a time** (to *rank* sensitivity), Move G perturbs **all of
them together** (to *size* the combined spread). Each trial draws every uncertain input from a
median-preserving log-normal (`rng.lognormvariate(0, σ)`, so the median stays at the nominal value
and draws are always positive), runs the full physics + economics, and records the result. Hundreds
of trials turn each scalar into a distribution with a 5th/50th/95th-percentile band.

```
=== AWD SUV Highway cruise: fuel_l_per_100km (200 trials) ===
  nominal : 4.623 L/100km
  90% band: [4.24, 5.09] L/100km (+/-9.2% of median)
```

```
=== Fleet uncertainty bands (64 trials, 90% CI) ===
  Body               Cost/km (5-95%)      CO2 g/km (5-95%)
  ------------------------------------------------------
  AWD SUV        0.074 [0.058-0.091]          105 [94-120]
  Sedan          0.041 [0.031-0.052]            48 [43-55]
  Hatchback      0.039 [0.030-0.051]            44 [37-51]
  Crossover      0.048 [0.040-0.060]            61 [51-73]
  Pickup         0.105 [0.080-0.139]         147 [129-194]
  Van / MPV      0.079 [0.063-0.097]         115 [105-128]
```

Two findings fall out:

1. **The point estimates are honest medians.** For the near-linear bodies the nominal sits right at
   the median of its band (Sedan 0.041, Crossover 0.048, SUV 0.074 reproduce Move D exactly), so the
   single numbers were not flattering — they are the centre of the spread, not its optimistic edge.
2. **Risk is asymmetric, and it grows with the body.** The light bodies carry a tight ±10 % fuel
   band; the heavy **Pickup** carries a visibly **right-skewed** cost/CO₂ band (median 0.105 €/km,
   95th percentile 0.139) — the same draws that make a heavy vehicle heavier/draggier cost it
   disproportionately more. The worst-case tail, not the mean, is where the heavy bodies hurt.

This refines every earlier Move without overturning any of them: the rankings, the lever ordering and
the "what to build" recommendation all hold *at the median*, and Move G simply attaches an error bar
so the conclusions can be quoted with a confidence interval instead of false precision. The bands are
read-only (they perturb copies, never the validated configuration), seeded-reproducible, and pure
stdlib (`random`, `math`, `statistics`). They are reproduced in `main.py`, locked by
`MonteCarloUncertaintyTest`, and `verify.py` asserts the validated nominal lies inside its own 90 %
band and that every fleet band is well-formed.

---

## Move H - Ambient temperature stress: what -10 C to +40 C does to the numbers

Every validated fuel/CO2 figure so far is quoted at a benign reference temperature. Real vehicles
run from a -10 C winter morning to a +40 C summer afternoon, and three physical effects move the
numbers, all expressible through the existing configurable parameters:

1. **Air density.** Cold air is denser, so aerodynamic drag (`0.5 rho Cd A v^2`) rises in winter and
   falls in summer. Density scales as `rho(T) = rho_ref * T_ref / T` (ideal gas, absolute
   temperature), which folds exactly into an equivalent drag-coefficient scaling: +10% drag at
   -10 C, -8% at +40 C.
2. **HVAC.** The cabin needs heating when cold and air-conditioning when hot, so the accessory load on
   the DC bus is a V-shape around a ~21 C comfort point - up to ~1.9 kW heating at -10 C and ~1.7 kW
   cooling at +40 C.
3. **Battery thermal.** The pack starts and sits at ambient, so a hot day pushes the cell temperature
   toward the battery-management derate threshold while a cold day keeps it clear.

The study is **read-only and opt-in**: each run is a charge-sustaining *copy* of the validated body
config, so no validated number can regress. Sweeping the AWD SUV on the mixed cycle:

```
=== AWD SUV | Mixed (urban+highway+sprint): ambient sweep ===
  Temp   Fuel        CO2     HVAC   Air    Pack pk  Derate
  -10C   6.72 L  155.2  1.86kW  1.10x   -8.4C    no
  +20C   5.86 L  135.3  0.06kW  0.98x   21.8C    no
  +40C   6.19 L  142.9  1.71kW  0.92x   41.9C    no
```

```
=== Fleet ambient sensitivity (-10C to +40C) ===
  Body          Cold L  Ref L   Hot L   Swing  Derate
  AWD SUV        6.72    6.19    6.19   13.9%  no
  Sedan          4.23    3.65    3.77   22.4%  no
  Pickup         9.20    8.35    8.18   15.2%  no
```

Two findings:

1. **Fuel is a U-shape with its floor at the comfort point, and cold is the worse end.** The minimum
   sits near +20 C; both colder and hotter cost fuel, but cold costs more because denser air **and**
   cabin heating stack on top of each other, whereas a hot day's thinner air partly offsets the
   air-conditioning. The cold-to-hot swing is **14-23%** of the reference figure depending on body -
   a real, quotable seasonal penalty that the single-temperature numbers hide. (The lighter, more
   efficient bodies show the *largest percentage* swing because a fixed ~1.9 kW HVAC load is a bigger
   slice of their smaller baseline demand.)
2. **The pack never thermally derates - not even on a +40 C day.** The cell peaks at ~42 C under
   realistic duty, comfortably below the 45 C battery-management threshold. This is the *same*
   conclusion Move F reached from the sizing side, now confirmed thermally: the 20 kWh pack is
   generously specified and runs cool, so hot-weather power fade is a non-issue in normal driving.

This is locked by `AmbientStressTest` and `verify.py` (cold raises fuel on every body; the pack never
derates from -10 C to +40 C), and reproduced in `main.py`. Pure stdlib.

---

## Move I - Standardized regulatory cycles: WLTP and EPA

The cycles used so far are representative but home-grown. Regulators measure every production car on a
small set of **named** procedures, so this Move adds them: **WLTP** (the European/global type-approval
cycle, four rising-speed phases), **EPA UDDS** (the US city cycle, lots of stops) and **EPA HWFET**
(the US steady-highway cycle).

**Honesty note.** These are *envelope-matched reconstructions*, not the official second-by-second
traces (which are copyrighted lookup tables). Each is built from trapezoidal accelerate/cruise/idle
micro-trips tuned so the macroscopic statistics that actually drive energy use - distance, duration,
average and peak speed, stop count - land within a few percent of the published figures:

```
=== Regulatory cycle reconstruction fidelity (built vs published) ===
  Cycle                    Dist km     Avg km/h    Max km/h
  WLTP Class 3            24.5/23.3   46.9/46.5  131.0/131.3
  EPA UDDS (city)         12.2/12.1   32.5/31.5   91.0/91.2
  EPA HWFET (highway)     16.9/16.4   78.3/77.7   96.0/96.4
```

Running the fleet charge-sustaining (SoC-corrected equivalent fuel) on the reconstructions:

```
=== Fleet fuel economy on regulatory cycles (L/100km) ===
  Body               HWFET      UDDS      WLTP
  AWD SUV           4.73      5.86      6.22
  Sedan             2.94      3.85      3.96
  Hatchback         2.77      3.41      3.64
  Pickup            6.30      7.26      8.06
```

The findings are reassuringly boring, which is the point:

1. **The reconstructions match the published envelope within ~5%** on distance and average speed, and
   to the nearest km/h on peak speed - faithful enough to report comparable economy without
   fabricating the exact trace.
2. **The home-grown cycles were representative all along.** The standardized numbers sit in the same
   band and the same body-ordering as the synthetic ones (the AWD SUV's WLTP ~6.2 L/100km is the
   expected step up from its steady synthetic-highway 4.62, because WLTP folds demanding urban and
   extra-high-speed phases into one trip). No earlier conclusion shifts.
3. **Every body completes every regulatory cycle with zero capability shortfalls** - the powertrain
   is comfortable across the full type-approval envelope, not just the synthetic duty it was tuned on.

Locked by `RegulatoryCycleTest` and `verify.py` (reconstructions match the published envelope; the
fleet completes every cycle without shortfalls), reproduced in `main.py`. Pure stdlib.

---
