# Project Phoenix

A physics-based **digital twin** of an integrated adaptive powertrain that pairs two
novel subsystems:

- **ATPE** — Adaptive Torque & Power Engine: a modular free-piston linear generator
  whose cylinder tiers are switched on demand (matches displacement to load at the
  *cylinder* level, not the engine level).
- **PCMRITMS** — Phase-Controlled Multi-Ring Inertial Torque Modulation System: a
  phase-coordinated multi-rotor inertial buffer that delivers millisecond torque
  bursts so the engine never trades efficiency for transient response.

The model is a **series hybrid**: the ATPE generates onto a DC bus, a two-bandwidth
buffering scheme (fast PCMRITMS inertial buffer + slow battery) absorbs transients and
manages state of charge, and energy is conserved every step.

## Quick start

The runtime package (`digital_twin/`) is **pure Python standard library** — no
third-party dependencies.

```powershell
# Run the full demonstration (drive cycles, ERS acceptance, body comparison,
# motor sizing, motor sweep, PCMRITMS reproduction):
.venv\Scripts\python.exe main.py

# One-command verification: re-derive every headline number from the live code,
# check it against its validated value, and run the full test suite:
.venv\Scripts\python.exe verify.py

# Run the regression test suite:
.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Executive summary — the one-table front door

`main.py` opens with a single dashboard that gathers the load-bearing number
from every analysis layer (Moves A–I) into one place
(`digital_twin/summary.py`, `build_executive_summary()`):

```
  Per-body headlines (same powertrain, six bodies):
  Body          Fuel   Cost/km  (5-95%)        CO2   Batt    WLTP  Season
                L/100  EUR/km   EUR/km        g/km   kW/C    L/100   swing
  ------------------------------------------------------------------------
  AWD SUV       2.70   0.073  [0.056-0.091]  104.6   90/4.5   6.22  13.9%
  Sedan         0.67   0.041  [0.031-0.053]   47.6   60/3.0   3.96  22.4%
  Hatchback     0.57   0.039  [0.030-0.052]   44.9   60/3.0   3.64  22.8%
  Crossover     1.15   0.048  [0.041-0.062]   61.0   70/3.5   4.96  20.5%
  Pickup        4.03   0.094  [0.078-0.128]  142.0   60/3.0   8.06  15.2%
  Van / MPV     3.06   0.079  [0.067-0.110]  114.9   60/3.0   6.76  18.0%

  Fleet-wide headline facts:
    PCMRITMS rotor      : 242.8 N.m peak (+34.9%), 50.2 kW surge lifts the buffer to 140 kW brief burst
    Battery longevity   : 0 replacements over vehicle life; embodied CO2 4.6% of lifecycle (SUV)
    Climate robustness  : thermal derate from -10C to +40C? no; regulatory shortfalls: 0
```

Each column is the headline of one layer: **Fuel/Cost/CO₂** (lifecycle
economics), the **5–95% band** (joint-uncertainty Monte-Carlo), **Batt kW/C**
(right-sized discharge power), **WLTP** (standardized-cycle economy) and
**Season** (the −10 °C → +40 °C fuel swing). Every figure is the *median of an
honest band*, not an optimistic point — and `verify.py` confirms the summary
echoes the same validated numbers.

## What the twin does

| Capability | Where |
|---|---|
| Drive-cycle simulation (fuel, CO₂, efficiency, tier activation, SoC) | `digital_twin/simulation.py` |
| ERS acceptance checks (pass/fail vs Project PHOENIX P1 targets) | `digital_twin/acceptance.py` |
| Six vehicle bodies (SUV / sedan / hatchback / crossover / pickup / van) | `digital_twin/config.py` |
| Per-body recommended motor sizing + 60–250 kW motor sweep | `digital_twin/acceptance.py` |
| Fleet harness — every body × every cycle, for incremental A/B data | `digital_twin/fleet.py` |
| Lifetime cost & cradle-to-grave CO₂ (Move D) | `digital_twin/economics.py` |
| Right-sizing the battery discharge power (Move F) | `digital_twin/sizing.py` |
| Monte-Carlo uncertainty bands (Move G) | `digital_twin/montecarlo.py` |
| Ambient temperature stress, −10 °C to +40 °C (Move H) | `digital_twin/ambient.py` |
| Standardized WLTP / EPA cycle reconstructions (Move I) | `digital_twin/regulatory_cycles.py` |
| One-table executive summary across every layer | `digital_twin/summary.py` |
| PCMRITMS rotor model (reproduces the whitepaper headline) | `digital_twin/pcmritms_rotor.py` |
| Rotor → buffer coupling (rotor physics sets a real buffer rating) | `digital_twin/pcmritms_coupling.py` |

## Validated invariants (locked by `tests/`)

- **PCMRITMS rotor:** 180 N·m → **242.8 N·m peak (+34.9%)**, 0.118 MJ stored — exact
  whitepaper reproduction.
- **Rotor coupling:** peak reaction torque 62.8 N·m × 800 rad/s = **50.2 kW surge**,
  raising the buffer's brief-burst discharge from 90 kW to **140 kW**.
- **All six bodies pass all** class-appropriate ERS targets (light/mid bodies on a
  ~150 kW motor; heavy SUV/pickup on 160 kW).
- **AWD SUV highway = 4.62 L/100 km**; urban is fully electric across the fleet.
- **Zero shortfalls** on every standard cycle.

## Key data finding: rotor coupling shapes peaks, not energy

The rotor-derived burst rating is **opt-in** (`rotor_coupled=True`) and defaults off, so
existing numbers never regress. Across the fleet A/B on standard cycles the coupling
produced **no change** in fuel, CO₂, buffer throughput, or net battery energy — because
raising the discharge cap without enlarging the reservoir delivers the **same energy at
higher power for a shorter time**. A direct transient probe confirms the mechanism: against
a 130 kW spike the baseline buffer clips to 90 kW while the coupled buffer covers the full
130 kW for 0.8 s. Its value is **sub-second peak-shaving / battery-current sparing**, not
steady-state fuel economy.

## Proving the concept under realistic transients

A real free-piston generator cannot ramp instantly, so the ATPE carries an optional
generation slew limit (`ATPEConfig.max_slew_w_per_s`, default off). Under that honest
constraint, a **transient-stress** cycle of back-to-back hard launches shows the buffer
earning its keep:

- **Buffer burst engages:** coupled buffer delivers up to **+50.2 kW** more (AWD SUV,
  Pickup, Van) — its full ~140 kW rotor-coupled rating.
- **Battery current spared:** that burst is drawn from the buffer, cutting battery
  throughput by **21–55 kJ** per stress cycle on the heavy bodies (pack-longevity benefit),
  monotone with body mass.
- **Honest limit:** the coupling adds *power, not energy* — on a depleting reservoir it
  shifts shortfall timing rather than eliminating it.
- **Scaling converts burst → capability:** modelling the whitepaper's 3 → 6 rotor growth
  (more burst **and** more stored energy) under a cold 40 kW battery cuts unmet launch
  energy from 1095 kJ to **493 kJ (−55%)**. The reservoir size, not the burst rating, sets
  sustained capability.


## Documentation

See [docs/](docs/) for the full concept, requirements, whitepaper extract, and the
digital-twin design. Start with [docs/README.md](docs/README.md).

> Content includes unverified engineering projections and should be validated with
> hardware before any commercial decision.
