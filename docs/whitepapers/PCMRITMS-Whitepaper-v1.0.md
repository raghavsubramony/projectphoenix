# PCMRITMS — Phase-Controlled Multi-Ring Inertial Torque Modulation System
## Concept Whitepaper v1.0

**Project Phoenix**  
**Document status:** Conceptual / simulation-validated — no hardware prototype measured  
**Publication date:** July 2026  
**Classification:** External distribution permitted with simulation disclaimer

---

> **Disclaimer:** All performance figures in this document are **engineering projections**
> from physics-based computer simulation unless explicitly marked *measured*. The 75–85%
> round-trip efficiency target is optimistic and unvalidated. Prefix external claims with
> “simulation shows…” Hardware validation is required before any commercial decision.

---

## 1. What It Is

The **Phase-Controlled Multi-Ring Inertial Torque Modulation System (PCMRITMS)** is an
electromechanical buffer placed between a primary energy source (battery, traction motor, or
engine–generator) and the driveline. It stores, shapes, and releases rotational energy on
**millisecond-to-second** timescales using **multiple mechanically independent coaxial
inertial rotors**, each with its own motor–generator and magnetic bearings.

It is an active inertial buffer and torque shaper — **not** a replacement for the primary
power source.

---

## 2. Reference Architecture

- **Independent coaxial rotors (nested rings)** whose speeds are not kinematically locked.
- Each rotor: dedicated magnetic-bearing set + motor–generator stator.
- Reaction torques routed to a **torque-summing stage** on the central output shaft.
- Electrical path: DC bus → per-rotor inverters → MG windings; encoders close the loop per rotor.

### Torque-summing options

| Option | Description | Status |
|--------|-------------|--------|
| A. Epicyclic summing | Each rotor MG stator grounded to carrier; planet/carrier torque sums to output | **Primary reference** (~1–3% gear loss per stage) |
| B. Direct MG reaction | MG stator bolted to summing housing | Alternative |
| C. Magnetic torque coupling | Non-contact rotor to output | Research stage |

**Selected reference:** Option A (epicyclic).

---

## 3. Operational Modes

| Mode | Objective | Behavior |
|------|-----------|----------|
| **Torque boost** | Short acceleration pulse | Rotors discharge stored KE; phases aligned for constructive reaction torque |
| **Energy recovery** | Braking / downhill | Rotors accelerate, absorbing driveline energy |
| **Smoothing** | Reduce ripple | Phases adjusted to cancel oscillatory components |
| **Decoupling** | Protect battery/motor | Primary source held near efficient point; buffer supplies transient deficit |

---

## 4. Representative Sizing (Phase 1, illustrative)

| Parameter | Value |
|-----------|------:|
| Number of rotors | 3 (expandable to 4–6) |
| Per-rotor inertia | 0.10–0.15 kg·m² |
| Operating speed (mean) | ~7,600 rpm (800 rad/s) |
| Stored energy (total) | **118 kJ** (0.118 MJ) |
| Per-rotor MG continuous rating | 30–35 kW |
| Pack electrical rating (3 rotors) | 90–105 kW |
| Continuous discharge (vehicle model) | **90 kW** |
| Brief burst (rotor-coupled, vehicle model) | **140 kW** |
| Target peak torque boost (brief) | +35–50% vs motor-only baseline |
| Boost duration (typical) | 0.2–0.5 s (energy-limited) |
| Energy at full 90 kW discharge | ~1.3 s until depleted |
| Target round-trip efficiency | 75–85% (unvalidated) |
| Modelled round-trip efficiency | **80%** |
| Envelope (automotive auxiliary) | ~40–60 L, 45–70 kg |

---

## 5. Working Principle

Per rotor *i*, electromagnetic torque accelerates the rotor; the **reaction torque** on the
stator/summing stage is the useful output contribution:

**τ_react,i = −I_i × α_i**

Total output torque:

**τ_out = τ_primary + Σ τ_react,i**

The controller selects per-rotor amplitude, modulation frequency, and phase subject to
DC-bus voltage/current limits, maximum speed and acceleration, minimum stored energy, and
thermal limits.

### Hard energy constraint

Peak output torque cannot exceed what stored kinetic energy and electrical input support.
**Sustained** torque multiplication for multiple seconds is not physically realistic at
vehicle packaging. The system delivers **brief, shaped pulses** and driveline-quality
improvement — not continuous overpowering of the traction motor.

### Gyroscopic management

Counter-rotating rotor pairs (opposite mean angular momentum) reduce net angular momentum
versus a single large flywheel while each member still phase-modulates for torque shaping.

---

## 6. Headline Simulation Result

**Lumped-parameter model (3 rotors, losses neglected in torque peak):**

| Rotor parameter | Value |
|-----------------|------:|
| Inertias | 0.12, 0.15, 0.10 kg·m² |
| Mean speed | 800 rad/s |
| Speed modulation amplitude | 80 rad/s |
| Modulation frequency | 18 rad/s |
| Phase separation | 120° |
| Fixed primary torque | 180 N·m |

| Metric | Value |
|--------|------:|
| Baseline torque (motor only) | 180 N·m |
| **Peak combined torque** | **242.8 N·m** |
| **Peak torque increase** | **+34.9%** (brief) |
| Total stored kinetic energy | 0.118 MJ |
| Rotor surge power (vehicle integration model) | 50.2 kW |
| Brief burst rating on DC bus | 140 kW |

*Note: instantaneous electrical demand in the unconstrained model reaches ~305 kW; production
design caps per-inverter ratings (~30–35 kW continuous per rotor), shortening the real boost window.*

---

## 7. Integration with Project Phoenix Powertrain

When coupled to the ATPE + battery series hybrid on an 800 V DC bus:

| Role | PCMRITMS contribution |
|------|----------------------|
| Covers ATPE tier-switch gap (~200–400 ms) | Seamless transient supply |
| Smooths free-piston electrical ripple | Rotor filtering |
| Absorbs acceleration spikes | 118 kJ sprint reserve |
| Refill between spikes | ATPE surplus charges rotors to operating speed |

**Honest finding from vehicle simulation:** smarter timing of flywheel discharge makes **no
measurable difference** on normal driving — the buffer is **energy-limited**, not power-limited.
The sprint reserve depletes before burst power is exhausted.

---

## 8. Simulation Limitations

- No multi-body epicyclic dynamics or gear-mesh stiffness
- No inverter saturation or thermal derating in headline torque model
- No vehicle longitudinal model in standalone rotor validation
- Round-trip efficiency used in vehicle model (80%) is within the stated 75–85% target band
  but is **not measured**

---

## 9. Safety and Integration

- **Safety:** high-speed rotors require burst containment (SAE practices), rotor-burst sensors,
  safe shutdown on bearing or inverter fault.
- **Integration:** parallel (torque-additive) to driveline or series-hybrid node; OEM
  collaboration required for packaging, NVH, and fault diagnostics.
- **Functional safety:** ISO 26262 ASIL assignment to be determined.

---

## 10. Development Roadmap

| Phase | Timeline | Deliverable |
|-------|----------|-------------|
| 1 — Modelling | 0–6 months | Drive-cycle energy benefit with loss models |
| 2 — Single-rotor lab | 6–12 months | One MG + magnetic bearing + dynamometer |
| 3 — Multi-rotor | 12–24 months | Phase control; summing stage; gyroscopic pair |
| 4 — Vehicle demo | 24–36 months | Fleet pilot; durability and containment test |

---

## 11. Summary

PCMRITMS delivers **+34.9% brief peak torque** (242.8 N·m vs 180 N·m baseline) through
phase-coordinated multi-rotor reaction torque summation. In the integrated Phoenix powertrain
it provides a **140 kW brief burst** and **118 kJ** sprint reserve on a shared DC bus —
bridging the gap between slow engine response and fast wheel demand. Hardware must validate
efficiency, containment, and real boost duration before commercial claims.

---

*Companion documents: ATPE Whitepaper v1.0; Project Phoenix Integrated System Whitepaper v1.0.*
