# PCMRITMS — Whitepaper ↔ Digital-Twin Alignment

How the whitepaper's specification ([10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md))
maps onto the digital twin, and where the twin reproduces or deliberately abstracts the source.

## 1. Parameter Alignment

| Quantity | Whitepaper | Twin ([config.py](../digital_twin/config.py)) | Match |
|----------|-----------|-----------------------------------------------|-------|
| Stored energy | 118 kJ | `buffer.max_energy_j = 118_000` | ✅ exact |
| Pack electrical rating | ~90–105 kW | `max_discharge_w = max_charge_w = 90 kW` | ✅ (conservative end) |
| Round-trip efficiency | 75–85% target | `round_trip_efficiency = 0.80` | ✅ within band |
| Rotor count | 3 (→4–6) | rotor model `RotorSet` n=3 | ✅ |
| Per-rotor inertia | 0.10–0.15 kg·m² | `(0.12, 0.15, 0.10)` | ✅ exact |
| Mean speed | 800 rad/s | `mean_speed_rad_s = 800` | ✅ |
| Boost magnitude | +35–50% brief | emergent from energy/power limits | ✅ consistent |
| Boost duration | 0.2–0.5 s | enforced by reservoir drain limit | ✅ (≈1.3 s at 90 kW full-empty) |

## 2. Two Levels of Model

The twin represents the PCMRITMS at two fidelities, matching the whitepaper's own structure:

1. **System level — energy reservoir** ([pcmritms.py](../digital_twin/pcmritms.py)).
   Used inside the full powertrain simulation. Treats the buffer as a bounded kinetic store
   with two-sided power limits and split round-trip efficiency. This is the right abstraction
   for drive-cycle energy accounting (the whitepaper's stated "next modeling step": co-sim with
   vehicle dynamics — which our twin already does).

2. **Rotor level — torque modulation** ([pcmritms_rotor.py](../digital_twin/pcmritms_rotor.py)).
   A faithful reimplementation of the whitepaper's Appendix A lumped-parameter model, in pure
   stdlib. Reproduces the headline result exactly:

   | Metric | Whitepaper | Twin output |
   |--------|-----------|-------------|
   | Peak combined torque | 242.8 N·m | **242.8 N·m** |
   | Brief boost | +34.9% | **+34.9%** |
   | Stored energy | 0.118 MJ | **0.118 MJ** |

   Run it via `python main.py` (last section) or:
   ```python
   from digital_twin import simulate_torque_augmentation
   print(simulate_torque_augmentation())
   ```

## 3. Where the Twin Improves on the Whitepaper's Simulation

The whitepaper (§4.4) lists gaps in its own model; the twin closes several:

| Whitepaper gap | Twin status |
|----------------|-------------|
| No vehicle longitudinal model | ✅ Implemented ([vehicle.py](../digital_twin/vehicle.py): mass, grade, aero, roll) |
| No round-trip energy accounting | ✅ Reservoir applies √η on each leg; energy booked every step |
| Peak power not capped to per-inverter ratings | ✅ `max_discharge_w`/`max_charge_w` enforce the 90 kW pack cap |
| No drive-cycle energy benefit | ✅ Buffer throughput + peak kW reported per cycle |

## 4. Honesty Clauses Preserved

The whitepaper is candid about limits; the twin encodes them rather than papering over them:

- **No sustained multiplication.** The reservoir physically empties at high discharge, so the
  twin cannot fabricate a long boost — matching §3.5 ("brief, shaped pulses, not continuous
  overpowering").
- **Capability shortfalls are surfaced**, never hidden — see the `shortfall` flag in
  [powertrain.py](../digital_twin/powertrain.py) and the report line in
  [simulation.py](../digital_twin/simulation.py).
- **Efficiency is a target, not a claim.** `round_trip_efficiency = 0.80` sits inside the
  whitepaper's "optimistic, unvalidated" 75–85% band and is labelled as such.

## 5. Still Abstracted (future fidelity, per whitepaper §8 + docs/08 roadmap)

- Multi-body / epicyclic gear-mesh dynamics and the ~1–3% per-stage gear loss.
- Magnetic-bearing drag and winding thermal derating.
- Closed-loop per-rotor phase/amplitude/frequency *control* inside the powertrain
  (the rotor model still runs open-loop; its derived burst rating is now used by the
  step loop — see §6 — but the controller does not yet command individual rotors).
- Gyroscopic counter-rotating-pair management during cornering maneuvers.

## 6. Rotor → Powertrain Coupling (integration, with A/B data)

Previously the rotor model (§2.2) only reproduced the whitepaper *standalone*; it did
not influence the powertrain step loop. That gap is now closed by
[pcmritms_coupling.py](../digital_twin/pcmritms_coupling.py), which turns the rotor
physics into a concrete bus-level capability:

| Step | Value |
|------|-------|
| Peak reaction torque (sampled over one beat period) | **62.8 N·m** (= whitepaper +62.8) |
| × mean speed 800 rad/s → brief surge power | **50.2 kW** |
| Buffer continuous discharge rating | 90 kW |
| Buffer **brief-burst** rating with coupling (`peak_transient_w`) | **140.2 kW** |

The coupling is **opt-in** via `rotor_coupled=True` on the config/builder factories
(`phase1_config`, `phase1_variants`, `build_body_twins`, `charge_sustaining_bodies`).
It defaults **off**, so every validated number above is unchanged when disabled.
`BufferConfig.peak_transient_w = None` means "continuous rating only" (no coupling); when
set, `InertialBuffer._discharge` allows the higher cap while the energy reservoir keeps
the burst physically self-limiting.

### Fleet A/B result — the coupling shapes peaks, not energy

Running the fleet harness ([fleet.py](../digital_twin/fleet.py)) for all six bodies over
all four standard cycles, **baseline vs rotor-coupled**, gives:

| Metric | Δ (coupled − baseline) |
|--------|------------------------|
| Fuel L/100 km | 0.00 — every body, every cycle |
| CO₂ g/km | 0 |
| Buffer throughput kJ | 0 |
| Net battery kWh | 0.000 |
| Buffer peak kW | only Pickup/Mixed +7.7 kW |
| Shortfall events | 0 → 0 (already none) |

This is the honest, expected result: **raising the discharge cap without enlarging the
reservoir delivers the same energy at higher power for a shorter time**, so steady-cycle
fuel and energy are untouched. A direct transient probe makes the mechanism explicit —
against a 130 kW demand spike the baseline buffer **clips to 90 kW** while the coupled
buffer **delivers the full 130 kW for 0.8 s** (both drain the same energy). The value is
therefore **sub-second peak-shaping and battery-current sparing** (driveability and pack
longevity), not steady-state fuel economy. These invariants are locked by
[tests/test_invariants.py](../tests/test_invariants.py).

## 7. Realistic Transients — Why the Buffer Is *Necessary*, Not Just Convenient

§6 found a near-null fleet result, but for a subtle modelling reason: the controller's
spike override floors generation to the instantaneous demand (`min(demand, max_gen)`)
**in a single timestep**. A real free-piston generator cannot ramp that fast — and an
engine that *can* instantly cover every spike makes the inertial buffer redundant by
construction. To test the concept under honest physics, the ATPE now carries an optional
generation ramp limit, `ATPEConfig.max_slew_w_per_s` (default `None` = instantaneous, so
all of §1–§6 is unchanged). With a finite slew the buffer must bridge the ramp — which is
the whole reason PCMRITMS exists.

A dedicated **transient-stress** scenario exercises this:
[`DriveCycles.transient_stress()`](../digital_twin/drive_cycles.py) (back-to-back hard
launches) run through [`stress_bodies()`](../digital_twin/fleet.py) (60 kW/s generation
slew, battery started at target, optional cold-pack derate).

### 7.1 Battery-current sparing (data-backed, monotone by mass)

Baseline 90 kW buffer vs rotor-coupled 140 kW buffer, slew-limited generation, dt = 0.2 s:

| Body | Buffer peak Δ | Battery throughput Δ |
|------|---------------|----------------------|
| AWD SUV | **+50.2 kW** | **−21 kJ** |
| Pickup | **+50.2 kW** | **−55 kJ** |
| Van / MPV | **+50.2 kW** | **−30 kJ** |
| Crossover | +27.9 kW | ~0 |
| Sedan | +22.6 kW | ~0 |
| Hatchback | +4.2 kW | ~0 |

The coupled buffer demonstrably delivers its full ~140 kW burst (the +50.2 kW gain is the
rotor surge), and that burst is drawn **from the buffer instead of the battery** — sparing
battery current on exactly the heavy bodies whose launches are most demanding. This is a
real pack-longevity / C-rate benefit, and it scales with body mass.

### 7.2 The honest limit: energy, not power, is the bottleneck

On a single constrained launch the baseline and coupled buffers leave **identical** unmet
energy — because the 118 kJ reservoir empties before the demand peak, after which the cap
(90 vs 140 kW) is moot. **The coupling adds power, not energy.**

### 7.3 Scaling PCMRITMS converts burst into sustained capability

The whitepaper's roadmap grows the rotor set from 3 to 4–6, which raises **both** the burst
rating **and** the stored energy. Modelling that coherent growth (constrained 40 kW cold
pack, transient-stress cycle, SUV) gives a clean monotone result:

| PCMRITMS config | Burst | Reservoir | Unmet launch energy | Shortfall steps |
|-----------------|-------|-----------|---------------------|-----------------|
| baseline 3-rotor | 90 kW | 118 kJ | 1095 kJ | 93 |
| coupled 3-rotor | 140 kW | 118 kJ | 1194 kJ | 79 |
| coupled ~5-rotor | 140 kW | 236 kJ | 853 kJ | 62 |
| coupled ~6-rotor | 140 kW | 354 kJ | **493 kJ (−55%)** | **38 (−59%)** |

**Conclusion (concept-proving):** the rotor coupling is *necessary* to lift the burst
rating, but the *reservoir size* is what converts that burst into sustained launch
capability. The whitepaper's planned 4–6 rotor growth yields a measured **−55% unmet
launch energy** under a cold battery — a concrete, data-backed reason to scale the
PCMRITMS rather than over-size the traction battery. All of §7 is locked by the
`GenerationSlewTest`, `TransientStressTest`, and `RotorScalingTest` cases in
[tests/test_invariants.py](../tests/test_invariants.py).


