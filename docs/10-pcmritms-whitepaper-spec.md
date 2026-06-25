# PCMRITMS — Whitepaper Specification (faithful extract)

Source: **"Phase-Controlled Multi-Ring Inertial Torque Modulation System for Enhanced Driveline
Performance," Version 1.0 — Concept Whitepaper** (`Whitepaper-Complete.pdf`, 12 pp.).
Status per document: *Conceptual / simulation-validated, no hardware prototype.*

This document records the whitepaper's actual specifications and claims verbatim-in-substance,
so the digital twin and the opinion in [02-pcmritms-opinion.md](02-pcmritms-opinion.md) are
anchored to the primary source rather than paraphrase.

## 1. What It Is

An electromechanical **buffer** placed between a primary energy source (battery + traction
motor, or engine–generator) and the driveline. It stores, shapes, and releases rotational
energy on **millisecond-to-second** timescales using **multiple mechanically independent
coaxial inertial rotors**, each with its own motor–generator (MG) and magnetic bearings. It is
an active inertial buffer and torque shaper — **not** a replacement for the primary power source.

## 2. Reference Architecture

- **Independent coaxial rotors (nested rings)** whose speeds are *not* kinematically locked.
- Each rotor: own **magnetic-bearing** set + dedicated **MG stator**.
- Reaction torques routed to a **torque-summing stage** coupled to the central output shaft.
- Electrical path: `battery → DC bus → per-rotor inverters → MG windings`; encoders close the loop per rotor.

### Torque-summing options
| Option | Description | Reference? | Notes |
|--------|-------------|-----------|-------|
| A. Epicyclic summing | Each rotor MG stator grounded to a carrier; planet/carrier torque sums to output | **Primary reference** | Mature; predictable torque addition; ~1–3% gear loss per stage |
| B. Direct MG reaction | MG stator bolted to summing housing | Alternative | Fewer gears; needs stiff housing + thermal design |
| C. Magnetic torque coupling | Non-contact rotor→output torque | Research | No contact wear; air-gap control & fault tolerance hard |

**Selected reference:** Option A (epicyclic) — well understood in hybrid transmissions, allows
independent rotor speeds with a single mechanical output.

## 3. Operational Modes
| Mode | Objective | Behavior |
|------|-----------|----------|
| **Torque boost** | Short acceleration pulse | Rotors discharge stored KE; phases aligned for constructive reaction torque |
| **Energy recovery** | Braking / downhill | Rotors accelerate, absorbing driveline energy |
| **Smoothing** | Reduce ripple | Phases adjusted to cancel oscillatory components |
| **Decoupling** | Protect battery/motor | Primary source held near efficient point; buffer supplies transient deficit |

## 4. Representative Sizing (illustrative auxiliary unit — *not* a committed spec)
| Parameter | Value |
|-----------|-------|
| Number of rotors | 3 (expandable to 4–6) |
| Per-rotor inertia | 0.10–0.15 kg·m² |
| Operating speed (mean) | ~7,600 rpm (800 rad/s) |
| Stored energy (total) | ~0.12 MJ (118 kJ) |
| Per-rotor MG continuous rating | 30–35 kW |
| Pack electrical rating (3 rotors) | ~90–105 kW |
| Target peak boost (brief) | +35–50% vs. motor-only baseline |
| Boost duration (typical) | 0.2–0.5 s (energy-limited) |
| Target round-trip efficiency | 75–85% (to be validated; incl. MG, bearings, gears, power electronics) |
| Envelope (automotive aux.) | ~40–60 L, 45–70 kg |

## 5. Working Principle (governing equations)

Per rotor $i$, electromagnetic torque accelerates the rotor; the **reaction torque** on the
stator/summing stage is the useful output contribution:

$$\tau_{\text{react},i} = -I_i\,\dot\omega_i = -I_i\,\alpha_i$$

Total output torque (buffer added in parallel/series with the main path $\tau_{\text{primary}}$):

$$\tau_{\text{out}} = \tau_{\text{primary}} + \sum_i \tau_{\text{react},i}$$

With independent **phase-controlled** velocity programs per rotor, the controller selects
amplitude $A_i$, modulation frequency $\Omega_i$, and phase $\phi_i$ subject to DC-bus
voltage/current limits, max $\omega$ and $\dot\omega$, minimum stored energy, and thermal limits.

### Hard energy/power constraint (the honesty clause)
Peak output torque cannot exceed what stored kinetic energy and electrical input support.
**Sustained** torque multiplication (e.g., 8–10× for seconds) is explicitly *not* physically
realistic at vehicle packaging. The system is for **brief, shaped pulses** and driveline-quality
improvement, not continuous overpowering of the traction motor.

### Gyroscopic management
Counter-rotating rotor **pairs** (opposite mean angular momentum) reduce net angular momentum
vs. a single large flywheel, while each member still phase-modulates for torque shaping. Does
not eliminate gyroscopic effects under all maneuvers.

## 6. Headline Simulation Result (Appendix A model)

Lumped-parameter Python/NumPy model:
- 3 rotors, $I=[0.12,\ 0.15,\ 0.10]$ kg·m²
- mean speed 800 rad/s; sinusoidal speed modulation $A=80$, $\Omega=18$ rad/s, **120° phase separation**
- fixed primary torque $\tau_{\text{primary}}=180$ N·m
- losses (epicyclic, bearing, inverter) neglected (⇒ optimistic torque, efficiency not back-calculated)

| Metric | Value |
|--------|-------|
| Baseline torque (motor only) | 180 N·m |
| Peak combined torque | **242.8 N·m** |
| Peak torque increase | **+34.9%** (brief) |
| Total stored kinetic energy | 0.118 MJ |
| Approx. peak instantaneous electrical demand | ~305 kW* |

\*Instantaneous sum of per-rotor electrical power; a production design caps commands to
per-inverter ratings (~30–35 kW continuous per rotor), which shortens the real boost window.

## 7. Stated Limitations of the Current Simulation
- No multi-body/epicyclic dynamics or gear-mesh stiffness
- No inverter saturation or thermal derating
- No efficiency map or round-trip energy accounting
- No vehicle longitudinal model (mass, tire, grade)
- Next step: co-simulate with vehicle dynamics + loss models; cap peak power to per-inverter ratings; predict energy benefit on WLTC / CARB bus cycles

## 8. Development Roadmap (whitepaper)
| Phase | Timeline | Deliverables |
|-------|----------|--------------|
| 1 — Modeling | 0–6 mo | MBD with epicyclic dynamics; efficiency map; drive-cycle energy benefit |
| 2 — Single-rotor lab | 6–12 mo | One MG + magnetic bearing + dynamometer; validate torque & losses |
| 3 — Dual/triple rotor | 12–24 mo | Phase control; summing stage; gyroscopic pair demo |
| 4 — Vehicle demo | 24–36 mo | Bus or truck pilot; durability + containment test |

## 9. Safety / Integration Notes
- **Safety:** high-speed rotors need burst containment (SAE practices), rotor-burst sensors, safe shutdown on bearing/inverter fault.
- **Integration:** parallel (torque-additive) to driveline or series-hybrid node; needs OEM collaboration for packaging, NVH, fault diagnostics; ISO 26262 ASIL assignment TBD.
- **IP novelty focus:** independent coaxial multi-rotor *phase coordination* for driveline torque shaping + specific summing architectures. Prior-art search/patent filing recommended before public disclosure.

## 10. How This Maps to the Digital Twin
See [11-pcmritms-twin-alignment.md](11-pcmritms-twin-alignment.md) for the parameter alignment
and the reproduction of the 242.8 N·m / +34.9% headline inside our twin
([digital_twin/pcmritms_rotor.py](../digital_twin/pcmritms_rotor.py)).
