# ATPE — Adaptive Torque & Power Engine
## Concept Whitepaper v1.0

**Project Phoenix**  
**Document status:** Conceptual / simulation-validated — no hardware prototype measured  
**Publication date:** July 2026  
**Classification:** External distribution permitted with simulation disclaimer

---

> **Disclaimer:** All performance figures in this document are **engineering projections**
> from physics-based computer simulation unless explicitly marked *measured*. Prefix
> external claims with “simulation shows…” Hardware validation is required before any
> commercial or regulatory decision.

---

## 1. What It Is

The **Adaptive Torque & Power Engine (ATPE)** — also called the Adaptive Modular Linear
Combustion Engine (AMLCE) — is a **crankless, camless, modular free-piston linear generator**
for series-hybrid vehicles. Combustion drives opposed pistons linearly; integrated linear
alternators produce DC electricity on a 400–800 V bus. Traction motors drive the wheels.

The central engineering insight: **match displacement to demand at the cylinder level**, not
the engine level. Instead of one fixed-displacement block with binary cylinder deactivation,
ATPE uses **three size classes (tiers)** of independently controlled combustion cartridges.
A supervisory controller activates the smallest tier set that covers the generation setpoint,
keeping each firing module near its optimal brake-specific fuel consumption (BSFC) band.

**ATPE does not turn the wheels.** It is a highly efficient electricity generator that runs
at quasi-steady load while faster buffers (inertial rotor and battery) handle transients.

---

## 2. Reference Architecture

### 2.1 Three-tier cartridge stack (Phase 1 reference vehicle)

| Tier | Role | Units | Displacement / unit | Max electrical (kW, tier total) | Target thermal efficiency |
|------|------|------:|--------------------:|--------------------------------:|--------------------------:|
| **1 — Micro** | Cruise, idle, light city | 4 | 100 cc | 30 | 44% |
| **2 — Medium** | Highway, moderate load | 2 | 300 cc | 80 | 41% |
| **3 — Large** | Peaks, grades, towing | 2 | 750 cc | 120 | 38% |

**Total stack (Phase 1):** 8 cartridges, ~230 kW peak electrical output.

**Tier selection** is discrete and additive: activate from the smallest tier upward until demand
is met. When multiple tiers fire together, fuel and power are blended using **energy-weighted
tier fill** — each active tier contributes in proportion to its electrical output and thermal
efficiency.

**Production vision (PHOENIX-X12):** cartridges arranged in a ring (X4 through X16 layouts) with
mixed tier counts per ring position.

### 2.2 Single cartridge module

Each cartridge is self-contained:

- Opposed pistons (50 mm total stroke, ±25 mm per side)
- Electronic valves — no camshaft
- Integrated linear alternator → DC bus
- Bounce chamber / gas spring for piston return
- Local pressure and position sensing

There is **no crankshaft** and **no shared camshaft**.

### 2.3 Variable-geometry controls (crankless advantage)

| Mechanism | Purpose |
|-----------|---------|
| Electronic valve timing | Torque at low speed vs power at high speed |
| Variable compression ratio | High CR for efficiency; low CR for knock-limited boost |
| Variable stroke | On-demand expansion ratio (Atkinson/Miller-like) |
| Variable intake geometry | Runner length tuning per operating point |
| Tier activation | Generalised cylinder deactivation by size class |

---

## 3. Operating Modes

| Mode | Generation setpoint | Active tiers | Notes |
|------|--------------------:|:-------------|-------|
| Off | 0 kW | None | Battery and buffer cover demand (urban EV) |
| Micro-only | 0–30 kW | Tier 1 | Highest thermal efficiency |
| Micro + medium | 30–110 kW | Tiers 1–2 | Highway charge-sustaining sweet spot |
| Full stack | >110 kW | Tiers 1–3 | Large tier active ≤15% of drive time |
| Degraded | Reduced cap | Remaining units | One cartridge offline — graceful degradation |

**Charge-sustaining state-of-charge target:** 55%  
**EV-only floor SoC:** 25%

---

## 4. Control Philosophy

ATPE generation is the **slow loop** (10–100 ms) in the unified powertrain controller.
The generation setpoint is:

**P_gen* = low-pass-filtered positive demand + SoC correction**

with a filter time constant of approximately **5 seconds** so the engine does not chase
transients. Fast mismatch is handled by the PCMRITMS inertial buffer first (1–5 ms), then
the battery.

**Decision priorities:**

1. Can the battery alone cover demand at acceptable SoC? → ATPE off.
2. Does Tier 1 alone suffice? → Micro cartridges only.
3. Demand exceeds Tier 1? → Add Tier 2, then Tier 3 as needed.
4. Acceleration spike detected? → Buffer discharges; ATPE setpoint rises on the slow loop.
5. Thermal / wear management → rotate firing pattern across units within a tier.

---

## 5. Efficiency — Simulation Results

### 5.1 Engine-level comparison vs conventional crankshaft ICE

| Metric | Conventional 2.0 L turbo | ATPE (simulation) |
|--------|--------------------------|-------------------|
| Part-load efficiency | 20–28% | 38–44% (tier-weighted) |
| Peak thermal efficiency | 38–42% | 42–47% (micro tier) |
| Mechanical losses (crank, cam) | 8–12% | ~2–3% |
| Displacement matching | Fixed block | Per-tier additive |
| Idle fuel | Moderate | ≈0 (combustion stops) |

### 5.2 Vehicle fuel economy — AWD SUV reference (simulation)

Identical vehicle mass, drag, and tyres; only the powertrain differs.

| Drive cycle | ATPE (L/100 km) | Conventional ICE (L/100 km) | Saving |
|-------------|----------------:|----------------------------:|-------:|
| Urban (charged battery) | 0.00 | 0.00 | EV-dominant |
| Highway (charge-sustaining) | **4.46** | 7.67 | **42%** |
| Tow + grade | 6.91 | 11.92 | 42% |
| Mixed | **2.35** | 3.26 | **28%** |
| WLTP-class reconstruction | 4.61 | 7.30 | 37% |

### 5.3 CO₂ — AWD SUV (simulation)

| Cycle | ATPE (g/km) | Conventional ICE (g/km) |
|-------|------------:|------------------------:|
| Highway | 103 | 177 |
| Mixed | **54** | **75** |
| WLTP-class | 106 | 169 |

### 5.4 Fleet-wide mixed-cycle fuel (simulation)

| Vehicle body | Fuel (L/100 km) | Running cost (€/km) | Lifecycle CO₂ (g/km) |
|--------------|----------------:|--------------------:|---------------------:|
| Hatchback | 0.55 | 0.039 | 44 |
| Sedan | 0.64 | 0.040 | 47 |
| Crossover | 1.11 | 0.048 | 60 |
| AWD SUV | 2.61 | 0.072 | 102 |
| Van / MPV | 2.95 | 0.077 | 112 |
| Pickup | 3.86 | 0.092 | 137 |

Uncertainty bands (5th–95th percentile) on AWD SUV running cost: **€0.066–0.091/km**.

---

## 6. Fault Tolerance (simulation)

Engineering requirement: the vehicle must remain driveable with **one combustion cartridge
offline**.

**Result (simulation):** all six vehicle bodies pass **9 of 9** performance checks
(acceleration, top speed, 20% gradeability, efficiency) in **18 of 18** fault scenarios
(one cylinder removed from each tier, across all bodies).

| Fault scenario | Peak power remaining (kW) | ERS checks |
|----------------|--------------------------:|:----------:|
| Tier 1 micro cylinder offline | 222 | 9/9 PASS |
| Tier 2 medium cylinder offline | 190 | 9/9 PASS |
| Tier 3 large cylinder offline | 170 | 9/9 PASS |

---

## 7. Virtual Single-Cartridge Bench (simulation acceptance criteria)

Before hardware is built, a **48-cell virtual bench matrix** defines lab acceptance criteria:

| Parameter | Value |
|-----------|-------|
| Grid | 4 speeds × 4 loads × 3 tiers = **48 cells** |
| Overall pass rate (simulation) | **100% (48/48)** |
| Speeds tested | 1800, 2200, 2600, 3000 rpm |
| Loads tested | 25%, 50%, 75%, 100% |

**Sweet-spot operating point (Tier 2 medium, 2600 rpm, 75% load, simulation):**

| Metric | Value | 90% confidence interval |
|--------|------:|------------------------|
| Stroke | 50.0 mm | — |
| Peak electrical power | 58.1 kW | 52.8–62.8 kW |
| Electric efficiency | 49.6% | 47–51% |
| IMEP | 4.2 bar | 3.9–4.6 bar |
| Bench acceptance checks | 6/6 pass | — |

**Bearing runout target (hardware):** ≤0.03 mm  
**Peak power target per cartridge at scale (hardware):** ≥78 kW

---

## 8. Multi-Cartridge Ring Scaling (simulation)

Ring layouts from **X4 to X16** were evaluated with one-, two-, and three-tier mixes.

**Key finding (simulation):** no homogeneous single-tier layout passes all nine vehicle
performance targets. A **multi-tier stack is required.**

| Layout category | Example mix (micro/medium/large) | ERS result | Highway fuel (L/100 km) |
|-----------------|----------------------------------|:----------:|------------------------:|
| Single-tier micro only | 14/0/0 | 8/9 FAIL | 4.31 |
| Single-tier medium only | 0/4/0 | 7/9 FAIL | 4.62 |
| Single-tier large only | 0/0/3 | 7/9 FAIL | 4.99 |
| Two-tier | 1/4/0 | 9/9 PASS | 4.58 |
| Three-tier (Phase 1 reference) | **4/2/2** | **9/9 PASS** | **4.46** |
| Three-tier (X12 storyboard) | 10/1/1 | 9/9 PASS | 4.31 |

Rated electrical output scales from 105 kW (single-tier micro) to 478 kW (X16 three-tier).

---

## 9. Engineering Challenges

| Challenge | Simulation status | Hardware requirement |
|-----------|-------------------|----------------------|
| Combustion timing without crank TDC | Physics surrogate model | Optical/pressure sensing per cartridge |
| Piston resonance / return | RPM-linked cycle model | Opposed-piston or active EM return |
| Bearing runout | Target ≤0.03 mm specified | Displacement probes on magnetic bearings |
| NVH from async firing | Not modelled in detail | Acoustic test cell |
| Ring packaging | Concept renders only | Production CAD |
| Thermal zoning | Simplified model | Rig thermocouples + FEA |

---

## 10. Comparison to Prior Art

| Prior art | What it has | What ATPE adds |
|-----------|-------------|----------------|
| Toyota VCM / GM AFM | Binary cylinder on/off | Multi-tier sizing + free-piston |
| Achates opposed-piston | Opposed geometry, no crank | Modular ring + linear generation |
| Aquarius / Libertine FPE | Single free-piston generator | Scalable multi-tier architecture |
| Infiniti VC-Turbo | Variable compression | VC + free stroke + no crankshaft |

---

## 11. Development Roadmap

| Phase | Timeline | Deliverable |
|-------|----------|-------------|
| Virtual bench (complete) | Done | 48-cell matrix, uncertainty bands |
| Single cartridge rig | Months 0–12 | Measured pressure, stroke, kW |
| Stable free-piston | Months 6–18 | Runout ≤0.03 mm; stability envelope |
| Linear generator | Months 12–18 | ≥78 kW peak per cartridge |
| Multi-cartridge ring | Months 18–30 | X8–X12 synchronization |
| Mule vehicle | Months 30–42 | Compare highway fuel to 4.46 L/100 km target |

**Seed-phase funding (illustrative):** $8–15M over 18 months for two bench prototypes with
measured efficiency data.

---

## 12. Summary

ATPE replaces the crankshaft engine with a modular ring of free-piston linear generators
sized in three tiers. Simulation shows approximately **28% less fuel** than a conventional
2.0 L turbo on the same SUV, **zero idle burn**, and **fault tolerance with one cartridge
offline** — provided hardware achieves the assumed thermal and electrical efficiencies.

The virtual 48-cell bench matrix defines what the first laboratory rig must measure before
any commercial claim is upgraded from “simulation shows” to “measured.”

---

*Companion documents: PCMRITMS Whitepaper v1.0; Project Phoenix Integrated System Whitepaper v1.0.*
