# Evidence pack (generated)

This folder holds **auto-generated** pitch and grant supporting material from the live
digital twin. Do not hand-edit generated files — regenerate them after model changes.

| File | Generator | Purpose |
|------|-----------|---------|
| `EVIDENCE-BASELINE.txt` | `scripts/export_evidence_pack.py` | Executive summary, ICE benchmark, fault tolerance, virtual Gate 1 + Gate 4 reports |
| `GATE1-VIRTUAL-BENCH-MATRIX.csv` | `scripts/export_gate1_matrix.py` | 48-cell single-cartridge virtual bench matrix |
| `GATE4-VIRTUAL-SCALING.csv` | `scripts/export_gate4_scaling.py` | X4–X16 tier-mix study (phase1 + storyboard kW profiles) |
| `GATE4-TIER-ADVANTAGE.csv` | `scripts/export_gate4_scaling.py` | Single vs two vs three-tier comparison + per-ring 3-tier picks |
| `GATE6-ATPE-BRAIN-REVIEW.txt` | (architecture review) | Gate-6 systems assessment + investment ranking |
| `GATE6-BACKLOG.txt` | (roadmap) | Backlog 1–6 ADVANCED (physics/rotor/endurance/health/HIL/fleet) |
| `GATE6-PHYSICS-TWIN.txt` | (impl note) | PhysicsTwinBackend lumped fidelity |
| `GATE6-ROTOR-PHASING.txt` | (impl note) | Closed-loop rotor coherence → surge_scale |
| `GATE6-HEALTH-ASSIST.txt` | (impl note) | Health-aware spike threshold + demote |
| `GATE6-ENDURANCE.txt` | `scripts/run_gate6_endurance_study.py` | Multi-pass SoC/health/thermal/R/A |
| `ATPE-BRAIN-VV-001.txt` | `scripts/run_atpe_brain_vv_001.py` | HIL against vehicle ECU runtime + stub |
| `VEHICLE-ECU-RUNTIME.txt` | `scripts/run_vehicle_ecu_smoke.py` | Layer-2 ECU flash ID + watchdog prove |
| `GATE6-FLEET-LEARNING.txt` | `scripts/run_gate6_fleet_learning_demo.py` | OptimizerWeights adaptation only |
| `PCMRITMS-BRAIN-AB-STUDY.txt` | `scripts/run_pcmritms_brain_study.py` | Brain-PCMRITMS on/off fuel A/B (physical buffer always on) |
| `PCMRITMS-BRAIN-COORD-PASS.txt` | (gate review) | **PASS** — fuelEqBuf +6–8%; tow/grade remaining closed |
| `PCMRITMS-BRAIN-STRESS-AB.txt` | `scripts/run_pcmritms_stress_study.py` | Assist/surge stress: transient, tow, low-buf, N-1 |
| `../canvases/digital-twin-pack-snapshot.canvas.tsx` | pack snapshot (Cursor canvas) | Live numbers from full pack run 2026-07-14 |

```powershell
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
.venv\Scripts\python.exe scripts\export_gate4_scaling.py
.venv\Scripts\python.exe scripts\run_pcmritms_brain_study.py --quick
.venv\Scripts\python.exe scripts\run_pcmritms_stress_study.py --quick
.venv\Scripts\python.exe scripts\run_vehicle_ecu_smoke.py
.venv\Scripts\python.exe scripts\run_atpe_brain_vv_001.py
.venv\Scripts\python.exe scripts\run_gate6_fleet_learning_demo.py
.venv\Scripts\python.exe scripts\run_gate6_endurance_study.py --quick
.venv\Scripts\python.exe verify.py
```

**Gate 4 CSV tips:** filter by `ring_name` (e.g. `X8`), `active_tier_count` (1/2/3), or `all_three_tiers=true`. Highway/mixed fuel uses **charge-sustaining SoC (0.55)** — reference `4/2/2` ≈ **4.46 L/100 km** on highway, aligned with `verify.py` and the fleet harness.

**Honesty rule:** all figures are **simulation outputs** unless a row is explicitly marked
measured. Prefix external claims with “simulation shows…”.

Attach the text report and CSV files to investor emails or data rooms alongside the storyboard
image and [DEVELOPMENT-PLAYBOOK.md](../DEVELOPMENT-PLAYBOOK.md).
