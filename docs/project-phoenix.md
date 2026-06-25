# Project Phoenix (c:\Users\c52444a\Desktop\project Phoenix)

## What it is
Concept + digital twin for an integrated automotive powertrain:
- ATPE = Adaptive Torque & Power Engine (free-piston linear generator, AI cylinder tiers)
- PCMRITMS = Phase-Controlled Multi-Ring Inertial Torque Modulation System (multi-rotor inertial torque buffer)

## Python environment (IMPORTANT)
- Project uses `uv`; venv at `.venv\`.
- The `py` launcher does NOT use the venv. To run with venv deps use:
  `.venv\Scripts\python.exe`
- `python`/`python3` on PATH are Windows Store stubs (python not found / wrong env).
- pyproject.toml has zero runtime deps; keep digital_twin pure stdlib.
- pypdf was installed into the venv (via `uv pip install pypdf`) only for one-off PDF extraction.

## Structure
- `digital_twin/` pure-stdlib package: config, vehicle, atpe, pcmritms, battery, controller, powertrain, drive_cycles, simulation
- `ml_study/` pure-stdlib ONLINE ML study for the Unified-AI policy: features (varying data ranges), scaling (MinMax/Welford), labels (tier decision EV/T1/T2/T3 = active_index+1), dataset (CSV), models (single-sample SGD softmax + linear regressor), collect (gathers from twin), study (LearningStudy + run_study). Learning increment = 1 (one sample per partial_fit); prequential accuracy ~94%. Hook: LearningStudy.predict_decision(obs)->(tier,gen_w).
- `docs/` 01..NN concept + design docs (README.md is the index)
- `docs/12-architecture-diagrams.md` = in-repo Mermaid diagrams (power flow, tier stack, free-piston module, rotor layout, control loops, data flow) + "Engine & Cylinder Design Approach" section.

## Design/tooling decisions (agreed)
- Python twin is single source of truth. Do NOT port to MATLAB/Simulink.
- Architecture diagrams: Mermaid in repo (done, doc 12). Real mechanical CAD = Gate 7, later (FreeCAD python-scriptable -> SolidWorks/Fusion).
- NO torque converter by design: series hybrid, electric drive; PCMRITMS inertial buffer fills the torque-smoothing role electrically.
- Engine/cylinder design path: (1) parametric in Python (add per-tier BSFC map + free-piston mass-spring dynamics to make abstracted Gates 1-2 real), (2) combustion via Cantera (pure-Python, optional like ml_study), (3) FEA only when a number needs it: FEMM->Ansys Maxwell (linear alternator), Ansys Mechanical (rotor ring burst at 800 rad/s), (4) CAD last via FreeCAD python API from same config dataclasses. Specialist tools feed PARAMETERS into the twin; never fork the model.
- `main.py` demo runner (ends with Unified-AI learning study section). Supports `--quick` (reduces Monte Carlo/uncertainty/ML); expensive blocks wrapped in `_section()` ctx mgr for failure isolation.
- Shared `digital_twin.fleet.stress_unmet_launch_kj(coupled, energy_scale)` used by main demo + RotorScalingTest (no duplicated logic).
- `Battery.derate_factor()` is the PUBLIC method (renamed from `_derate_factor`).
- `simulation.run` reports `duration_s = n*dt` (n = #records), consistent with distance/fuel integration (was off-by-one (n-1)*dt).
- LearnedController: classifier only gates EV vs CS; the REGRESSOR's watt setpoint drives the ATPE tier (active_index). Test: test_ml_study.test_regressor_drives_hardware_tier.
- economics.tco_for_body recovers pack cycle-life rating from FleetCells (not hardcoded 4000).
- dashboard /api/tests guarded by `_TEST_RUN_LOCK` (429 if busy). tests/test_dashboard.py covers API (never hits /api/tests success path = would recurse).
- `dashboard/` pure-stdlib web dashboard (http.server): `python -m dashboard` (flags --host/--port/--no-open). server.py exposes /api/options, /api/simulate?cycle&body&coupled, /api/tests (runs unittest via _JSONResult). Frontend static/ (index.html, style.css, app.js): instrument-cluster speedo + power-flow bars + SoC tubes + scrolling trace canvas that plays back the per-step telemetry; Test Bench tab renders pass/fail cards. No third-party deps. Use `.venv\Scripts\python.exe -m dashboard`.

## Key PCMRITMS whitepaper specs (Whitepaper-Complete.pdf)
- 3 rotors (expandable 4-6), per-rotor inertia 0.10-0.15 kg·m² (I=[0.12,0.15,0.10])
- mean speed 800 rad/s (~7600 rpm), stored energy 118 kJ
- per-rotor MG 30-35 kW, pack ~90-105 kW, round-trip 75-85% target
- boost +35-50% for 0.2-0.5 s (energy-limited); headline sim: 180->242.8 N·m (+34.9%)
- epicyclic torque summing (Option A reference), ~1-3% gear loss/stage
