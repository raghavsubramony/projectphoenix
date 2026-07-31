# Project Phoenix — Integrated Adaptive Powertrain
## System Whitepaper v1.0

**Project Phoenix**  
**Document status:** Phase 1 simulation complete — no hardware gate cleared  
**Publication date:** July 2026  
**Classification:** External distribution permitted with simulation disclaimer

---

> **Disclaimer:** This document reports **simulation-validated** engineering projections.
> No road-test, EPA/WLTP certification, or homologation claims are made. Prefix all
> external statements with “simulation shows…” Measured bench prototypes are the Seed-phase
> deliverable.

---

## 1. Executive Summary

Project Phoenix is an **integrated series-hybrid powertrain** combining:

1. **ATPE** — crankless three-tier free-piston linear generator  
2. **PCMRITMS** — phase-coordinated multi-rotor inertial torque buffer  
3. **20 kWh LFP battery** — low-frequency energy store and urban EV range  
4. **Unified controller** — nested slow/fast loops that keep the engine off transients  

Physics-based computer simulation stress-tested this architecture across **thirteen
validation studies**, six vehicle bodies, temperatures from −10 °C to +40 °C,
regulatory-style drive cycles, full payload, cold starts, plug-in charging, 250,000 km
ageing, comparison against a conventional 2.0 L turbo, and single-cartridge fault tolerance.

**Core conclusion (simulation):** a small, cool-running, lifetime-lasting battery plus an
inertial sprint reserve plus an efficient generator can deliver strong performance with
competitive lifetime cost and carbon — **if hardware matches the model assumptions**.

**What has not been done:** building and measuring real cartridges, rotors, or a mule vehicle.

---

## 2. System Architecture

### 2.1 Energy flow

```
  PETROL
    │
    ▼
  ATPE (3-tier free-piston ring)     ← slow loop: 10–100 ms
    │
    ▼
  DC bus (400–800 V)
    │
    ├── PCMRITMS (inertial buffer)  ← fast loop: 1–5 ms
    ├── LFP battery (20 kWh)
    │
    ▼
  Traction motor(s) → Wheels
```

### 2.2 Complementary roles

| ATPE weakness | PCMRITMS remedy |
|---------------|-----------------|
| Tier switching gap (~200–400 ms) | Buffer covers seamlessly |
| Irregular free-piston ripple | Rotor smoothing |
| Slow generation setpoint | Millisecond inertial response |

| PCMRITMS weakness | ATPE remedy |
|-------------------|-------------|
| 118 kJ depletes in ~1.3 s at full discharge | Engine refills during sustained load |
| Needs primary energy source | Efficient continuous generation |
| Round-trip losses | ATPE efficiency advantage offsets |

### 2.3 Reference sizing — AWD SUV (Phase 1)

| Subsystem | Parameter | Value |
|-----------|-----------|------:|
| ATPE stack | Peak electrical | 230 kW (8 cartridges, 4/2/2 mix) |
| ATPE stack | Continuous | ~100 kW |
| PCMRITMS | Stored energy | 118 kJ |
| PCMRITMS | Continuous discharge | 90 kW |
| PCMRITMS | Brief burst | **140 kW** (~1.3 s energy-limited) |
| PCMRITMS | Peak torque boost | **+34.9%** (242.8 vs 180 N·m) |
| Battery | Capacity | 20 kWh LFP |
| Battery | Max discharge (default) | 120 kW |
| Battery | Right-sized discharge (SUV) | **90 kW** |
| Battery | Max charge | 80 kW |
| Battery | Charge-sustaining SoC | 55% |
| Traction | Peak power | 222 kW |
| Traction | Peak torque | ≥350 N·m |
| Vehicle | 0–100 km/h target | <8 s |
| Vehicle | Top speed | 180 km/h |

---

## 3. Unified Control

Three nested loops with strict authority:

**Loop A — Mode (100–500 ms):** electric-only vs charge-sustaining from filtered demand
and SoC floor (25%).

**Loop B — ATPE setpoint (10–100 ms):** five-second low-pass filtered load plus SoC
correction → smallest sufficient tier set.

**Loop C — Buffer arbitration (1–5 ms):**

1. PCMRITMS supplies or absorbs mismatch first.  
2. Battery covers remainder.  
3. Any residual is recorded as capability shortfall — energy is never silently invented.

Surplus generation refills PCMRITMS first, then charges the battery.

**Power balance (every timestep):** generator + battery + buffer = demand + losses.

---

## 4. Validation Studies — Results (simulation)

| Study | Question | Headline result |
|-------|----------|-----------------|
| A — Learned controller | Can control learn from data? | Matches rule-based with fair charge-sustaining correction |
| B — Battery thermal | Heat and ageing? | +1 °C in normal driving; **0 replacements** over vehicle life |
| C — Sensitivity | Which inputs matter? | Aerodynamics > weight at highway; no single magic factor |
| D — Economics | True cost and CO₂? | Battery embodied CO₂ = **4.7%** of SUV lifecycle |
| E — Smart flywheel timing | Cleverer sprint use? | **No benefit** — energy-limited, not power-limited |
| F — Right-sizing | Oversized parts? | SUV battery can drop 120 → 90 kW discharge |
| G — Uncertainty | Error bars? | 5th–95th percentile bands on all headlines |
| H — Climate | −10 °C to +40 °C? | 12–23% seasonal fuel swing; no thermal derate |
| I — Regulatory cycles | WLTP/EPA reconstructions? | Rankings match custom cycles |
| J — Cold start | Cold morning? | Hurts long trips only; urban stays electric |
| K — Payload | Full load? | More fuel; hill climb still passes |
| L — Plug-in | Grid charging? | CO₂ win only on clean grid |
| M — Ageing | 250,000 km? | ~23% EV range loss; modest fuel creep |
| N — ICE benchmark | vs 2.0 L turbo? | **28% less fuel** (AWD SUV, mixed) |

---

## 5. Key Performance Data (simulation)

### 5.1 AWD SUV headlines

| Metric | Value |
|--------|------:|
| Highway fuel (charge-sustaining) | **4.46 L/100 km** |
| Mixed-cycle fuel | **2.35 L/100 km** |
| vs 2.0 L turbo (mixed) | **−28%** fuel |
| Mixed-cycle CO₂ | **54 g/km** (ICE: 75 g/km) |
| Lifecycle CO₂ (cradle-to-grave) | **102 g/km** |
| Running cost | **€0.072/km** [€0.066–0.091] |
| Battery replacements (250k km) | **0** |
| WLTP-class fuel | 4.61 L/100 km (ICE: 7.30) |
| Seasonal fuel swing | 11.6% |
| Cold-start fuel penalty | +19.3% |
| Full-payload fuel penalty | +19.2% |
| PHEV CO₂ (clean grid) | 12 g/km |
| Fuel drift after 250k km | +44.4% |
| EV range loss after 250k km | −23.3% |

### 5.2 Fleet — mixed cycle (simulation)

| Body | Fuel (L/100 km) | Cost (€/km) | CO₂ (g/km) | WLTP (L/100 km) | Season swing |
|------|----------------:|------------:|-----------:|----------------:|-------------:|
| Hatchback | 0.55 | 0.039 | 44 | 3.60 | 22.7% |
| Sedan | 0.64 | 0.040 | 47 | 3.91 | 22.3% |
| Crossover | 1.11 | 0.048 | 60 | 4.87 | 20.2% |
| AWD SUV | 2.61 | 0.072 | 102 | 6.07 | 11.6% |
| Van / MPV | 2.95 | 0.077 | 112 | 6.59 | 16.6% |
| Pickup | 3.86 | 0.092 | 137 | 7.83 | 14.8% |

### 5.3 ATPE vs conventional 2.0 L turbo — full comparison (L/100 km)

| Body | Urban | Highway | Tow+grade | Mixed | WLTP-class |
|------|------:|--------:|----------:|------:|-----------:|
| AWD SUV | 0.00 / 0.00 | 4.46 / 7.67 | 6.91 / 11.92 | 2.35 / 3.26 | 4.61 / 7.30 |
| Sedan | 0.00 / 0.00 | 0.83 / 1.42 | 4.64 / 9.21 | 0.40 / 0.64 | 1.64 / 2.70 |
| Hatchback | 0.00 / 0.00 | 0.69 / 1.19 | 4.09 / 8.47 | 0.34 / 0.56 | 1.18 / 1.92 |
| Crossover | 0.00 / 0.00 | 1.50 / 2.43 | 5.58 / 10.37 | 1.01 / 1.46 | 3.03 / 4.91 |
| Pickup | 0.00 / 0.00 | 6.17 / 9.68 | 8.58 / 13.62 | 4.23 / 7.01 | 6.81 / 10.75 |
| Van / MPV | 0.00 / 0.00 | 5.01 / 8.29 | 7.39 / 12.42 | 2.77 / 5.12 | 5.38 / 8.55 |

*Format: ATPE / conventional ICE. Urban figures are near-zero when battery is charged.*

### 5.4 Fault tolerance

**18 of 18 scenarios PASS** — one cartridge offline in any tier, all six bodies pass 9/9
performance targets (acceleration, top speed, 20% gradeability, efficiency).

### 5.5 Virtual single-cartridge bench

48-cell matrix (4 speeds × 4 loads × 3 tiers): **100% pass rate (simulation)**.

---

## 6. Development Gate Status

| Gate | Milestone | Simulation | Hardware |
|:----:|-----------|:----------:|:--------:|
| 1 | Single cartridge bench | 48-cell matrix complete | Not started |
| 2 | Stable free-piston | Timing model | Not started |
| 3 | Linear generator | Parametric model | Not started |
| 4 | Multi-cartridge ring | X4–X16 layout study | Not started |
| 5 | Vehicle integration | **Complete** (6 bodies) | Not started |
| 6 | AI optimisation | Supervisory brain + ECU (software) | Not started |
| 7 | CAD / packaging | Concept renders | Not started |

**Software position:** vehicle integration complete; Gate 6 advanced in software (not certified).  
**Hardware position:** no gate cleared.

*Reference vehicle figures use the Phase-1 **4/2/2** eight-cartridge stack (~230 kW). A separate
production-ring study freezes a **4/6/2** twelve-cartridge layout (~313 kW) — different layer;
do not equate the two when quoting fuel economy.*

---

## 7. Conclusions (simulation)

**Supported by modelling:**

- Architecture is internally energy-consistent.  
- Small LFP pack runs cool, lasts the vehicle life, low embodied carbon (4.7% of lifecycle).  
- Capability comes from stored energy and battery power, not peak kW alone.  
- Default battery is over-specced — right-sizing saves cost and weight.  
- Smart flywheel timing was tested and **rejected** — honest negative result.  
- One cylinder can fail; all bodies still pass performance targets.

**Not validated externally:**

- Combustion efficiency on a real rig  
- Rotor round-trip efficiency and burst containment  
- NVH, packaging, homologation  
- Official regulatory test traces (reconstructions used)

---

## 8. What This Document Does Not Claim

| Limit | Implication |
|-------|-------------|
| No physical prototypes | Efficiency, noise, weight are modelled |
| Combustion is simplified in default model | Detailed physics used only for bench matrix |
| WLTP/EPA are reconstructions | Not official copyrighted traces |
| AI controller is experimental | Not ASIL-certified production software |
| PCMRITMS 75–85% round-trip | Optimistic until bench measured |
| Patents not yet filed | File before wide public disclosure |

---

## 9. Roadmap to Measured Credibility

| Step | Months | Deliverable | Model falsified if… |
|------|-------:|-------------|---------------------|
| Single ATPE cartridge rig | 0–12 | Pressure, stroke, kW data | BTE ≪ 40% at sweet spot |
| Single PCMRITMS rotor bench | 6–18 | Torque pulse, round-trip η | η ≪ 70% or no +35% boost |
| Integrated electrical bench | 18–24 | Shared 800 V bus | Bus instability under spike |
| Mule vehicle | 30–42 | Dyno + on-road fuel | Highway fuel ≫ 6 L/100 km |
| Emissions + durability | 42–54 | Euro 7 / 300k km equiv | Regulatory or wear failure |

**Seed funding (illustrative):** $8–15M over 18 months — two bench prototypes with measured data.

---

## 10. One-Paragraph Summary

Project Phoenix pairs a crankless three-tier linear generator with a phase-coordinated
inertial torque buffer and a small lifetime battery on a unified 800 V DC bus. Thirteen
simulation studies across six vehicle types support the conclusion that this architecture
can match strong performance with approximately **28% less fuel** than a conventional 2.0 L
turbo on the same SUV, **zero battery replacements** over the vehicle life, and **fault
tolerance with one cylinder offline**. Simulation shows these outcomes; measured prototypes
are the required next step.

---

*Companion documents: ATPE Whitepaper v1.0; PCMRITMS Whitepaper v1.0.*
