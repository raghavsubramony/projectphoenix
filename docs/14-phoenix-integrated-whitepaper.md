# Project Phoenix — Integrated Powertrain
## System Whitepaper v1.0 (internal engineering copy)

> **For external publication**, use the standalone version with embedded data only:
> [whitepapers/Phoenix-Integrated-Whitepaper-v1.0.md](whitepapers/Phoenix-Integrated-Whitepaper-v1.0.md)
> and [whitepapers/pdf/Phoenix-Integrated-Whitepaper-v1.0.pdf](whitepapers/pdf/Phoenix-Integrated-Whitepaper-v1.0.pdf)

**Status:** Phase 1 simulation complete — hardware gates not cleared.  
**Audience:** Investors, OEM partners, grant agencies, technical due diligence.  
**Integrity:** `.venv\Scripts\python.exe verify.py` → PASS (63 checks + 169 tests).

> **Disclaimer:** This document reports **simulation-validated** engineering projections.
> No road-test, EPA/WLTP certification, or homologation claims are made. Prefix external
> statements with “simulation shows…” Measured bench prototypes are the Seed-phase deliverable.

**Subsystem whitepapers:**

| Component | Document |
|-----------|----------|
| ATPE (engine / generator) | [13-atpe-whitepaper.md](13-atpe-whitepaper.md) |
| PCMRITMS (inertial buffer) | [10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md) |
| Twin alignment (rotor headline) | [11-pcmritms-twin-alignment.md](11-pcmritms-twin-alignment.md) |

---

## 1. Executive Summary

Project Phoenix is an **integrated series-hybrid powertrain** combining:

1. **ATPE** — a crankless, three-tier free-piston linear generator  
2. **PCMRITMS** — a phase-coordinated multi-rotor inertial torque buffer  
3. **A small LFP battery** — low-frequency energy store and urban EV range  
4. **A unified controller** — nested slow/fast loops that keep the engine off transients  

The **digital twin** (pure Python, no paid dependencies) stress-tested this architecture across
**thirteen simulation studies (Moves A–M)**, six vehicle bodies, four temperature bands,
regulatory-style drive cycles, payload and cold-start scenarios, plug-in charging, 250,000 km
ageing, ICE benchmark comparison, and fault tolerance.

**Core simulation conclusion:** a small, cool-running, lifetime-lasting battery plus an inertial
sprint reserve plus an efficient generator can deliver strong performance with competitive
lifetime cost and carbon — **if hardware matches the model assumptions**.

**What has not been done:** building and measuring real cartridges, rotors, or a mule vehicle.

---

## 2. System Architecture

### 2.1 Energy flow

```
  PETROL
    │
    ▼
┌─────────────────────────────────────┐
│  ATPE — 3-tier free-piston ring     │  ← slow loop (10–100 ms)
│  Micro │ Medium │ Large cartridges  │
└─────────────────┬───────────────────┘
                  │ DC (400–800 V bus)
                  ▼
    ┌─────────────────────────────────┐
    │  Buffers (two bandwidths)       │
    │  • PCMRITMS — ms transients       │  ← fast loop (1–5 ms)
    │  • LFP battery — SoC (minutes)    │
    └─────────────────┬───────────────┘
                      ▼
              Traction motor(s) → Wheels
```

### 2.2 Why integrate ATPE + PCMRITMS

Each subsystem covers the other's critical weakness (`03-integration-viability.md`):

| ATPE weakness | PCMRITMS fix |
|---------------|--------------|
| Tier switching gap (~200–400 ms) | Buffer covers seamlessly |
| Irregular free-piston ripple | Rotor smoothing |
| Slow generation setpoint | ms inertial response |

| PCMRITMS weakness | ATPE fix |
|-------------------|----------|
| 118 kJ depletes in ~1.3 s at full discharge | Engine refills during sustained load |
| Needs primary energy source | Efficient continuous generation |
| Round-trip losses | ATPE efficiency advantage offsets |

### 2.3 Reference sizing (Phase 1, AWD SUV)

| Subsystem | Key parameter | Value |
|-----------|---------------|-------|
| ATPE stack | Peak electrical | ~230 kW (8 cartridges, tier-weighted) |
| PCMRITMS | Stored energy | 118 kJ |
| PCMRITMS | Continuous discharge | 90 kW |
| PCMRITMS | Brief burst (rotor-coupled) | **140 kW** (~1.3 s energy-limited) |
| Battery | Capacity | 20 kWh LFP |
| Battery | Max discharge | 120 kW (right-sized to ~90 kW for SUV) |
| Traction | Peak power | 222 kW |

---

## 3. Unified Control Law

Three nested loops with strict authority (`07-core-concept-refinements.md` §5):

**Loop A — Mode (100–500 ms):** `EV` vs `charge-sustaining` from filtered demand and SoC floor.

**Loop B — ATPE setpoint (10–100 ms):** low-pass filtered load + SoC correction → tier set.

**Loop C — Buffer arbitration (1–5 ms):**

1. PCMRITMS supplies/absorbs mismatch first (fastest, no chemical wear).  
2. Battery covers remainder.  
3. Residual → flagged **capability shortfall** (never silently fabricated).

Surplus generation refills PCMRITMS to target first, then charges the battery.

**Conservation guarantees (every timestep):**

- Power balance: $P_{gen} + P_{batt} + P_{buffer} = P_d + P_{losses}$  
- Bounded SoC ∈ [0, 1], buffer energy ∈ [0, $E_{max}$]  
- Fuel energy integrates per active tier efficiency

---

## 4. Digital Twin Validation — Moves A–M

| Move | Question answered | Headline result (simulation) |
|------|-------------------|------------------------------|
| **A** | Can the controller learn from data? | Learned ≈ rule-based with fair charge-sustaining correction |
| **B** | Battery heat and ageing? | +1 °C normal driving; **0 replacements** over vehicle life |
| **C** | Which inputs matter for fuel? | Aero > weight at highway; no single magic knob |
| **D** | True cost and lifecycle CO₂? | Battery embodied CO₂ **~4.7%** of SUV lifecycle |
| **E** | Smarter flywheel timing? | **Honest no** — energy-limited, not power-limited |
| **F** | Right-sizing parts? | SUV battery can drop **120 → 90 kW** discharge |
| **G** | Uncertainty on every headline? | 5–95% bands on fuel, cost, CO₂ |
| **H** | −10 °C to +40 °C? | 12–23% seasonal fuel swing; no thermal derate |
| **I** | WLTP / EPA reconstructions? | Rankings match home-made cycles |
| **J** | Cold engine morning? | Hurts long trips only; urban stays electric |
| **K** | Full payload? | +fuel, hill climb still passes |
| **L** | Grid charging (PHEV)? | CO₂ win only on clean grid |
| **M** | 250,000 km ageing? | ~23% EV range loss; modest fuel creep |

**Move N — ICE benchmark:** ~**28%** less fuel vs conventional 2.0 L turbo (AWD SUV, mixed).

All Moves are opt-in capable, tested, and locked in `verify.py`.

---

## 5. Key Performance Numbers (simulation)

*Median of honest uncertainty bands unless noted. AWD SUV unless noted.*

| Metric | Value | Locked by |
|--------|-------|-----------|
| Highway fuel (charge-sustaining) | **4.46 L/100 km** | `verify.py` |
| Mixed cycle fuel | **2.35 L/100 km** | Move N |
| vs 2.0 L turbo (mixed) | **−28%** fuel | `ice_benchmark.py` |
| Lifetime CO₂ (all-in) | **102 g/km** | Move D |
| Running cost | **€0.072/km** | Move D |
| Battery replacements (250k km) | **0** | Move D / M |
| PCMRITMS peak torque boost | **+34.9%** (brief) | `pcmritms_rotor.py` |
| Fault tolerance (1 cylinder out) | **18/18 pass** | `graceful_degradation.py` |
| Six bodies × ERS targets | **9/9 each** | `acceptance.py` |

### Fleet-wide table (mixed-cycle fuel, simulation)

| Body | Fuel L/100 km | Cost/km | CO₂ g/km |
|------|--------------:|--------:|---------:|
| Hatchback | 0.55 | €0.039 | 44 |
| Sedan | 0.64 | €0.040 | 47 |
| Crossover | 1.11 | €0.048 | 60 |
| AWD SUV | 2.61 | €0.072 | 102 |
| Van / MPV | 2.95 | €0.077 | 112 |
| Pickup | 3.86 | €0.092 | 137 |

Full table with uncertainty bands: `docs/evidence-pack/EVIDENCE-BASELINE.txt`.

---

## 6. Virtual Hardware Gates (software evidence)

| Gate | Milestone | Software status | Hardware |
|:----:|-----------|-----------------|----------|
| **1** | Single cartridge bench | 48-cell matrix, 100% pass (sim) | Not started |
| **2** | Stable free-piston | RPM-linked timing surrogate | Not started |
| **3** | Linear generator | Parametric η_gen | Not started |
| **4** | Multi-cylinder ring | X4–X16 tier-mix sweep + CSV | Not started |
| **5** | Vehicle integration | **Done** — 6 bodies, dashboard | Not started |
| **6** | AI optimisation | ATPE Brain + ECU advanced (software); not certified | Not started |
| **7** | CAD / packaging | Concept renders only | Not started |

**Software position:** Gate 5 complete; Gate 6 advanced in software. **Hardware position:** no gate cleared.

*Phase-1 locked headlines use the **4/2/2** (8-cylinder) vehicle twin. A separate Phoenix V3
**4/6/2 X12** ring freeze candidate exists for production-ring studies — do not mix the two stacks
when quoting fuel figures.*

---

## 7. Big-Picture Conclusions (simulation)

**Validated internally:**

- Architecture is energy-consistent; claims are reproducible via `verify.py`.  
- Small LFP pack runs cool, lasts the vehicle life, low embodied carbon.  
- Capability comes from **stored energy and battery power**, not peak kW alone.  
- Default battery is over-specced — right-sizing saves cost/weight.  
- Some ideas were tested and **rejected honestly** (smart flywheel timing).  

**Not validated externally:**

- Combustion efficiency on a real rig  
- Rotor round-trip efficiency and containment  
- NVH, packaging, safety certification  
- Official regulatory test traces (reconstructions only)

---

## 8. Honest Limits — What This Whitepaper Does Not Claim

| Limit | Implication |
|-------|-------------|
| No physical prototypes | Efficiency, noise, weight are modelled |
| Combustion surrogate (default path) | Gate 1 physics is opt-in |
| WLTP/EPA are reconstructions | Not copyrighted official traces |
| AI controller / ECU are software-validated | Not ASIL-certified production software |
| PCMRITMS 75–85% round-trip | Labelled optimistic until bench measured |
| IP not filed in repo | File provisionals before wide disclosure |

---

## 9. Roadmap to Hardware Credibility

| Step | Months | Deliverable | Falsifies model if… |
|------|--------|-------------|---------------------|
| 1. Single ATPE cartridge rig | 0–12 | Pressure, stroke, kW CSV | BTE ≪ 40% at sweet spot |
| 2. Single PCMRITMS rotor bench | 6–18 | Torque pulse, round-trip η | η ≪ 70% or cannot hit +35% boost |
| 3. Integrated electrical bench | 18–24 | Shared 800 V bus | Bus instability under spike |
| 4. Mule vehicle | 30–42 | Dyno + on-road fuel | Highway fuel ≫ 6 L/100 km |
| 5. Emissions + durability | 42–54 | Euro 7 / 300k km equiv | Fails regulatory or wear targets |

Seed ask (illustrative): **$8–15M**, 18 months — two bench prototypes with measured data
(`05-investor-pitch-and-advantages.md`).

---

## 10. Evidence Package (attach to data room)

Regenerate before any external send:

```powershell
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
.venv\Scripts\python.exe scripts\export_gate4_scaling.py
.venv\Scripts\python.exe verify.py
```

| Artifact | Path |
|----------|------|
| Executive baseline | `docs/evidence-pack/EVIDENCE-BASELINE.txt` |
| Gate 1 matrix CSV | `docs/evidence-pack/GATE1-VIRTUAL-BENCH-MATRIX.csv` |
| Gate 4 scaling CSV | `docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv` |
| Tier advantage CSV | `docs/evidence-pack/GATE4-TIER-ADVANTAGE.csv` |
| Interactive demo | `python -m dashboard` |
| Visual storyboard | `designs/` (20-panel PHOENIX-X12 concept) |

---

## 11. One-Paragraph Pitch (simulation-qualified)

> Project Phoenix pairs a crankless three-tier linear generator with a phase-coordinated
> inertial torque buffer and a small lifetime battery on a unified DC bus. A disciplined digital
> twin — thirteen studies, six vehicle bodies, virtual bench matrices, and 58 automated integrity
> checks — supports the story that this architecture can match strong performance with ~28% less
> fuel than a conventional 2.0 L turbo on the same SUV, zero battery replacements, and fault
> tolerance with one cylinder offline. **Simulation shows these outcomes; measured prototypes are
> the required next step.**

---

## Related documents

| Document | Purpose |
|----------|---------|
| [PROJECT-STATUS-AND-NEXT-STEPS.md](PROJECT-STATUS-AND-NEXT-STEPS.md) | Plain-English status |
| [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) | Funding, IP, rig plan |
| [15-patent-portfolio.md](15-patent-portfolio.md) | Patent family map |
| [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) | Gate scorecard + ERS |

*Subsystem detail: [13-atpe-whitepaper.md](13-atpe-whitepaper.md),
[10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md).*
