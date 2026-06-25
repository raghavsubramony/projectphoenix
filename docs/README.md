# Project Phoenix — Documentation

This folder captures the engineering concept, analysis, and business case for an
**integrated adaptive powertrain** combining two novel systems:

- **ATPE** — Adaptive Torque & Power Engine (modular free-piston linear generator with AI cylinder management)
- **PCMRITMS** — Phase-Controlled Multi-Ring Inertial Torque Modulation System (phase-coordinated multi-rotor inertial torque buffer)

> Source: Concept exploration conversation (Claude, shared by Raghav).
> Content includes unverified engineering projections and should be validated with hardware before any commercial decision.

## Document Index

| File | Contents |
|------|----------|
| [PROJECT-STATUS-AND-NEXT-STEPS.md](PROJECT-STATUS-AND-NEXT-STEPS.md) | **Plain-English status report** — where the project stands vs the plan, what is done, what comes next |
| [PLAIN-ENGLISH-OVERVIEW.md](PLAIN-ENGLISH-OVERVIEW.md) | No-jargon tour of Moves A–M and what we proved |
| [01-atpe-concept.md](01-atpe-concept.md) | ATPE concept analysis — architecture, tiers, free-piston generation, AI logic, efficiency, challenges |
| [02-pcmritms-opinion.md](02-pcmritms-opinion.md) | Opinion on the PCMRITMS inertial torque buffer concept |
| [03-integration-viability.md](03-integration-viability.md) | Viability of integrating ATPE + PCMRITMS into one powertrain |
| [04-muv-suv-system-requirements.md](04-muv-suv-system-requirements.md) | Full-stack requirements for a petrol MUV/SUV engine (Phase 1) → performance (Phase 2) |
| [05-investor-pitch-and-advantages.md](05-investor-pitch-and-advantages.md) | Investor estimates, funding roadmap, revenue model, real-world advantages |
| [06-glossary.md](06-glossary.md) | Glossary of acronyms and key terms |
| [07-core-concept-refinements.md](07-core-concept-refinements.md) | Refined, parameterized core concepts (control law, conservation guarantees) |
| [08-digital-twin-design.md](08-digital-twin-design.md) | Digital-twin architecture, physics, metrics, and how to run it |
| [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) | ATPE Engine Requirements Spec (Project PHOENIX) + variable-geometry mechanisms & dev gates |
| [10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md) | Faithful extract of the PCMRITMS whitepaper (architecture, sizing, equations, results) |
| [11-pcmritms-twin-alignment.md](11-pcmritms-twin-alignment.md) | How the whitepaper maps onto the twin + exact reproduction of its headline result |
| [12-architecture-diagrams.md](12-architecture-diagrams.md) | In-repo Mermaid architecture diagrams (power flow, tier stack, cylinder module, rotor layout, control) + engine/cylinder/CAD design approach |

## Digital Twin

A runnable, physics-based **digital twin** of the powertrain lives in
[digital_twin/](../digital_twin) (pure Python, no dependencies). It simulates drive cycles
and reports fuel economy, efficiency, tier activation, buffer/battery states, and emissions.
It also runs **ERS acceptance checks** (pass/fail against the Project PHOENIX P1 targets) and a
**vehicle-body comparison** (the same powertrain in SUV / sedan / hatchback / crossover / pickup /
van bodies).

```powershell
python main.py     # or: py main.py
```

See [08-digital-twin-design.md](08-digital-twin-design.md) for the design, the body-variant table,
and the ERS acceptance harness.

## One-Line Pitch

> "A software-defined powertrain that delivers supercar torque response with hybrid efficiency — without the compromises of either."

## Core Insight

Match displacement to demand at the **cylinder level**, not the engine level — and pair
that with a millisecond-response inertial torque buffer so the engine never has to trade
efficiency for responsiveness. Each subsystem covers the other's biggest weakness.
