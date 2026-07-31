# Digital Twin — Design & Architecture

A **digital twin** of the integrated ATPE + PCMRITMS powertrain: a physics-based, time-stepped
simulation that takes a drive cycle (speed + road grade vs. time) and reproduces the behavior of
the real powertrain — power flows, tier activation, buffer/battery states, fuel use, efficiency,
and emissions.

It is implemented in **pure Python (standard library only)** for the core vehicle twin
(`digital_twin/`), so Phase-1 headlines run with no installation beyond Python ≥ 3.12.
The optional Phoenix V3 / ATPE Brain path (`designs/phoenix_v3/`, `atpe_brain/`) uses
`numpy` (see `designs/requirements-design.txt`). Optional Cantera supports Gate 1 chemistry.

## Goals

- **Faithful, not flashy:** enforce conservation of energy/power every timestep (see [07-core-concept-refinements.md](07-core-concept-refinements.md) §6).
- **Parameterized:** every subsystem is a dataclass config so Phase-1 (MUV/SUV) and Phase-2 (performance) are just different numbers.
- **Inspectable:** every step emits a full telemetry record; metrics are derived, never hand-waved.
- **Deterministic & testable:** no randomness; same cycle ⇒ same result.

## Package Layout

```
digital_twin/                  # Core twin — pure stdlib (Phase-1 4/2/2 vehicle stack)
├── __init__.py                Public API (build_default_twin, build_body_twins, run, ...)
├── config.py                  Dataclasses + body variants
├── vehicle.py                 Longitudinal road-load → DC-bus demand
├── atpe.py                    Three-tier free-piston generator + energy-weighted tier fill
├── atpe_ring.py               Dynamic ring ATPE (V3 bridge path)
├── phoenix_v3_bridge.py       Calibration bridge to designs/phoenix_v3
├── pcmritms.py / pcmritms_rotor.py / pcmritms_coupling.py
├── battery.py / controller.py / powertrain.py
├── drive_cycles.py / regulatory_cycles.py / ambient.py
├── acceptance.py / fleet.py / simulation.py
├── economics.py / sizing.py / montecarlo.py / summary.py
├── coldstart.py / payload.py / phev.py / degradation.py
├── ice_benchmark.py / graceful_degradation.py
├── single_cylinder.py / gate1_matrix.py / gate4_scaling.py
└── validation.py / misfire.py

atpe_brain/                    # Gate 6 supervisory brain (Layer 3)
ecu/ + firmware/c/             # Layer-2 vehicle ECU (Python ref + C skeleton)
designs/phoenix_v3/            # V3 ring plant (Gate 5 freeze: 4/6/2 X12)
```

> Regression invariants (rotor reproduction, per-body acceptance, fleet numbers, and the
> rotor-coupling no-regression rule) are locked by
> [tests/test_invariants.py](../tests/test_invariants.py) — run with
> `python -m unittest discover -s tests -v`.
> Headline lock: `verify.py` → **63 checks**; full suite **169 tests** (V3/brain need `numpy`).

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

**ATPE (atpe.py).** The smallest tier set that covers $P_{gen}^*$ is activated; electrical output is
**filled from Tier 1 upward**, and fuel power is the sum of each tier's share divided by that tier's
fuel→electrical efficiency (energy-weighted blend). `active_tier` / `active_index` still report the
governing (largest active) tier for controller telemetry. Gate 1 mode applies per-tier load fractions.
Fuel mass from gasoline LHV (43.4 MJ/kg, 0.745 kg/L); CO₂ at 2.31 kg/L.

**Buffer (pcmritms.py).** Bounded reservoir; charge applies $\sqrt{\eta_{rt}}$, discharge divides
by $\sqrt{\eta_{rt}}$; power and energy both clamped.

**Battery (battery.py).** SoC integrates net energy / capacity; discharge/charge power clamped;
SoC bounded [0, 1].

**Gate 1 virtual bench (`single_cylinder.py`, `gate1_matrix.py`).** Optional per-cartridge
combustion and free-piston surrogates feed `gate1_bench_at_load()` and the **48-cell** speed × load
× tier matrix (`run_gate1_matrix()`). Bench peak power uses a load-bank rating surrogate (tier
nameplate × load × efficiency); vehicle fuel on the default path still uses fixed tier-efficiency
tables unless `build_gate1_twin()` / `with_gate1()` is enabled. Export CSV:

```powershell
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
```

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

The Phase-1 powertrain (ATPE stack + inertial buffer + battery) is held fixed while only the
**body** changes. Heavy bodies (AWD SUV, Pickup) use a **160 kW** traction motor; light/mid
bodies use **150 kW** — both clear class-appropriate ERS targets **9/9**. `BodyStyle` in
[config.py](../digital_twin/config.py) captures chassis-dependent parameters; `phase1_variants()`
returns one `TwinConfig` per body. The **AWD SUV is the primary reference**.

`compare_bodies()` ([acceptance.py](../digital_twin/acceptance.py)) prints a side-by-side table.
Details and rationale live in [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

## ERS Acceptance Checks

[acceptance.py](../digital_twin/acceptance.py) encodes the Project PHOENIX P1 targets as objective,
simulation-backed pass/fail checks. **All six bodies pass 9/9** against class-appropriate targets
with the 150/160 kW motor family. `recommend_motor()` / `sweep_motor()` quantify that nothing in
Phase-1 needs more than 160 kW. See [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

## Validation Strategy

- **Sanity bounds:** efficiency between 0–48%, SoC within limits, no negative fuel — asserted in code.
- **Energy closure:** sum of source energy ≈ demand energy + losses (residual reported).
- **Cross-check vs. docs:** steady highway efficiency should land in the 43–47% band predicted in [03-integration-viability.md](03-integration-viability.md); towing economy in the 10–13 L/100km band from [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md).
- **Integrity seal:** `verify.py` re-derives locked headlines (63 checks) and runs the unit suite.

## Roadmap (twin fidelity tiers)

1. **v0** — quasi-steady power-flow twin, rule-based control. ✅
2. **v1** — ambient / cold-start / payload / PHEV / ageing Moves (H–M). ✅
3. **v2** — Gate 1 virtual bench + Gate 4 ring scaling; opt-in combustion path. ✅
4. **v3** — Phoenix V3 mixed ring + ATPE Brain supervisory path + Layer-2 ECU. ✅ (software)
5. **Next** — calibrate surrogates from measured Gate 1 rig CSV; physical HIL for Brain/ECU.

Parallel hardware plan: [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) §6–7.
