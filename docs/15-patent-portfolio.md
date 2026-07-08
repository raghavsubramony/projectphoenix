# Project Phoenix — Patent Portfolio Assessment

**Status:** Internal engineering assessment — **not legal advice**.  
**Action required:** Engage patent counsel before public disclosure, investor roadshows, or OEM meetings.  
**Last updated:** July 2026

Cross-references: [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) §5,
[13-atpe-whitepaper.md](13-atpe-whitepaper.md),
[10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md),
[14-phoenix-integrated-whitepaper.md](14-phoenix-integrated-whitepaper.md).

---

## 1. Executive Answer — How Many Patents?

| Strategy | Provisional applications | Utility patents (after counsel review) | Typical 5-year total* |
|----------|-------------------------:|---------------------------------------:|----------------------:|
| **Minimum viable** | **3** | 3–6 | **6–12** |
| **Recommended (Seed)** | **8–10** | 10–15 | **18–30** |
| **Full defensive portfolio** | **12–15** | 20–30+ | **35–50+** |

\*Includes US utility + selected international (PCT/EPO) filings, divisionals, and continuations.
Counts assume counsel agrees claim themes are novel over prior art.

**Practical recommendation for Project Phoenix today:**

1. File **3 provisionals immediately** (one per protection bucket — see §3).  
2. Expand to **8–10 provisionals** within 6 months as rig CAD and control implementations mature.  
3. Convert to **10–15 utility patents** within 12 months of first provisional.  
4. Budget **$150k–$400k** for the first 18 months of US-focused filing (counsel + USPTO fees; highly variable).

---

## 2. Three Protection Buckets

The portfolio naturally splits into three buckets that map to the whitepaper set:

| Bucket | Whitepaper | What it protects |
|--------|------------|------------------|
| **A — ATPE** | [13-atpe-whitepaper.md](13-atpe-whitepaper.md) | Crankless multi-tier free-piston generation |
| **B — PCMRITMS** | [10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md) | Phase-coordinated multi-rotor inertial buffer |
| **C — System** | [14-phoenix-integrated-whitepaper.md](14-phoenix-integrated-whitepaper.md) | Integrated control, bus arbitration, vehicle method |

Trade secrets (calibration maps, optimal tier mixes from Gate 4 CSV) complement patents — do not
disclose commercially optimal parameters without NDA or filed provisional.

---

## 3. Patent Family Inventory

Each row is one **patent family** (one provisional → one or more utility claims + continuations).
Novelty must be confirmed by professional prior-art search.

### Bucket A — ATPE (6 families)

| # | Family name | Example claim themes | Strength | Doc / code support |
|---|-------------|---------------------|----------|-------------------|
| **A1** | **Multi-tier modular free-piston ring** | Cartridge ring with mixed displacement tiers; additive tier activation; scalable X4–X16 layout | **High** — specific combination | `gate4_scaling.py`, [13-atpe](13-atpe-whitepaper.md) §2 |
| **A2** | **Opposed-piston cartridge with linear generation** | No crankshaft; bounce chamber; integrated alternator per module; hot-swappable cartridge concept | **Medium–High** — geometry + integration | [01-atpe-concept](01-atpe-concept.md), storyboard |
| **A3** | **Electronic combustion control without crank TDC** | Pressure/optical sensing; predicted TDC; async firing across modules | **High** if algorithm-specific | `single_cylinder.py`, Gate 1 bench |
| **A4** | **Variable stroke / compression per cartridge** | Free-piston Atkinson/Miller; electronic valve timing; no camshaft | **Medium** — VC-Turbo prior art exists | [09-atpe-ers](09-atpe-ers-and-insights.md) §3 |
| **A5** | **Energy-weighted multi-tier fuel / power arbitration** | Tier fill from smallest upward; blended efficiency when multiple tiers active | **Medium–High** — specific method | `atpe.py`, `tests/test_atpe.py` |
| **A6** | **Graceful degradation with modular cartridge offline** | Vehicle continues with reduced tier capacity; re-sizing setpoints | **Medium** — fault-tolerance method | `graceful_degradation.py` |

### Bucket B — PCMRITMS (4 families)

| # | Family name | Example claim themes | Strength | Doc / code support |
|---|-------------|---------------------|----------|-------------------|
| **B1** | **Phase-coordinated multi-rotor torque modulation** | Independent coaxial rotors; constructive reaction torque via phase alignment; brief boost | **High** — core novelty | [10-pcmritms](10-pcmritms-whitepaper-spec.md), `pcmritms_rotor.py` |
| **B2** | **Sinusoidal speed-modulation programme** | Per-rotor amplitude, frequency, phase; beat-period torque shaping | **High** — specific control law | Appendix A model, `pcmritms_rotor.py` |
| **B3** | **Epicyclic torque-summing for inertial buffer** | Option A architecture; reaction torque aggregation to output shaft | **Medium** — epicyclic prior art | [10-pcmritms](10-pcmritms-whitepaper-spec.md) §2 |
| **B4** | **Gyroscopic management via counter-rotating pairs** | Opposite mean angular momentum; paired phase modulation | **Medium** | [10-pcmritms](10-pcmritms-whitepaper-spec.md) §5 |

### Bucket C — Integrated system (5 families)

| # | Family name | Example claim themes | Strength | Doc / code support |
|---|-------------|---------------------|----------|-------------------|
| **C1** | **Two-bandwidth buffer arbitration on DC bus** | PCMRITMS owns ms band; battery owns SoC band; ATPE on slow loop | **High** — integration method | [07-core-concept](07-core-concept-refinements.md) §5, `controller.py` |
| **C2** | **Nested control authority (fast buffer wins)** | 1–5 ms buffer loop overrides 10–100 ms generation loop on transients | **High** | `controller.py`, [03-integration](03-integration-viability.md) |
| **C3** | **Rotor-to-bus transient rating coupling** | Peak transient power = continuous + rotor surge from reaction torque × mean speed | **Medium–High** — specific bridge | `pcmritms_coupling.py` |
| **C4** | **Charge-sustaining series hybrid with spike intent** | Pedal-rate proxy; surge gating; SoC target 0.55; EV floor | **Medium** — hybrid prior art dense | `controller.py`, Move A |
| **C5** | **ATPE + inertial buffer complementary powertrain** | System claims: engine never chases transients; buffer refilled from ATPE surplus | **High** — combination claim | [14-phoenix-integrated](14-phoenix-integrated-whitepaper.md) |

### Optional / weaker families (file if budget allows)

| # | Family name | Notes |
|---|-------------|-------|
| **D1** | ML imitation controller for tier selection | Weaker — ML patents crowded; better as trade secret |
| **D2** | Virtual bench acceptance matrix as calibration method | Software/process patent; jurisdiction-dependent |
| **D3** | Driver intent from pedal rate + context | Not fully implemented; file when `DriverIntent` ships |
| **D4** | Right-sized battery discharge for tier stack | Move F insight; may be narrow |

---

## 4. Recommended Filing Sequence

| Step | When | Patents | Action |
|------|------|---------|--------|
| **0** | Now | — | Internal invention disclosure records (date, inventors, diagrams) |
| **1** | Before roadshow | **A1, B1, C1** | Three provisionals — one per bucket (minimum viable) |
| **2** | +30 days | **A3, A5, B2, C2** | Core methods while rig design stabilises |
| **3** | +90 days | **A2, A6, B3, C3, C5** | Mechanical + system combination |
| **4** | +6 months | **A4, B4, C4** | Variable geometry + hybrid modes |
| **5** | Within 12 mo of first provisional | All above → utility | PCT decision with counsel |
| **6** | Post–Gate 1 rig data | Continuations | Claims tightened to measured embodiments |

---

## 5. Count Summary Table

| Category | Families | Recommended provisionals | Utility (est.) |
|----------|:--------:|:------------------------:|:--------------:|
| ATPE (A1–A6) | 6 | 4–6 | 6–10 |
| PCMRITMS (B1–B4) | 4 | 2–4 | 4–6 |
| System (C1–C5) | 5 | 3–5 | 5–8 |
| Optional (D1–D4) | 4 | 0–2 | 0–4 |
| **Total** | **15–19** | **8–10** (Seed) | **18–30** |

**Answer in one line:** Plan for **8–10 provisional patents** at Seed and **18–30 utility patents**
over 3–5 years if the technology validates and funding supports international filing.

---

## 6. Prior Art Risk Areas (counsel must search)

| Area | Known prior art | Phoenix differentiation |
|------|-----------------|------------------------|
| Free-piston engines | Achates, Libertine, Aquarius | Multi-tier ring + linear gen + vehicle integration |
| Cylinder deactivation | GM, Honda, Toyota | **Sized tiers**, not binary on/off |
| Flywheels / KERS | F1, Volvo, some buses | **Phase-coordinated multi-rotor shaping** |
| Series hybrids | BMW i3 REx, Nissan e-Power | ATPE tier architecture + PCMRITMS on same bus |
| Variable compression | Infiniti VC-Turbo | Crankless + per-cartridge VC + stroke |

Combination claims (C5, A1+C1) are often the strongest **if** counsel confirms no single reference
teaches the full integration.

---

## 7. Disclosure Discipline

### Do not publicly disclose without NDA or filed provisional

- Detailed control tuning not already in the public repo  
- Exact commercially optimal tier mixes from Gate 4 CSV  
- Unpublished mechanical drawings with tolerances  
- Rig BOM with supplier part numbers  

### Safe to share (already in repo + whitepapers)

- Architecture diagrams and concept whitepapers (this set)  
- Simulation headlines with “simulation shows…” prefix  
- Gate scorecard and honest gaps  
- Evidence pack CSVs (regenerated from open code)

> **Warning:** Publishing these whitepapers broadly **may constitute public disclosure** in many
> jurisdictions. File provisionals **first** if counsel advises.

---

## 8. Invention Disclosure Checklist (per family)

For each family A1–C5, record internally before filing:

- [ ] Title and one-paragraph summary  
- [ ] Named inventors (everyone who contributed to conception)  
- [ ] Date of conception (earliest lab notebook / commit)  
- [ ] Closest prior art known to inventors  
- [ ] Key figures (architecture diagram, control flow, rig photo when available)  
- [ ] Best mode / preferred embodiment (X12 4/2/2, 3 rotors, 800 V bus, etc.)  
- [ ] Problem solved and unexpected result (e.g. honest “no” on flywheel timing)  

Template: copy §3 table rows into a spreadsheet with columns for date, inventors, figures, status.

---

## 9. Budget Indicators (illustrative, US-focused)

| Item | Low | High |
|------|-----|------|
| Prior-art search (per bucket) | $3k | $8k |
| Provisional (each, with counsel) | $5k | $12k |
| Utility filing (each) | $12k | $25k |
| PCT + national phase (per family) | $30k | $80k |
| **18-month Seed portfolio (8–10 provisionals + 3 utilities)** | **$80k** | **$200k** |

---

## 10. Summary

| Question | Answer |
|----------|--------|
| **Minimum patents to protect core IP?** | **3 provisionals** (ATPE + PCMRITMS + System) |
| **Recommended at Seed fundraise?** | **8–10 provisionals** covering families A1–A3, A5, B1–B2, C1–C3, C5 |
| **Full portfolio over 5 years?** | **18–30 utility patents** (+ continuations if hardware validates) |
| **Distinct invention families identified?** | **15** core + **4** optional = **19** |
| **Urgent action?** | Prior-art search + provisionals **before** wide whitepaper distribution |

*Engage patent counsel. This document supports invention identification only; it does not establish
patentability or freedom to operate.*
