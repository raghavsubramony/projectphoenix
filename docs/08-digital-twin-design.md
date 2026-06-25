# Digital Twin — Design & Architecture

A **digital twin** of the integrated ATPE + PCMRITMS powertrain: a physics-based, time-stepped
simulation that takes a drive cycle (speed + road grade vs. time) and reproduces the behavior of
the real powertrain — power flows, tier activation, buffer/battery states, fuel use, efficiency,
and emissions.

It is implemented in **pure Python (standard library only)** so it runs with no installation
beyond Python ≥ 3.12, matching the project's zero-dependency `pyproject.toml`.

## Goals

- **Faithful, not flashy:** enforce conservation of energy/power every timestep (see [07-core-concept-refinements.md](07-core-concept-refinements.md) §6).
- **Parameterized:** every subsystem is a dataclass config so Phase-1 (MUV/SUV) and Phase-2 (performance) are just different numbers.
- **Inspectable:** every step emits a full telemetry record; metrics are derived, never hand-waved.
- **Deterministic & testable:** no randomness; same cycle ⇒ same result.

## Package Layout

```
digital_twin/
├── __init__.py        Public API (build_default_twin, build_body_twins, run, ...)
├── config.py          Dataclasses: TierSpec, ATPEConfig, BufferConfig, BatteryConfig,
│                       VehicleConfig, TractionConfig, ControlConfig, TwinConfig,
│                       BodyStyle (+ phase1_variants / phase2 builders)
├── vehicle.py         Longitudinal road-load model → DC-bus power demand
├── atpe.py            Three-tier free-piston generator + tier selection
├── pcmritms.py        Inertial torque buffer (bounded kinetic reservoir)
├── pcmritms_rotor.py  Whitepaper Appendix-A multi-ring torque-modulation model
├── pcmritms_coupling.py  Derives the buffer's brief-burst rating from the rotor model
├── battery.py         LFP pack with SoC + power limits
├── controller.py      Unified 3-loop control law (mode, gen setpoint, arbitration)
├── powertrain.py      Orchestrator: one step = vehicle → controller → sources
├── drive_cycles.py    Synthetic urban / highway / towing / mixed cycles
├── acceptance.py      ERS pass/fail checks + multi-body capability comparison
├── fleet.py           Fleet harness: every body × every cycle, A/B delta tables
└── simulation.py      Runner + metrics aggregation + report
```

> Regression invariants (rotor reproduction, per-body acceptance, fleet numbers, and the
> rotor-coupling no-regression rule) are locked by
> [tests/test_invariants.py](../tests/test_invariants.py) — run with
> `python -m unittest discover -s tests -v`.

## Data Flow (one timestep `dt`)

```
drive cycle (v, grade, dv/dt)
        │
        ▼
 vehicle.power_demand()  ──►  P_d  (DC-bus watts, +traction / −regen)
        │
        ▼
 controller.decide(P_d, states)
        ├─ Loop A: mode = EV | CS
        ├─ Loop B: P_gen* → atpe.select_tiers() → P_gen, fuel
        └─ Loop C: Δ = P_d − P_gen
               1) buffer.exchange(Δ_fast)   (within E,P limits)
               2) battery.exchange(Δ_rest)  (within SoC,P limits)
               3) residual → shortfall flag
        │
        ▼
 integrate states (buffer E, battery SoC, fuel, distance, CO2)
        │
        ▼
 StepRecord telemetry  ──►  collected into Result → metrics
```

## Physics Summary

**Road load (vehicle.py).** Tractive force
$F = m a + m g \sin\theta + C_{rr} m g \cos\theta + \tfrac12 \rho C_d A v^2$, wheel power
$P_{wheel} = F v$. Converted to DC-bus electrical demand via driveline + motor efficiency when
motoring, and via regen efficiency when braking.

**ATPE (atpe.py).** Tier set chosen to cover $P_{gen}^*$; fuel power $= P_{gen}/\eta_{th}$ of the
governing tier; fuel mass from gasoline LHV (43.4 MJ/kg, 0.745 kg/L); CO₂ at 2.31 kg/L.

**Buffer (pcmritms.py).** Bounded reservoir; charge applies $\sqrt{\eta_{rt}}$, discharge divides
by $\sqrt{\eta_{rt}}$; power and energy both clamped.

**Battery (battery.py).** SoC integrates net energy / capacity; discharge/charge power clamped;
SoC bounded [0, 1].

## Metrics Produced

- Distance (km), duration (s), mean/peak speed
- Fuel used (L), fuel economy (L/100 km), CO₂ (g/km)
- Mean & by-phase powertrain efficiency
- Time-share per mode (EV / Tier 1 / 2 / 3)
- Battery SoC start/end & net electrical energy used
- Buffer utilization (cycles, peak power, energy throughput)
- Capability shortfall events (count, max unmet kW)

## How to Run

```powershell
python main.py                       # runs the bundled demo cycles + report
```

Or programmatically:

```python
from digital_twin import build_default_twin, DriveCycles, run

twin = build_default_twin()                  # Phase-1 AWD SUV (primary reference)
cycle = DriveCycles.mixed(duration_s=1800)   # 30-min synthetic mixed cycle
result = run(twin, cycle)
print(result.report())
```

Compare the same powertrain across body styles, or run the ERS checks:

```python
from digital_twin import build_body_twins, build_default_twin, phase1_targets
from digital_twin.acceptance import compare_bodies, report

print(compare_bodies(build_body_twins(), phase1_targets()))   # body sweep
print(report(build_default_twin, phase1_targets()))            # ERS verdict
```

## Vehicle-Body Variants (same powertrain, different chassis)

The Phase-1 powertrain (ATPE stack + inertial buffer + battery + 150 kW traction motor) is held
fixed while only the **body** changes. `BodyStyle` in [config.py](../digital_twin/config.py)
captures the chassis-dependent parameters (mass, $C_d$, frontal area, $C_{rr}$, wheel radius,
driveline efficiency, aux load); `phase1_variants()` returns one `TwinConfig` per body, and
`build_body_twins()` exposes fresh builders for the capability sims. The **AWD SUV is the primary
reference**; the rest exist for comparison.

`compare_bodies()` ([acceptance.py](../digital_twin/acceptance.py)) prints a side-by-side table.
Representative output (same powertrain throughout):

| Body | Mass (kg) | Peak (kW) | 0–100 (s) | Top (km/h) | Grade @100 (%) |
|------|----------:|----------:|----------:|-----------:|---------------:|
| **AWD SUV** (primary) | 2200 | 440 | 8.2 | 192.6 | 20.5 |
| Sedan | 1650 | 440 | 5.8 | 228.6 | 30.0 |
| Hatchback | 1400 | 440 | 4.8 | 226.8 | 36.0 |
| Crossover | 1850 | 440 | 6.7 | 208.8 | 25.5 |
| Pickup | 2500 | 440 | 9.9 | 169.2 | 16.5 |
| Van / MPV | 2300 | 440 | 8.6 | 183.6 | 19.5 |

The spread is pure body physics — lighter, slipperier bodies accelerate and climb harder on the
identical engine, while the heavy, high-drag pickup/van trail the SUV. This is the same lever the
ERS 0–100 discussion turns on (see [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9).

## ERS Acceptance Checks

[acceptance.py](../digital_twin/acceptance.py) encodes the Project PHOENIX P1 targets as objective,
simulation-backed pass/fail checks (peak/continuous power, motor torque, 0–100, top speed,
gradeability, brake-thermal/generator/fuel-to-wheel efficiency). `report(build_twin, targets)`
prints the verdict; the demo runs it for the SUV (currently **8/9**, with 0–100 the single honest
FAIL for the heavy SUV body). Details and rationale live in
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

**Per-body acceptance.** Each body is also judged against **class-appropriate** targets via
`phase1_targets_for(body)` / `phase1_body_targets()`, evaluated by `report_bodies()`. The
powertrain-level targets are identical (same engine); only the 0–100 / top-speed / gradeability
targets reflect each vehicle class. With the shared 150 kW powertrain the lighter bodies pass 9/9,
while the SUV (acceleration) and pickup (gradeability) land at 8/9 — an honest signal that those two
bodies want a larger motor or the Phase-2 stack.

**Recommended motor sizing.** `recommend_motor()` / `recommend_motors()` bisect the minimum motor
power that clears a body's targets (peak power for 0–100 + gradeability; continuous for top speed,
capped by engine output). The SUV and pickup need only **+10 kW (→160 kW)** to pass, while the
lighter bodies show 25–65 kW of headroom — quantifying the spec/config tension exactly. See
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

**Motor-size sweep study.** `sweep_grid()` / `sweep_motor()` evaluate every body across **60–250 kW
in 10 kW steps** and print a pass/fail grid plus the minimum passing motor (and the binding target)
per body. The comparative result: a two-motor family covers the lineup — ~150 kW for the light/mid
bodies, 160 kW for the heavy SUV/pickup; nothing in Phase-1 needs more than 160 kW. See
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

## Validation Strategy

- **Sanity bounds:** efficiency between 0–48%, SoC within limits, no negative fuel — asserted in code.
- **Energy closure:** sum of source energy ≈ demand energy + losses (residual reported).
- **Cross-check vs. docs:** steady highway efficiency should land in the 43–47% band predicted in [03-integration-viability.md](03-integration-viability.md); towing economy in the 10–13 L/100km band from [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md).

## Roadmap (twin fidelity tiers)

1. **v0 (this):** quasi-steady power-flow twin, rule-based control. ✅
2. **v1:** add thermal states (catalyst light-off, Tier-1 HCCI enable gate, cold-start window).
3. **v2:** per-cylinder combustion phasing & NVH signature; buffer phase-coordination dynamics.
4. **v3:** swap rule-based controller for a learned predictive coordinator; co-sim with a real WLTP/EPA cycle file.
