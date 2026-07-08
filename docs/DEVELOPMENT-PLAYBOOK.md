# Project Phoenix — Development Playbook

*How to run **two parallel tracks**: keep improving the digital twin **and** build the real
company (funding, IP, Gate 1 lab rig).*

**Last updated:** July 2026  
**Audience:** founders, technical leads, investors (internal), grant writers

Cross-references: [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md),
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) (Gate scorecard),
[PLAIN-ENGLISH-OVERVIEW.md](PLAIN-ENGLISH-OVERVIEW.md),
[PROJECT-STATUS-AND-NEXT-STEPS.md](PROJECT-STATUS-AND-NEXT-STEPS.md).

---

## 1. Strategy — you are not choosing twin *or* hardware

| Track | Goal | Status | Keep doing? |
|-------|------|--------|-------------|
| **A — Digital twin (Phase 2)** | Predict rig results; calibrate when data arrives | Phase 1 complete (`verify.py` PASS) | **Yes** — especially Gates 1–3 physics |
| **B — Funding & pitch** | Seed $8–15M (see §5 investor doc) | Narrative ready; no term sheet | **Yes** — package twin evidence + rig plan |
| **C — IP / patents** | Protect ATPE, PCMRITMS, integrated control | Not filed in repo | **Yes** — urgent before wide disclosure |
| **D — Gate 1 lab rig** | Measured single-cartridge data | Not built | **Yes** — parallel with A once funded or bootstrapped |

**Rule:** Twin improvements should **serve** the rig (what to measure, what would falsify the model).
Funding should **pay for** the rig. Patents should **precede** public demos and detailed OEM outreach.

---

## 2. Evidence base you already have (use in every pitch)

Regenerate anytime:

```powershell
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe verify.py
```

### Simulation headlines (say “simulation shows…”)

| Claim | Number | Locked by |
|-------|--------|-----------|
| AWD SUV highway fuel (charge-sustaining) | ~4.46 L/100km | `verify.py` |
| vs conventional 2.0 L turbo (same car, mixed) | ~28% less fuel | Move N / `ice_benchmark.py` |
| Battery replacements over vehicle life | 0 | Move D |
| Winter–summer fuel swing | ~14–23% | Move H |
| One cylinder offline — still passes targets | 18/18 scenarios | `graceful_degradation.py` |
| Six vehicle bodies — performance targets | 9/9 each | `acceptance.py` |
| Integrity gate | 58 checks + 135 tests PASS | `verify.py` |

### Honest limits (say these proactively)

- No road test, no EPA/WLTP certification, no homologation.
- Regulatory cycles are **reconstructions**, not official traces.
- Combustion on default path is tier-efficiency abstracted; Gate 1 physics is **opt-in**.
- **July 2026:** ATPE fuel accounting uses energy-weighted tier fill (see `atpe.py`, `tests/test_atpe.py`); headline fuel figures shifted ~3–4% vs the prior governing-tier shortcut.
- **No hardware gate cleared** — see [Gate scorecard](09-atpe-ers-and-insights.md#gate-scorecard).

### Visual assets

- 20-panel storyboard: `designs/create the scenes use the image as the idea concept.png`
- Blender concept + short MP4 animations in `designs/`
- Interactive demo: `python -m dashboard`

---

## 3. Funding & skills assessment (fill this in)

Use this worksheet to see **where you are** vs **what Seed needs**. Copy the table into a spreadsheet.

### 3.1 Team skills

| Capability | Needed for | Who has it? (name/org) | Gap? |
|------------|------------|------------------------|------|
| Free-piston / combustion engineering | Gate 1 rig | | |
| Mechanical design & fabrication | Rig + fixtures | | |
| Power electronics / linear generator | Gate 3 | | |
| Controls / embedded (real-time) | Rig safety + later vehicle | | |
| Test & instrumentation | Pressure, LVDT, power analyser | | |
| Simulation / digital twin | Track A | | |
| Fundraising / BD | Track B | | |
| Patent counsel | Track C | | |

### 3.2 Capital

| Source | Amount available | Strings attached | Realistic for Gate 1? |
|--------|------------------|------------------|------------------------|
| Founders / angels | | | |
| Grants (gov / innovation) | | | |
| University / lab partnership | | | |
| Strategic (Tier 1 / OEM) | | | |
| Deep-tech VC (Seed) | $8–15M target | Equity | Full Seed scope |

### 3.3 Minimum vs full Seed lab plan

| Scope | Budget (indicative) | Timeline | Unlocks |
|-------|---------------------|----------|---------|
| **Bootstrap Gate 1** | $150k–$400k | 6–12 mo | Single cartridge, basic instrumentation, first pressure/stroke/power curves |
| **Seed Gate 1 + flywheel bench** | $8–15M | 12–18 mo | Per [05-investor-pitch](05-investor-pitch-and-advantages.md): ATPE rig + PCMRITMS bench + team |
| **University collaboration** | Often in-kind + $50k–$200k cash | 12–24 mo | Credibility; slower but cheaper |

**If funds are thin:** start with **bootstrap Gate 1** (one cartridge, rent instrumentation, partner with a university engine lab). Twin Track A still runs in parallel to define acceptance tests before metal is cut.

---

## 4. Pitch package — the “schabang” checklist

### 4.1 Deliverables

| Item | Purpose | Status | Location / action |
|------|---------|--------|-------------------|
| **One-pager** | Email / first meeting | Draft from §4.2 below | Create `docs/pitch/ONE-PAGER.md` when ready |
| **10–12 slide deck** | Investor meetings | Outline §4.3 | PowerPoint / Google Slides from outline |
| **Evidence pack** | Data room / appendix | Auto-generated | `scripts/export_evidence_pack.py` |
| **Executive summary printout** | Leave-behind | Live from code | `main.py` (first section) |
| **Gate scorecard** | “Where we are” | Done | [09-atpe-ers §Gate scorecard](09-atpe-ers-and-insights.md#gate-scorecard) |
| **Gate 1 rig plan** | Use of funds | §6 below | This doc |
| **Demo** | Show don’t tell | Ready | `python -m dashboard` + `verify.py` |
| **Storyboard PDF/PNG** | Vision | Ready | `designs/…concept.png` |
| **Technical appendix** | Due diligence | Ready | `docs/09`, `docs/10`, `docs/PLAIN-ENGLISH-OVERVIEW.md` |

### 4.2 One-pager skeleton (copy and tighten)

**Problem:** SUVs need strong torque and towing without burning fuel like a conventional truck.

**Solution:** Series hybrid with (1) tiered free-piston generator (ATPE), (2) millisecond inertial buffer (PCMRITMS), (3) small long-life battery — software-defined power matching.

**Traction (simulation):** Six vehicle types pass performance targets; ~28% fuel saving vs 2.0 L turbo on identical routes; fault-tolerant in model; 46 automated checks PASS.

**Ask:** $X for Gate 1 single-cartridge lab rig + Y months runway (or: partnership with engine test lab).

**Milestone:** Measured efficiency and piston stability on bench — compare to digital twin; triggers Series A conversations.

**Team:** [names, relevant credentials]

**IP:** Provisional filings planned for ATPE architecture, PCMRITMS phase coordination, integrated control (see §5).

### 4.3 Slide deck outline (investor version)

| # | Slide | Content source |
|---|-------|----------------|
| 1 | Title | One-line pitch from [05-investor-pitch](05-investor-pitch-and-advantages.md) |
| 2 | Problem | Turbo lag, towing derate, hybrid complexity |
| 3 | Solution | Three-box diagram: ATPE + PCMRITMS + battery (use [12-architecture-diagrams](12-architecture-diagrams.md)) |
| 4 | Why now | Hybrid market growth (cite 05 — verify independently) |
| 5 | How it works | 60-second storyboard walk (Scenes 1, 8, 11, 16, 20) |
| 6 | Simulation proof | Executive summary table; **simulation shows** disclaimer |
| 7 | vs conventional ICE | Move N: 2.35 vs 3.26 L/100km mixed (SUV) |
| 8 | Battery & TCO story | Never replaced; low embodied CO₂ (Move D) |
| 9 | Competitive moat | Integration + software; IP (§5) |
| 10 | Roadmap & gates | Gate scorecard; Seed = bench prototypes |
| 11 | Use of funds | Gate 1 rig BOM + team (§6) |
| 12 | Ask & milestones | $8–15M Seed / or bootstrap path; 18-month deliverables |

### 4.4 Live demo script (15 minutes)

1. Run `verify.py --quick` — “This is our integrity seal.”
2. Open dashboard — one highway cycle, show tier activation and buffer.
3. Show executive summary from `main.py` or evidence pack.
4. Show storyboard — “This is what Seed builds toward.”
5. Show Gate scorecard — “Software Gate 5 done; hardware Gate 1 next.”
6. Close with ask and milestone.

---

## 5. IP & patent strategy

> **Not legal advice.** Engage a patent attorney before public disclosure, OEM meetings, or conference talks.

### 5.1 Three protection buckets

| Bucket | What to protect | Example claim themes | Doc support |
|--------|-----------------|----------------------|-------------|
| **ATPE** | Crankless multi-tier free-piston; electronic valve/stroke; cylinder deactivation as tiers | Modular cartridge ring; opposed dual-piston; load-following tier selection | [01-atpe-concept](01-atpe-concept.md), [07-core-concept-refinements](07-core-concept-refinements.md) |
| **PCMRITMS** | Phase-coordinated multi-rotor inertial buffer for driveline torque | Independent coaxial rotors; phase summing; burst vs continuous | [10-pcmritms-whitepaper-spec](10-pcmritms-whitepaper-spec.md) |
| **System** | Unified controller; rotor-buffer coupling; series-hybrid arbitration | Closed-loop surge gating; spike intent; charge-sustaining hybrid control | [14-phoenix-integrated-whitepaper](14-phoenix-integrated-whitepaper.md), twin code |

Full family inventory and counts: [15-patent-portfolio.md](15-patent-portfolio.md).

### 5.2 Recommended filing sequence

| Step | Action | When |
|------|--------|------|
| 1 | **Prior art search** (professional) | Before provisional |
| 2 | **Provisional patent** (US or PCT strategy with counsel) | Before investor roadshow / OEM |
| 3 | **Invention disclosure** internal record | Now — dates, inventors, diagrams |
| 4 | **Trade secret policy** for calibration maps | When hiring |
| 5 | **Full utility + international** | Within 12 months of provisional |

### 5.3 Disclosure discipline

**Do not publicly disclose** (without NDA or filed provisional):

- Detailed control algorithms not already in repo
- Exact tier sizing that is commercially optimal
- Unpublished mechanical drawings with tolerances

**Safe to share** with “simulation shows” framing:

- Everything already in this repo and evidence pack
- Gate scorecard and honest gaps

---

## 6. Gate 1 lab rig — build plan for funding / partners

**Detailed mechanical / instrumentation plan:** [GATE1-LAB-RIG-DESIGN.md](GATE1-LAB-RIG-DESIGN.md)
(Rig α motion → Rig β combustion → Rig γ electrical; DAQ, safety, 48-cell protocol, CSV schema).

Full acceptance bands: [09-atpe-ers §Gate 1 bench](09-atpe-ers-and-insights.md#gate-1-bench-acceptance-criteria-phoenix-x12-storyboard--lab-targets).

### 6.1 Objective

Build a **single power cartridge** test rig that measures:

- Piston stroke and position (target 50 mm total / ±25 mm opposed)
- Cylinder pressure trace
- Electrical power out (target ≥78 kW peak per cartridge at scale; lower on first rig is OK)
- Bearing runout (target ≤0.03 mm class)
- Fuel flow and exhaust temperature (surrogates validated later)

**Success:** compare measurements to `gate1_bench_at_load()` and update the twin; add row to Gate scorecard measured-results log.

### 6.2 Subsystems (BOM categories)

| Subsystem | Typical contents | Skills |
|-----------|------------------|--------|
| **Mechanical** | Cartridge housing, opposed pistons, bounce chambers, fuel/air/exhaust plumbing | Mech design, machining |
| **Combustion** | Injector, ignition, sensors | Engine development |
| **Bearings** | Magnetic or gas bearings (storyboard: active magnetic) | Specialist supplier |
| **Linear generator** | Stator, mover, rectifier, DC bus | EM design, power electronics |
| **Instrumentation** | Pressure transducer, LVDT/laser, fuel meter, thermocouples, power analyser | Test engineering |
| **Controls & safety** | Real-time shutdown, pressure limits, ventilation | Embedded + functional safety mindset |
| **Data** | DAQ, logging, sync with twin format | Software |

### 6.3 Partner options (if you lack in-house skills)

| Partner type | What they provide | Trade-off |
|--------------|-------------------|-----------|
| **University engine lab** | Test cell, emissions, grad students | Slower; IP agreement needed |
| **Motorsport / niche engine shop** | Fast fabrication, dyno culture | Less academic credibility |
| **National lab / innovation centre** | Grants, equipment | Bureaucracy |
| **Contract design firm** | CAD + FEA | Cost; need clear spec from twin |
| **Magnetic bearing / linear motor vendor** | COTS subsystems | Integration risk |

### 6.4 Indicative budget (bootstrap single cartridge)

| Line | Low | High |
|------|-----|------|
| Design + engineering labour | $80k | $250k |
| Machining & materials | $40k | $120k |
| Instrumentation & DAQ | $30k | $80k |
| Bearings + generator prototype | $50k | $200k |
| Test cell / safety / facilities | $20k | $100k |
| Contingency (30%) | — | — |
| **Total order-of-magnitude** | **$150k** | **$400k** |

Seed-scale ($8–15M) adds multi-cylinder path, flywheel bench, full team — see [05-investor-pitch](05-investor-pitch-and-advantages.md).

---

## 7. Digital twin Phase 2 (parallel with rig planning)

Improve the twin **in service of Gate 1–3**, not as a substitute for building metal.

| Priority | Work | Status | Feeds |
|----------|------|--------|-------|
| 1 | **Virtual bench matrix** — 48-cell sweep, CSV, uncertainty bands | **Done** | Pre-funding evidence, rig test plan |
| 2 | **Virtual Gate 4 layout search** — cylinder mix + X12 ring CSV | **Done** | Pre-ring architecture trade study |
| 3 | **Wire Gate 1 physics to vehicle twin** (`build_gate1_twin()`) | **Done (opt-in)** | Traceable fuel vs fixed tables |
| 3 | **Calibration hook** — load measured CSV → update surrogates → `verify.py` | Backlog | After first rig run |
| 4 | Free-piston stability envelope | **Started** | Gate 2 |
| 5 | Generator efficiency from bench η | Backlog | Gate 3 |
| 6 | Driver intent layer | Backlog | Gate 6 / product |
| 7 | Dashboard: rig vs model overlay | Partial (`?gate1=1`) | Investor demo |

---

## 8. Grant & investor channels (categories)

Verify eligibility and numbers independently before applying.

| Channel | Fit | Notes |
|---------|-----|-------|
| Deep-tech / climate VCs | Seed $8–15M | Need evidence pack + rig plan |
| Automotive corporate VC | Strategic | OEM licensing angle |
| DOE ARPA-E / vehicle programs (US) | Rig + efficiency | Heavy application process |
| Innovate UK / similar | UK-based teams | Often match-funded |
| India PLI / automotive innovation | If India-based | Policy-dependent |
| University translational grants | Bootstrap rig | Smaller checks |

---

## 9. Ninety-day action plan

### Days 1–14 — Package

- [ ] Fill §3 skills & capital worksheet
- [ ] Run `export_evidence_pack.py`, `export_gate1_matrix.py`, and `verify.py`; archive outputs
- [ ] Draft one-pager (§4.2)
- [ ] Book patent counsel intro; start prior-art discussion
- [ ] Build slide deck from §4.3

### Days 15–45 — Outreach

- [ ] 10 targeted investor / grant conversations with demo script (§4.4)
- [ ] 3 university or test-lab conversations for Gate 1 partnership
- [ ] File provisional patent(s) if counsel agrees
- [ ] Twin: virtual bench CSV attached to data room; dashboard Gate 1 trace (`?gate1=1`)

### Days 46–90 — Commit

- [ ] Close angel/grant/bootstrap budget **or** advance Seed process
- [ ] Issue Gate 1 rig RFQ / partnership MOU
- [ ] Order long-lead instrumentation
- [ ] First rig design review against storyboard Scene 4 cutaway
- [ ] Update Gate scorecard measured-results log when anything is built or measured

---

## 10. Document map

| Need | Read |
|------|------|
| Layman status | [PLAIN-ENGLISH-OVERVIEW.md](PLAIN-ENGLISH-OVERVIEW.md) |
| Gate status | [09-atpe-ers §Gate scorecard](09-atpe-ers-and-insights.md#gate-scorecard) |
| Market & funding phases | [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md) |
| Requirements | [04-muv-suv-system-requirements.md](04-muv-suv-system-requirements.md) |
| Twin architecture | [08-digital-twin-design.md](08-digital-twin-design.md) |
| Seed test matrix | [09-atpe-ers §Seed-phase](09-atpe-ers-and-insights.md#seed-phase-bench-test-matrix-months-018) |
| Auto evidence export | `scripts/export_evidence_pack.py` |
| Virtual Gate 4 CSV | `scripts/export_gate4_scaling.py` → `docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv` |

---

*When bench data exists, update the Gate scorecard first, then refresh the evidence pack and slide 6–7 numbers.*
