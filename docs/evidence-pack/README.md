# Evidence pack (generated)

This folder holds **auto-generated** pitch and grant supporting material from the live
digital twin. Do not hand-edit generated files — regenerate them after model changes.

| File | Generator | Purpose |
|------|-----------|---------|
| `EVIDENCE-BASELINE.txt` | `scripts/export_evidence_pack.py` | Executive summary, ICE benchmark, fault tolerance, virtual Gate 1 + Gate 4 reports |
| `GATE1-VIRTUAL-BENCH-MATRIX.csv` | `scripts/export_gate1_matrix.py` | 48-cell single-cartridge virtual bench matrix |
| `GATE4-VIRTUAL-SCALING.csv` | `scripts/export_gate4_scaling.py` | X4–X16 tier-mix study (phase1 + storyboard kW profiles) |
| `GATE4-TIER-ADVANTAGE.csv` | `scripts/export_gate4_scaling.py` | Single vs two vs three-tier comparison + per-ring 3-tier picks |

```powershell
.venv\Scripts\python.exe scripts\export_evidence_pack.py
.venv\Scripts\python.exe scripts\export_gate1_matrix.py
.venv\Scripts\python.exe scripts\export_gate4_scaling.py
.venv\Scripts\python.exe verify.py
```

**Gate 4 CSV tips:** filter by `ring_name` (e.g. `X8`), `active_tier_count` (1/2/3), or `all_three_tiers=true`. Highway/mixed fuel uses **charge-sustaining SoC (0.55)** — reference `4/2/2` ≈ **4.46 L/100 km** on highway, aligned with `verify.py` and the fleet harness.

**Honesty rule:** all figures are **simulation outputs** unless a row is explicitly marked
measured. Prefix external claims with “simulation shows…”.

Attach the text report and CSV files to investor emails or data rooms alongside the storyboard
image and [DEVELOPMENT-PLAYBOOK.md](../DEVELOPMENT-PLAYBOOK.md).
