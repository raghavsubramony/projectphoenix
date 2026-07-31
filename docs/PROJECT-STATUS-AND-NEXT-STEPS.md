# Project Phoenix — Where We Stand and What Comes Next

*A plain-English status report for anyone who wants the full picture without reading code or engineering jargon.*

**Last updated:** July 2026  
**How we know nothing is broken:** run `.venv\Scripts\python.exe verify.py` — reports **PASS (63 checks + 169 tests)** when the design-stack deps are present (`numpy` for Phoenix V3 / ATPE Brain); use `verify.py --quick` for a ~10 s smoke subset (headline checks only).

---

## 1. What is this project?

Imagine you want to design a **new kind of hybrid car engine** before spending millions on metal, labs, and prototypes. Project Phoenix is a **computer simulation** — a “digital twin” — of that powertrain. It obeys real-world rules: weight, air resistance, tyre friction, battery limits, and fuel burn.

The powertrain has three main parts:

| Part | Simple analogy | What it does |
|------|----------------|--------------|
| **ATPE** (the engine) | A very smart petrol generator | Burns fuel to make electricity. It does **not** turn the wheels directly. |
| **Battery** | A medium electricity tank | Stores energy, helps on short trips, smooths demand. |
| **PCMRITMS** (the flywheel buffer) | A spinning “sprint reserve” | Delivers a fast burst of power for hard acceleration, then the engine catches up. |

A **controller** (the brain) decides every fraction of a second who supplies power and who stores it.

We test the same technology on **six vehicle types**: SUV, sedan, hatchback, crossover, pickup, and van — so we see how it behaves on a small car versus a heavy truck.

**Important honesty rule:** every headline number in this project must come from the simulation itself. If an idea does not help in the model, we say so. A clear “no” is as valuable as a “yes.”

---

## 2. What was the plan?

The work was organised in two layers: **software “Moves”** (A through M) and **hardware “Gates”** (1 through 7).

### Software Moves — one realism layer at a time

Each Move adds one new question the simulation can answer. Every Move is:

- **Optional** — it cannot silently break numbers we already trust.
- **Tested** — automated checks lock the result so it cannot drift later.
- **Shown in the demo** — `main.py` walks through the findings.

| Move | Plain question | Status |
|------|----------------|--------|
| **A** | Can the controller learn from data instead of only fixed rules? | **Done** |
| **B** | Does the battery heat up and wear out like a real one? | **Done** |
| **C** | Which inputs (weight, drag, etc.) actually matter for fuel? | **Done** |
| **D** | What does ownership really cost, and what is total lifetime CO₂? | **Done** |
| **E** | Can we spend the flywheel’s “sprint” more cleverly? | **Done** (honest “no” on normal driving) |
| **F** | How big do the parts really need to be? | **Done** |
| **G** | How sure are we? (error bars on every headline) | **Done** |
| **H** | What about winter and summer (−10 °C to +40 °C)? | **Done** |
| **I** | How does it do on official-style test drives (WLTP / EPA)? | **Done** |
| **J** | What about a cold engine on a cold morning? | **Done** |
| **K** | What if the car is full of people and luggage? | **Done** |
| **L** | What if you plug it in to charge from the grid? | **Done** |
| **M** | How does the car change after 250,000 km of ageing? | **Done** |

**Bottom line for software Moves:** **A through M are all built, tested, and demonstrated.** The simulation phase of the plan is complete for Phase 1.

### Hardware Gates — from idea to physical prototype

These are the real-world milestones from the engineering requirements document:

| Gate | What it means | Status |
|------|---------------|--------|
| **1** | Single-cylinder computer model | **Partially (software)** — virtual 48-cell bench matrix passes in simulation; combustion surrogate + opt-in physics path; **hardware not measured** |
| **2** | Stable free-piston operation (physics of the piston) | **Started in software** — RPM-linked cycle timing; bearing-runout surrogate; **hardware not started** |
| **3** | Linear generator integrated | **Not started in hardware** — efficiency numbers are assumed, not measured |
| **4** | Multiple cylinders working together | **Started in software** — virtual layout sweep + X12 ring scaling; **hardware not started** |
| **5** | Whole vehicle integration | **Done in software** — six bodies, drive cycles, pass/fail targets |
| **6** | AI optimisation | **Advanced in software** — `atpe_brain/` supervisor + PCMRITMS coordinator PASS; Layer-2 `ecu/` runtime; not vehicle-certified |
| **7** | Mechanical CAD and physical parts | **Not started** — diagrams exist; no 3D manufacturing models yet |

**Bottom line for hardware:** we are roughly at **“Gate 5 in software only.”** The next big step is **bench prototypes and measured data**, not more spreadsheet modelling.

### Business / funding roadmap (from investor materials)

This is separate from the simulation Moves:

| Phase | Timeline (illustrative) | Goal |
|-------|-------------------------|------|
| **Seed / Pre-Series A** | Months 0–18 | Single-cylinder engine lab rig + single-rotor flywheel bench test |
| **Series A** | Months 18–36 | Full multi-cylinder engine + integrated flywheel on a test rig; **mule vehicle** |
| **Series B** | Months 36–60 | Production-intent vehicle; homologation; durability testing |

**None of these hardware phases have started** in this repository. This repo is the **digital evidence base** for whether the idea is worth building.

---

## 3. Where we stand today — the honest scorecard

### What is finished and trustworthy

1. **A working digital twin** — pure Python, no paid dependencies, runs on a laptop.
2. **All six vehicle bodies pass their performance targets** (acceleration, top speed, hill climbing, efficiency) when sized correctly.
3. **The flywheel (PCMRITMS) whitepaper headline is reproduced** in software (torque boost, stored energy).
4. **Every software Move A–M** has code, tests, a section in `main.py`, and checks in `verify.py`.
5. **A one-command proof** (`verify.py`) re-derives headline numbers from live code so silent drift is caught.
6. **A web dashboard** (`python -m dashboard`) lets you pick a vehicle and drive cycle and watch the simulation play back.
7. **An executive summary table** at the start of `main.py` rolls up Moves A–M plus ICE benchmark and fault tolerance.
8. **A virtual Gate 1 bench matrix** — 48 operating points (speed × load × tier), CSV export, uncertainty bands, and vehicle fuel traced to per-cartridge physics (`digital_twin/gate1_matrix.py`).
9. **A virtual Gate 4 layout search** — every tier mix at **X4, X6, X8, X10, X12, X14, X16** under phase1 and storyboard kW profiles (`digital_twin/gate4_scaling.py`).
10. **ATPE Brain + vehicle ECU (software)** — Gate 6 supervisory brain (`atpe_brain/`) and Layer-2 ECU reference runtime (`ecu/`, `firmware/c/`); Phoenix V3 ring plant in `designs/phoenix_v3/`.

### What the simulation has proven (in plain language)

- **The architecture is internally consistent.** Energy is accounted for every timestep. Claims are reproducible.
- **The battery is the quiet hero.** It is small (20 kWh), runs cool, barely wears out in normal use, and **never needs replacing** over the car’s life in the model. Its manufacturing carbon is a small slice of total lifetime emissions.
- **Capability comes from stored energy and battery power**, not from flashy peak power alone. Three separate studies (sensitivity, smart flywheel timing, and right-sizing) all reached the same conclusion.
- **The default battery power is over-specced** — most vehicles could use **25–50% less** battery power and still handle hard launches. That means potential cost and weight savings.
- **Seasonal and real-world wrinkles are understood:** winter costs more fuel; hot days do not overheat the pack in normal driving; a full load adds fuel but does not break hill-climbing ability; cold starts only hurt on trips where the engine actually runs; plugging in only wins on CO₂ when the electricity grid is clean.
- **Official-style test cycles** (WLTP / EPA reconstructions) give similar rankings to our home-made routes — so earlier conclusions were not an artefact of fake roads.
- **Some ideas were tested and rejected honestly:** smarter flywheel “sprint timing” makes **no difference** on normal driving because the flywheel runs out of **energy** before it runs out of **burst power**.

### What is *not* finished (gaps between simulation and reality)

| Gap | Why it matters |
|-----|----------------|
| **No physical prototypes** | All efficiency, noise, vibration, packaging, and safety numbers are **modelled**, not measured. |
| **Combustion is simplified** | Virtual bench passes in simulation; real flame, valves, and measured piston motion still need lab data to calibrate surrogates. |
| **AI controller is a study, not a product** | A learning experiment exists; it is not the same as a certified vehicle brain. |
| **Regulatory cycles are reconstructions** | They match published distance and speed envelopes; they are **not** the copyrighted official second-by-second traces. |
| **20-scene visual pipeline not built** | Storyboard exists; reproducible scene generation deferred until bench data or funding demo need. |

**Disclaimer (already in project docs):** content includes **unverified engineering projections**. Hardware validation is required before any commercial decision.

---

## 4. What we built — a simple map of the repository

| Folder / file | What a non-engineer should know |
|---------------|----------------------------------|
| `digital_twin/` | The physics engine — vehicle, engine, battery, flywheel, controller (Phase-1 **4/2/2** stack) |
| `digital_twin/gate1_matrix.py` | Virtual single-cartridge bench — 48-cell test matrix, CSV export, investor report |
| `digital_twin/gate4_scaling.py` | Virtual multi-cylinder layout search — best cartridge mix, X12 ring sweep, CSV |
| `designs/phoenix_v3/` | Phoenix V3 ring plant (Gate 4/5 freeze candidate **4/6/2 X12** — different layer from the vehicle twin) |
| `atpe_brain/` | Gate 6 supervisory AI brain (setpoints only — does not drive actuators directly) |
| `ecu/` | Layer-2 vehicle ECU runtime — rate-limits, watchdog, fail-OFF safe-state |
| `firmware/c/` | C skeleton for microcontroller flash (same bus contract as `ecu/`) |
| `scripts/export_gate4_scaling.py` | Regenerate `docs/evidence-pack/GATE4-VIRTUAL-SCALING.csv` |
| `scripts/export_evidence_pack.py` | One-command pitch/grant evidence baseline (text + CSV) |
| `ml_study/` | Move A imitation study — controller learns from simulation data |
| `main.py` | Long demonstration that prints every study’s results |
| `verify.py` | Green-light button: “are all headline numbers still correct?” |
| `dashboard/` | Web page to run one simulation and see charts (`?gate1=1` for Gate 1 overlay) |
| `tests/` | 169 automated tests that must pass (V3/brain path needs design-stack `numpy`) |
| `docs/` | Concept papers, requirements, and plain-English guides |

---

## 5. Key numbers a layperson might care about

These are **model outputs**, not road-test certificates.

**AWD SUV (main reference vehicle), charge-sustaining highway fuel:** about **4.46 L/100 km**  
**Mixed cycle vs conventional 2.0 L turbo (same car):** about **2.35 vs 3.26 L/100 km** (~**28%** less fuel)  
**Urban driving:** effectively **electric** (near-zero fuel) when the battery is charged  
**Battery replacements over 250,000 km:** **zero** in the model  
**Right-sized battery discharge power (SUV):** about **90 kW** vs **120 kW** default — room to downsize  
**Winter-to-summer fuel swing:** roughly **12–23%** depending on body style (SUV ~12%)  
**Lifetime CO₂ (SUV, all-in):** about **102 g/km** in the model  
**After 250,000 km of ageing:** electric range fades about **23%**; fuel use creeps up modestly in absolute terms

**July 2026 model note:** ATPE now uses energy-weighted tier fill when multiple cylinder sizes fire together (see `digital_twin/atpe.py`). Headline fuel figures improved ~3–4% vs the prior governing-tier shortcut.

Every one of these sits inside an **uncertainty band** (Move G) — the single numbers are the **middle** of a range, not a best-case cherry-pick.

---

## 6. What needs to happen next

Think of this as three tracks: **polish the software**, **deepen the model where it matters**, and **build hardware**.

### Track A — Software polish (weeks, low cost)

| Task | Status | Why |
|------|--------|-----|
| **`--quick` smoke mode** on `verify.py` | **Done** | Headline checks only; unit tests skipped (~10–15 s) |
| Document for stakeholders | Ongoing | This document + [PLAIN-ENGLISH-OVERVIEW.md](PLAIN-ENGLISH-OVERVIEW.md) + [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) |

*Most of this is communication and convenience, not new science.*

### Track B — Next simulation depth (months, still laptop-scale)

Several Track B items are **done** for the no-lab-funding path; the remainder stays on the backlog:

| Task | Status | Plain benefit |
|------|--------|----------------|
| **Virtual Gate 1 bench matrix** | **Done** | 48-cell speed/load/tier sweep, pass/fail bands, CSV for spreadsheets |
| **Virtual Gate 4 layout search** | **Done** | X4–X16 tier mixes × 2 kW profiles; CSV with `tier_mix_kind` column |
| **Wire bench physics to vehicle twin** | **Done (opt-in)** | `build_gate1_twin()` traces fuel to per-cartridge combustion |
| **Gate 1 uncertainty bands** | **Done** | Sweet-spot Monte Carlo on efficiency, power, IMEP |
| **Evidence pack export** | **Done** | `export_evidence_pack.py` + `GATE1-VIRTUAL-BENCH-MATRIX.csv` |
| **Driver intent** | Backlog | Understand *how* the driver presses the pedal, not just *how much* |
| **Variable compression / stroke per tier** | Backlog | Trade efficiency vs power inside the engine model |
| **Calibrate surrogates from measured rig data** | Backlog | Feed lab CSV into `gate1_bench_at_load()` when hardware exists |
| **Tighter learned-AI loop** | Backlog | Ensure the learned controller’s decisions match what the hardware actually does |

*Default validated fuel figures still use tier-efficiency tables; Gate 1 physics is opt-in so headline numbers do not drift.*

### Track C — Hardware and business (the real milestone path)

This is what turns a convincing model into a **credible product story**:

| Step | Deliverable |
|------|-------------|
| **1. Single-cylinder ATPE lab rig** | Measured efficiency and stability data |
| **2. Single-rotor PCMRITMS bench** | Measured burst torque and round-trip efficiency |
| **3. Integrated electrical bench** | Engine + flywheel + battery on one shared high-voltage bus |
| **4. Mule vehicle** | Existing SUV platform with the new powertrain swapped in |
| **5. Emissions and durability testing** | Real regulatory and 300,000 km equivalent evidence |
| **6. CAD / packaging (Gate 7)** | Physical fit, weight, safety containment for the flywheel |
| **7. IP and partnerships** | Patents, OEM conversations, funding per Phase 1–3 roadmap |

**The simulation’s job is done enough to support step 1–2 planning.** The critical path now is **measured prototypes**, not more Moves in the twin.

---

## 7. Recommended priority order

If you are deciding what to do Monday morning:

1. **Run `verify.py` before any demo or investor meeting** — it is the integrity seal.
2. **Treat the executive summary (Moves A–M / N) as the elevator pitch** — it is the validated core story.
3. **Use Moves J–M when someone asks “yes, but what about winter / luggage / plugging in / old age?”** — those answers exist; they are just later in `main.py`.
4. **Do not claim road-test or certification results** — say “simulation shows…” and point to `verify.py`.
5. **For funding:** use the **virtual bench dossier** (`export_evidence_pack.py`, CSV matrix) plus the narrative that **measured** bench data is the Seed-phase deliverable.
6. **For engineering:** when hardware exists, **calibrate** surrogates from rig CSV; until then the virtual matrix defines acceptance tests before metal is cut.

---

## 8. How anyone can check our work

No special tools beyond Python:

```powershell
# Full integrity check (63 headline checks + 169 tests)
.venv\Scripts\python.exe verify.py

# Full story walkthrough (long; use --quick for a faster run)
.venv\Scripts\python.exe main.py
.venv\Scripts\python.exe main.py --quick

# Virtual Gate 1 bench matrix (CSV)
.venv\Scripts\python.exe scripts\export_gate1_matrix.py

# Virtual Gate 4 layout sweep (CSV)
.venv\Scripts\python.exe scripts\export_gate4_scaling.py

# Investor / grant evidence pack (text + CSV)
.venv\Scripts\python.exe scripts\export_evidence_pack.py

# Interactive dashboard in a browser
.venv\Scripts\python.exe -m dashboard
```

If `verify.py` prints **RESULT: PASS**, the locked headline numbers and tests agree with the current code.

---

## 9. One-paragraph summary for a non-technical audience

> Project Phoenix has built a honest computer model of a new hybrid powertrain and stress-tested it across six vehicle types, four seasons of temperature, official-style drive tests, full loads, cold starts, grid charging, and vehicle ageing. **All thirteen planned simulation studies (Moves A–M) are complete and automatically tested.** A **virtual Gate 1 bench matrix** (48 cartridge operating points, CSV export, uncertainty bands) provides digital bench evidence before lab funding. The model supports the core story: a small, long-lasting battery plus a flywheel sprint reserve plus an efficient generator can deliver strong performance with low lifetime cost and carbon — if the hardware matches the assumptions. **What has not been done is building and measuring real parts.** The next step is laboratory prototypes whose measured results can confirm or correct the model, then a mule vehicle. Until then, treat every number as a well-disciplined engineering projection, not a road-test fact.

---

## Related documents

| Document | Audience |
|----------|----------|
| [PLAIN-ENGLISH-OVERVIEW.md](PLAIN-ENGLISH-OVERVIEW.md) | Detailed walkthrough of Moves A–M and conclusions |
| [README.md](README.md) | Documentation index |
| [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) | Technical requirements, gates, and Move write-ups |
| [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md) | Funding phases and market framing |
| [08-digital-twin-design.md](08-digital-twin-design.md) | How the twin is structured (more technical) |
| [13-atpe-whitepaper.md](13-atpe-whitepaper.md) | ATPE concept whitepaper (publication-ready, simulation-qualified) |
| [14-phoenix-integrated-whitepaper.md](14-phoenix-integrated-whitepaper.md) | Integrated powertrain whitepaper + twin evidence |
| [15-patent-portfolio.md](15-patent-portfolio.md) | Patent family map and filing recommendations |

---

*This status document is meant to stay readable. When the project advances — especially after hardware measurements — update the scorecard in Section 3 and the priority list in Section 7.*
