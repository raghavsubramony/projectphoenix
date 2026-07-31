# Gate 1 Lab Rig — Design Plan

*Single-cartridge ATPE bench to validate or falsify the digital twin before multi-cylinder work.*

**Status:** Planning (no hardware in repo)  
**Last updated:** July 2026  
**Audience:** founders, mechanical/powertrain engineers, university lab partners, grant reviewers

Cross-references: [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) §6,
[09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) (Gate 1 acceptance + Seed matrix),
[digital_twin/single_cylinder.py](../digital_twin/single_cylinder.py),
[digital_twin/gate1_matrix.py](../digital_twin/gate1_matrix.py).

---

## 1. Purpose — what this rig is for

The rig exists to answer **one question**:

> Can a single opposed-piston free-piston cartridge achieve the stroke, stability, combustion severity, and electrical output the twin assumes?

It is **not** a vehicle demo, a ring synchronisation test, or a homologation engine. It is the **minimum physical experiment** that converts “simulation shows…” into measured CSV rows the twin can ingest.

### Kill criteria (honest falsification)

| If measured… | Then… |
|--------------|-------|
| Fuel→electrical efficiency ≪ **28%** at sweet spot (2600 rpm, 75% load) | Revisit combustion architecture or tier sizing |
| Brake thermal efficiency ≪ **40%** at sweet spot | Thesis weakens materially — do not scale to ring |
| Peak runout **> 0.05 mm** under combustion | Gate 2 not cleared — bearing/control redesign |
| Cannot hold **50 mm ± 2 mm** stroke envelope | Free-piston control approach fails on hardware |
| Generator η **< 0.90** at rated load | Linear alternator path needs redesign before vehicle claims |

### What the twin already defines

The virtual 48-cell matrix (`scripts/export_gate1_matrix.py`) is the **test protocol**, not marketing:

| Grid axis | Values |
|-----------|--------|
| Speed | 1800, 2200, 2600, 3000 rpm |
| Load | 25%, 50%, 75%, 100% |
| Tier (Phase 1 rig) | **Tier 2 — medium** first (300 cc, 50 mm stroke) |

**Sweet-spot reference (simulation, Tier 2, 2600 rpm, 75% load):**

| Quantity | Twin value | Rig instrument |
|----------|------------|----------------|
| Stroke | 50 mm total | LVDT / laser triangulation |
| Peak electrical power | ~58 kW (× load fraction) | DC power analyser + load bank |
| Electric efficiency (fuel→elec) | ~0.50 (90% CI 0.47–0.52) | Fuel flow + electrical output |
| IMEP | ~4.2 bar | Cylinder pressure integration |
| Bearing runout | ≤0.03 mm (+0.02 mm tol) | Eddy-current displacement probes |
| Core / exhaust temp | surrogates only in twin | Optical pyrometer + exhaust TC |

Run the digital acceptance check before every design review:

```powershell
.venv\Scripts\python.exe -c "from digital_twin import gate1_bench_at_load; r=gate1_bench_at_load(prefer_cantera=False, tier_index=1); print(r.passed, [(c.name, c.measured) for c in r.checks])"
```

---

## 2. Recommended build strategy — three rigs, one lineage

Do **not** jump straight to a full 78 kW combustion + linear-generator cartridge. Historical free-piston programmes failed by combining too many unknowns at once. Phoenix should stage risk:

```
  Rig α (motion)  →  Rig β (combustion)  →  Rig γ (electrical)
       │                    │                      │
   bearings,            pressure,              load bank,
   stroke,              fuel, IMEP,            η_gen,
   control loop         exhaust                48-cell matrix
```

### Rig α — Pneumatic / spring motion bench (bootstrap-friendly)

**Goal:** Prove opposed-piston centre stability and stroke control **without combustion**.

| Item | Detail |
|------|--------|
| Duration | 2–4 months |
| Budget | $30k–$80k |
| Risk removed | Bearing runout, position sensing, bounce-chamber tuning, real-time shutdown |
| Not proven | Combustion, efficiency, generator |

**Configuration:**

- Single combustion-chamber **geometry fixture** (no fuel) or water-filled chamber for mass damping
- Opposed pistons on **magnetic or gas bearings** (or hydrostatic for α-only if COTS magnetic is delayed)
- **Bounce chambers** both ends — tune gas spring to 50 mm peak-to-peak at ~43 Hz (2600 rpm equivalent)
- **Pneumatic assist** optional for startup and amplitude control
- **No crankshaft** — this is the point

**Instruments (minimum):**

| Sensor | Spec hint | Channel |
|--------|-----------|---------|
| Piston position ×2 | LVDT or laser, ±0.01 mm resolution | 10 kHz |
| Bearing gap ×2 | Eddy-current, 0–1 mm range | 10 kHz |
| Chamber pressure | 0–50 bar piezo (even if no combustion yet) | 50 kHz |
| Bus voltage | Safety monitor | 1 kHz |

**Pass (Rig α):**

- Stroke 50 mm ± 2 mm for ≥30 s continuous oscillation
- Peak runout ≤ 0.05 mm at centre (relaxed vs production 0.03 mm)
- Control loop holds frequency within ±5% of setpoint 1800–3000 rpm equivalent

---

### Rig β — Combustion cartridge (no production linear generator yet)

**Goal:** Measure **real** pressure traces, fuel consumption, IMEP, and exhaust temperature on opposed free-piston hardware.

| Item | Detail |
|------|--------|
| Duration | 4–8 months (overlap with late α) |
| Budget | $80k–$200k incremental |
| Risk removed | Combustion phasing without crank, sealing, thermal behaviour |
| Electrical | **Motor-as-generator** on one piston rod *or* hydraulic/absorber dyno for indicated work — acceptable for Gate 1 partial credit |

**Configuration (Tier 2 medium reference):**

| Parameter | Target | Twin source |
|-----------|--------|-------------|
| Displacement | 300 cc total (opposed) | `TIER_DISPLACEMENT_CC[1]` |
| Stroke | 50 mm peak-to-peak | `PHOENIX_X12_STROKE_MM` |
| Compression ratio | 12.5:1 (variable if mechanism ready) | `tier_physics_profile(1)` |
| Moving mass (per side) | ~2.0 kg order | `FreePistonConfig` |
| Bore × stroke implied | ~87 mm bore if full stroke used | derived |

**Subsystems:**

| Subsystem | Notes |
|-----------|-------|
| **Head / valves** | Electronic valve timing (solenoid or pneumatic first); no camshaft |
| **Fuel** | Port or direct injection; mass-flow meter on supply |
| **Ignition** | Coil + ionisation or pressure-derived timing feedback |
| **Exhaust** | Water-cooled collector + TC ring |
| **Lubrication** | Dry liner + oil scraper or charge-lubricated — sealing is a key experiment |
| **Ventilation** | Enclosed test cell, CO/HC monitors, flame arrestor on breather |

**Pass (Rig β):**

- ≥5/6 Gate 1 checks at sweet spot **excluding** production generator η (use indicated work or motoring η instead)
- Stable combustion for ≥100 consecutive cycles without runaway amplitude
- Fuel→electrical or fuel→indicated efficiency recorded — compare to twin band

---

### Rig γ — Integrated linear generator cartridge

**Goal:** Full Gate 1 acceptance on **electrical** output — the Seed-phase deliverable.

| Item | Detail |
|------|--------|
| Duration | 6–12 months from project start |
| Budget | $150k–$400k total (bootstrap) or part of $8–15M Seed |
| Delivers | Steps 1–3 of [Seed bench matrix](09-atpe-ers-and-insights.md#seed-phase-bench-test-matrix-months-018) |

**Configuration:**

- Rig β mechanical core + **permanent-magnet linear alternator** (or separate mover/stator package)
- **Active rectifier** to regulated DC bus (400–800 V class scaled down for lab — 48–400 V OK initially)
- **Programmable load bank** sized for ≥80 kW brief pulses (water-cooled resistors or motor dyno in regen)
- **Capacitor bank** on DC link for ripple absorption (mirrors vehicle bus)

**Pass (Rig γ):**

- ≥5/6 Gate 1 checks at sweet spot including η_gen ≥ 0.95 piston→electrical at rated load
- 48-cell matrix: document pass rate per cell (twin expects 100% in simulation — hardware will not)
- CSV logged in schema §7 → twin calibration hook (future)

---

## 3. Mechanical layout (conceptual)

```
                    ┌── Exhaust collector + TCs ──┐
                    │                             │
   Bounce ◄────────┤   COMBUSTION CHAMBER        ├────────► Bounce
   chamber         │   (opposed pistons)         │          chamber
                    │                             │
                    └── Fuel / air / valves ──────┘
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
      [Linear alternator A]           [Linear alternator B]
      or bearing + rod                (optional dual-side gen)
              │                               │
              └───────────┬───────────────────┘
                          ▼
                   [Active rectifier]
                          ▼
              [DC bus + cap bank + load]
                          │
                   [Power analyser]
```

**Frame:** stiff bedplate (steel weldment or cast aluminium plate), vibration isolation pads, burst shield around chamber.

**Alignment:** opposed-piston **centre dead centre** is software-defined, not crank-defined — optical or capacitive TDC calibration fixture required once per build.

---

## 4. Instrumentation and DAQ

### 4.1 Sensor bill of materials (Rig γ complete)

| # | Measurement | Technology | Rate | Est. cost |
|---|-------------|------------|------|-----------|
| 1 | Cylinder pressure | Piezoelectric + charge amp | 50–100 kHz | $3k–$8k |
| 2 | Piston position ×2 | LVDT or laser | 10 kHz | $4k–$12k |
| 3 | Bearing gap / runout ×2 | Eddy current | 10 kHz | $6k–$15k |
| 4 | Fuel flow | Coriolis or turbine | 10 Hz | $2k–$6k |
| 5 | Air mass flow | Hot-wire or orifice + ΔP | 100 Hz | $2k–$5k |
| 6 | Exhaust gas temp ×4 | K-type TC | 100 Hz | $0.5k |
| 7 | Gas temperature (optional) | Optical pyrometer | 1 kHz | $3k–$10k |
| 8 | DC voltage / current | Hall + shunt or power analyser | 10 kHz | $5k–$25k |
| 9 | Vibration | Accelerometer on bedplate | 10 kHz | $1k |
| 10 | EGT / catalyst brick (later) | TC | — | defer |

### 4.2 DAQ architecture

| Layer | Options |
|-------|---------|
| **Realtime safety PLC** | Dedicated SIL-minded controller (Beckhoff, B&R, or industrial PLC) — **independent** of logging PC |
| **High-speed acquisition** | NI cDAQ / Dewesoft / Opal-RT for pressure + position |
| **Logging PC** | Time-sync all channels (IEEE 1588 or hardware clock) |
| **Format** | CSV per run + TDMS/HDF5 archive; schema in §7 |

**Rule:** the safety PLC must cut fuel and vent pressure on any of: over-pressure, over-stroke, bearing fault, loss of position signal, external estop.

---

## 5. Controls and safety

### 5.1 Control loops (hierarchical)

| Loop | Period | Authority |
|------|--------|-----------|
| **Safety** | <1 ms | Hardwired + PLC — always wins |
| **Stroke / amplitude** | 1–5 ms | Valve timing, pneumatic assist, EM damping |
| **Combustion phasing** | Per cycle | Injection + ignition from predicted TDC |
| **Power / load** | 10–100 ms | Load bank setpoint, bus voltage regulator |
| **Test sequencer** | Human | Sweeps for 48-cell matrix |

### 5.2 Test cell minimum

| Requirement | Notes |
|-------------|-------|
| Fire suppression | CO₂ or clean agent — consult local code |
| Ventilation | ≥6 air changes/min when running |
| Blast shield | Polycarbonate or steel curtain around chamber |
| Fuel storage | Day tank inside cell; bulk outside |
| Gas detection | CO, HC, optional H₂ for pre-mix experiments |
| Ear protection | Free-piston can be loud — NVH mic deferred but hearing PPE mandatory |

---

## 6. Test programme — mapping to the 48-cell matrix

### 6.1 Procedure per cell

For each `(speed, load)` point:

1. Warm stabilisation (coolant / oil if used) — 10 min
2. Set target oscillation frequency from rpm: $f = \mathrm{rpm}/60$
3. Adjust fueling / valve timing to hit load fraction (proxy: IMEP or electrical power vs nameplate)
4. Record 200+ consecutive cycles steady-state
5. Extract: stroke, peak power, η_elec, IMEP, runout, temps
6. Compare to `evaluate_gate1_bench()` bands for that `tier_index` and `load_fraction`

### 6.2 Nameplate power note

Storyboard nameplate for medium tier is **78 kW** per cartridge (`PHOENIX_X12_CARTRIDGE_KW`). The first rig may deliver **30–60 kW** peak while learning — that is acceptable if:

- Results are **scaled honestly** (report kW achieved, not nameplate)
- Efficiency is measured at the achieved load point
- Twin comparison uses the same load fraction definition

### 6.3 Partner / university path

If capital is limited:

| Partner provides | You provide |
|------------------|-------------|
| Test cell, emissions bench, safety sign-off | Cartridge mechanical design, control spec, twin acceptance script |
| Graduate labour | IP agreement, publication review, CSV data ownership |
| Existing dyno infrastructure | Long-lead custom parts (bearings, liner, alternator) |

See [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) §6.3 for partner types.

---

### 6.4 Simulation-derived targets (Phoenix V3)

Validated cartridge physics (July 2026) exports a machine-readable rig spec:

```powershell
py -3 scripts/export_gate1_rig_spec.py
```

Outputs:

- `docs/evidence-pack/GATE1-RIG-SPEC-FROM-V3.md`
- `docs/evidence-pack/GATE1-RIG-SPEC-FROM-V3.json`

Use these as **design targets** for Rig β/γ (stroke, frequency, pressure, η band, instrumentation).
Hardware acceptance remains ≥5/6 Gate 1 checks on **measured** data — the JSON is not a pass certificate.

---

## 7. Data schema (for twin calibration)

When Rig γ produces data, log one row per steady-state point:

```csv
timestamp_utc,tier_index,speed_rpm,load_fraction,stroke_mm,peak_power_kw,electric_efficiency,imep_bar,peak_pressure_bar,bearing_runout_mm,core_temp_c,exhaust_temp_c,generator_efficiency,fuel_rate_g_s,notes
```

**Twin residual hook (live):**

```powershell
# Pipeline prove-out (synthetic lab CSV from twin + noise)
.venv\Scripts\python.exe scripts\import_gate1_rig_csv.py --synthesize data/rig/demo_synthetic.csv

# Real DAQ export
.venv\Scripts\python.exe scripts\import_gate1_rig_csv.py data/rig/run_001.csv
```

Implementation: `digital_twin/gate1_residuals.py` (schema + `measured − twin` residuals).
Store raw runs under `data/rig/` (gitignored). Attach **measured** summary CSV to the evidence pack only after residual review — do not retune surrogates from synthetic rows.

---

## 8. Budget and timeline summary

### 8.1 Bootstrap path (founder / grant / university)

| Phase | Months | Cash | Milestone |
|-------|--------|------|-----------|
| Design freeze + FEA | 0–2 | $15k–$40k | CAD, BOM, safety FMEA |
| Rig α motion bench | 2–5 | $30k–$80k | Stroke + runout demonstrated |
| Rig β combustion | 4–9 | $80k–$150k | Pressure trace + BSFC |
| Rig γ electrical | 8–14 | $50k–$130k | Gate 1 sweet spot measured |
| **Total** | **~12–14** | **$150k–$400k** | Seed-ready data package |

### 8.2 Seed path ($8–15M, parallel)

Adds: full engineering team, Rig γ at 78 kW class, **single-rotor PCMRITMS bench** (separate document — see [10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md) §8), unified control hardware, IP filings.

**Do not integrate PCMRITMS on the first ATPE bed** — validate subsystems separately per [03-integration-viability.md](03-integration-viability.md).

---

## 9. Immediate next actions (30 days)

| # | Action | Owner | Output |
|---|--------|-------|--------|
| 1 | Fill skills & capital worksheet | Founders | [DEVELOPMENT-PLAYBOOK.md](DEVELOPMENT-PLAYBOOK.md) §3 |
| 2 | Choose bootstrap vs university partner | Founders | MOU or RFQ shortlist |
| 3 | Mechanical pre-design | Mech eng | Tier 2 cross-section sketch, bore/stroke trade |
| 4 | Bearing supplier inquiry | Mech / vendor | COTS magnetic bearing quote or gas-bearing fallback |
| 5 | Safety FMEA draft | Controls | Interlock list signed by responsible engineer |
| 6 | Export virtual matrix to attach to RFQ | Twin lead | `scripts/export_gate1_matrix.py` |
| 7 | Provisional patent check before vendor disclosure | Counsel | Filing or NDA pack |

---

## 10. PCMRITMS bench (pointer only)

The inertial buffer is a **second rig**, not a modification of the ATPE bed:

| Item | Spec |
|------|------|
| Deliverable | Single rotor + MG + magnetic bearing on torque transducer |
| Validates | 242.8 N·m peak, +34.9% brief boost, round-trip η |
| Budget | Often $100k–$300k standalone |
| When | After or parallel to Rig α — **not** before ATPE combustion is understood |

Detail: [10-pcmritms-whitepaper-spec.md](10-pcmritms-whitepaper-spec.md) §8 and Seed matrix step 4.

---

## 11. Document map

| Need | Read |
|------|------|
| Acceptance numbers | [09-atpe-ers §Gate 1](09-atpe-ers-and-insights.md#gate-1-bench-acceptance-criteria-phoenix-x12-storyboard--lab-targets) |
| Funding framing | [05-investor-pitch](05-investor-pitch-and-advantages.md) Phase 1 |
| Twin module map | [08-digital-twin-design](08-digital-twin-design.md) |
| Visual target | `designs/` storyboard Scene 4 (cutaway), Scene 8–11 |
| Gate scorecard | [09-atpe-ers §Gate scorecard](09-atpe-ers-and-insights.md#gate-scorecard) |

---

## 12. Digital CAD viewer and twin-linked animation

Parametric rig model on disk — geometry tracks ``tier_physics_profile()``; motion tracks
``gate1_bench_at_load()`` / ``simulate_free_piston()``.

| File | Purpose |
|------|---------|
| [`designs/gate1_rig_geometry.py`](../designs/gate1_rig_geometry.py) | Shared dimensions (mm) for viewer + FreeCAD |
| [`designs/gate1_rig_viewer.py`](../designs/gate1_rig_viewer.py) | **PC viewer** — matplotlib 3D + pressure/position plots |
| [`designs/gate1_rig_freecad.py`](../designs/gate1_rig_freecad.py) | **Solid CAD** — run inside FreeCAD → STEP + FCStd |

**Requires:** `matplotlib` and `numpy`. For an **interactive 3D window** on Windows (when
`tkinter` is missing from your Python build), also install **PyQt6**:

```powershell
uv pip install -r designs\requirements-design.txt
# or: uv pip install PyQt6
```

### Quick start (interactive)

```powershell
py -3 designs\gate1_rig_viewer.py
py -3 designs\gate1_rig_viewer.py --rpm 2600 --load 0.75 --tier 1 --stage gamma --animate
```

### Export for slides / RFQ

```powershell
py -3 designs\gate1_rig_viewer.py --headless --export-png designs\gate1_rig_preview.png
py -3 designs\gate1_rig_viewer.py --headless --animate --export-gif designs\gate1_rig_demo.gif --cycles 3
py -3 designs\gate1_rig_viewer.py --headless --export-obj designs\gate1_rig_lab.obj
```

**Stages:** `--stage alpha` (motion only), `beta` (+ combustion flash), `gamma` (+ coils, load bank, DC bus).

### FreeCAD (optional)

1. Install [FreeCAD](https://www.freecad.org/) 0.21+
2. Macro → open `designs/gate1_rig_freecad.py` → Execute
3. Outputs `designs/gate1_rig_lab.step` and `gate1_rig_lab.FCStd`

---

*When the first run produces CSV data, update the Gate scorecard measured-results log before updating any external fuel or efficiency claim.*
