# ATPE — Adaptive Torque & Power Engine
## Concept Whitepaper v1.0 (internal engineering copy)

> **For external publication**, use the standalone version with embedded data only:
> [whitepapers/ATPE-Whitepaper-v1.0.md](whitepapers/ATPE-Whitepaper-v1.0.md)
> and [whitepapers/pdf/ATPE-Whitepaper-v1.0.pdf](whitepapers/pdf/ATPE-Whitepaper-v1.0.pdf)

**Status:** Conceptual / simulation-validated — no hardware prototype measured in this repository.  
**Audience:** Investors, OEM engineering partners, grant reviewers, patent counsel.  
**Integrity:** `.venv\Scripts\python.exe verify.py` → PASS (58 checks + 135 tests).

> **Disclaimer:** All performance figures below are **engineering projections** from the
> Project Phoenix digital twin unless explicitly marked *measured*. Prefix external claims with
> “simulation shows…” Hardware validation is required before commercial decisions.

Cross-references: [01-atpe-concept.md](01-atpe-concept.md),
[07-core-concept-refinements.md](07-core-concept-refinements.md),
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md),
[14-phoenix-integrated-whitepaper.md](14-phoenix-integrated-whitepaper.md).

---

## 1. What It Is

The **Adaptive Torque & Power Engine (ATPE)** — also called the Adaptive Modular Linear
Combustion Engine (AMLCE) — is a **crankless, camless, modular free-piston linear generator**
for series-hybrid vehicles. Combustion drives opposed pistons linearly; integrated linear
alternators produce DC electricity on a high-voltage bus. Traction motors drive the wheels.

The central engineering insight: **match displacement to demand at the cylinder level**, not
the engine level. Instead of one fixed-displacement block with binary cylinder deactivation,
ATPE uses **three size classes (tiers)** of independently controlled combustion cartridges.
A supervisory controller activates the smallest tier set that covers the generation setpoint,
keeping each firing module near its optimal brake-specific fuel consumption (BSFC) island.

**ATPE does not turn the wheels.** It is a highly efficient electricity generator that runs
at quasi-steady load while faster buffers (inertial rotor + battery) handle transients — see
the integrated system whitepaper for the full powertrain story.

---

## 2. Reference Architecture

### 2.1 Three-tier cartridge stack

| Tier | Role | Units (Phase 1) | Disp / unit (cc) | Max elec (kW, tier total) | Target thermal η |
|------|------|-----------------|------------------|---------------------------|------------------|
| **1 — Micro** | Cruise, idle, light city | 4 | 100 | 30 | 0.44 |
| **2 — Medium** | Highway, moderate load | 2 | 300 | 80 | 0.41 |
| **3 — Large** | Peaks, grades, towing | 2 | 750 | 120 | 0.38 |

**Tier selection** is discrete and additive: fill from smallest tier upward until demand is met.
When multiple tiers fire together, fuel accounting uses **energy-weighted tier fill** (July 2026
model — see `digital_twin/atpe.py`).

**Production vision (PHOENIX-X12):** cartridges arranged in a ring (X4–X16 layouts) with
mixed tier counts per ring position. Virtual layout search is documented in Gate 4
(`digital_twin/gate4_scaling.py`).

### 2.2 Single cartridge module

```
  Fuel / air ──► [Combustion chamber]
                        │
            ┌───────────┴───────────┐
            ▼                       ▼
     [Opposed pistons]        [Electronic valves]
            │                  (no camshaft)
            ▼
     [Linear alternator] ──► DC bus
            ▲
     [Bounce chamber / gas spring]
```

Each cartridge is self-contained: combustion, variable stroke, electronic valve timing,
linear generation, and local sensing. There is **no crankshaft** and **no shared camshaft**.

### 2.3 Variable-geometry knobs (crankless advantage)

Because piston motion is not kinematically locked to a crank, each module can vary:

| Mechanism | Purpose |
|-----------|---------|
| **Electronic valve timing** | Torque at low speed vs power at high speed |
| **Variable compression ratio** | High CR for efficiency; low CR for knock-limited boost |
| **Variable stroke** | On-demand expansion ratio (Atkinson/Miller-like) |
| **Variable intake geometry** | Runner length tuning per operating point |
| **Tier activation** | Generalised cylinder deactivation by *size class* |

---

## 3. Operating Modes

| Mode | Generation setpoint | Typical tiers | Notes |
|------|---------------------|---------------|-------|
| **Off** | 0 kW | None | Battery + buffer cover demand (urban EV) |
| **Micro-only** | 0–30 kW | Tier 1 | Highest thermal efficiency band |
| **Micro + medium** | 30–110 kW | Tiers 1–2 | Highway charge-sustaining sweet spot |
| **Full stack** | >110 kW | Tiers 1–3 | Rare peaks; large tier active ≤15% of drive time |
| **Degraded** | Reduced cap | Remaining units | One cartridge offline — graceful degradation |

---

## 4. Control Philosophy

ATPE generation is the **slow loop** (10–100 ms) in the unified powertrain controller:

$$P_{gen}^{*} = \text{LPF}_{\tau}(P_d^{+}) + k_{soc}\,(\text{SoC}_{tgt}-\text{SoC})$$

with $\tau \approx 5$ s so the engine **does not chase transients**. Fast mismatch is handled
by the PCMRITMS inertial buffer first, then the battery (`07-core-concept-refinements.md` §5).

**Decision priorities:**

1. Can battery alone cover demand at acceptable SoC? → ATPE off.
2. Does Tier 1 alone suffice? → Micro cartridges only.
3. Demand exceeds Tier 1? → Add Tier 2, then Tier 3 as needed.
4. Spike detected (pedal rate proxy)? → Buffer discharges; ATPE setpoint rises on slow loop.
5. Thermal / wear management → rotate firing pattern across units within a tier.

A machine-learning study (`ml_study/`) demonstrates imitation of tier selection (~94.5% accuracy)
but production control remains rule-based and deterministic in Phase 1.

---

## 5. Efficiency Claims (simulation-backed)

### 5.1 vs conventional crankshaft ICE

| Metric | Conventional 2.0 L turbo | ATPE (twin model) |
|--------|--------------------------|-------------------|
| Part-load efficiency | 20–28% | 38–44% (tier-weighted) |
| Peak thermal efficiency | 38–42% | 42–47% (micro tier) |
| Mechanical losses (crank, cam) | 8–12% | ~2–3% (no crankshaft) |
| Displacement matching | Fixed block | Per-tier additive |
| Idle fuel | Moderate | ≈0 (combustion stops) |

### 5.2 Headline vehicle results (AWD SUV reference, simulation)

| Cycle | ATPE fuel | Conventional ICE (same vehicle) | Saving |
|-------|-----------|--------------------------------|--------|
| Mixed | 2.35 L/100 km | 3.26 L/100 km | **~28%** |
| Highway (charge-sustaining) | 4.46 L/100 km | 7.67 L/100 km | **~42%** |
| Urban (charged battery) | ~0 L/100 km | ~0 L/100 km | EV-dominant |

Locked by `verify.py` and `docs/evidence-pack/EVIDENCE-BASELINE.txt`.

### 5.3 Fault tolerance (ERS §4.8)

With **one cartridge offline** in any tier, all six vehicle bodies still pass **9/9** ERS
performance checks (acceleration, top speed, gradeability, efficiency) — **18/18 scenarios**
in simulation (`digital_twin/graceful_degradation.py`).

---

## 6. Virtual Gate 1 Bench (digital prototype)

Before metal is cut, a **48-cell virtual bench matrix** defines acceptance criteria:

- **Grid:** 4 speeds × 4 loads × 3 tiers = 48 operating points
- **Pass rate (simulation):** 100% (48/48)
- **Exports:** `docs/evidence-pack/GATE1-VIRTUAL-BENCH-MATRIX.csv`
- **Opt-in physics path:** `gate1_bench_at_load()` in `digital_twin/single_cylinder.py`

**Sweet-spot snapshot (Tier 2, 2600 rpm, 75% load, simulation):**

| Metric | Value |
|--------|-------|
| Stroke | 50 mm (opposed ±25 mm) |
| Peak power | 58.1 kW |
| Electric efficiency | 0.496 |
| IMEP | 4.2 bar |
| Bench checks | 6/6 pass |

**Uncertainty bands (Monte Carlo on perturbed inputs):** efficiency 0.50 ± 0.01 (90% CI
0.47–0.51); peak power 57.7 ± 3.0 kW.

> These are **virtual acceptance tests**, not measured rig data. Seed-phase hardware must
> compare against the same matrix and update the twin.

---

## 7. Virtual Gate 4 — Multi-cartridge scaling

Ring layouts from **X4 to X16** with one-, two-, and three-tier mixes were swept under Phase 1
and storyboard kW profiles (`digital_twin/gate4_scaling.py`).

**Design-aligned finding (simulation):** no single-tier layout passes full ERS; a **three-tier
stack** (e.g. 4/2/2 or 7/1/0 on X8 storyboard profile) is required for simultaneous acceleration,
top speed, hill climb, and efficiency targets.

Exports: `docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv`,
`docs/evidence-pack/GATE4-TIER-ADVANTAGE.csv`.

---

## 8. Engineering Challenges (honest)

| Challenge | Status in repo | Hardware need |
|-----------|----------------|---------------|
| Combustion timing without crank TDC reference | Surrogate + opt-in physics | Optical/pressure sensing per cartridge |
| Piston resonance / return control | RPM-linked cycle model | Opposed-piston or active EM return on rig |
| Bearing runout (≤0.03 mm target) | Proxy in twin | Displacement probes on magnetic bearings |
| NVH from async free-piston firing | Not modelled in detail | Acoustic test cell |
| Packaging of linear cartridges in ring | Storyboard + Blender concept | Production CAD (Gate 7) |
| Thermal zoning (combustion + generator) | Simplified | Rig thermocouples + FEA |

---

## 9. Comparison to Prior Art

| Prior art | What it has | What ATPE adds |
|-----------|-------------|----------------|
| Toyota VCM / GM AFM | Binary cylinder on/off | Multi-tier **sizing** + free-piston |
| Achates opposed-piston | Opposed geometry, no crank | Modular ring + linear generation + tier AI |
| Aquarius / Libertine FPE | Single free-piston generator | Scalable multi-tier architecture |
| Infiniti VC-Turbo | Variable compression | VC + free stroke + no crankshaft |

**Novelty focus (for IP):** the **combination** of sized tier cartridges + crankless linear
generation + energy-weighted tier fill + ring scaling — not any single element in isolation.

---

## 10. Development Roadmap

| Phase | Timeline | Deliverable | Gate |
|-------|----------|-------------|------|
| **1 — Virtual bench** | Done (software) | 48-cell matrix, CSV, uncertainty bands | Gate 1 (software) |
| **2 — Single cartridge rig** | Months 0–12 | Measured pressure, stroke, kW out | Gate 1 (hardware) |
| **3 — Stable free-piston** | Months 6–18 | Runout ≤0.03 mm class; stability envelope | Gate 2 |
| **4 — Linear generator** | Months 12–18 | ≥78 kW peak per cartridge (scaled) | Gate 3 |
| **5 — Multi-cartridge ring** | Months 18–30 | X8–X12 synchronization | Gate 4 |
| **6 — Vehicle integration** | Months 30–42 | Mule SUV; compare to 4.46 L/100 km highway | Gate 5 (hardware) |

---

## 11. How to Reproduce Claims

```powershell
.venv\Scripts\python.exe verify.py
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
.venv\Scripts\python.exe scripts\export_gate4_scaling.py
.venv\Scripts\python.exe main.py --quick
```

See [08-digital-twin-design.md](08-digital-twin-design.md) for module map.

---

## 12. Summary

> ATPE replaces the crankshaft engine with a modular ring of free-piston linear generators
> sized in three tiers. Simulation shows ~28% less fuel than a conventional 2.0 L turbo on
> the same SUV, zero idle burn, and fault tolerance with one cartridge offline — provided the
> hardware achieves the assumed thermal and electrical efficiencies. The virtual Gate 1 bench
> matrix defines what the first lab rig must measure.

*Next document: [14-phoenix-integrated-whitepaper.md](14-phoenix-integrated-whitepaper.md) —
full powertrain integration with PCMRITMS and digital-twin validation.*
