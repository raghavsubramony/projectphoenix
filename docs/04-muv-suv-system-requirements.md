# Complete System Requirements: Integrated ATPE + PCMRITMS

## MUV/SUV (Phase 1) → Performance Engine (Phase 2)

**Design constraints chosen:** Fuel = **Petrol/Gasoline**; Market = **Global multi-market**.

## Strategic Framing

Petrol + global multi-market is the hardest regulatory target — you must satisfy **EPA, Euro 7,
BS6+, and China 6b simultaneously**. That constraint shapes every design decision below.

---

## LAYER 1: Combustion & Generation Core (ATPE)

### Free-Piston Module Design for Petrol

Petrol's lower compression ratio (10:1–13:1 vs diesel's 16:1–22:1) and knock sensitivity change earlier assumptions.

**Tier 1 Micro Cylinders — Petrol-Specific**
- 80–120 cc per unit, 2–4 units
- HCCI combustion mode (extremely load/temperature sensitive — requires closed-loop combustion pressure sensing in every cylinder)
- Operating window: light cruise, urban crawl, generator recharge
- Target: 42–45% thermal efficiency at this narrow optimal band

**Tier 2 Medium Cylinders**
- 250–350 cc per unit, 2 units
- Conventional port or direct-injection spark ignition
- Miller/Atkinson cycle achievable with free-piston variable stroke (a genuine advantage over crank engines)
- Operating window: highway cruise, moderate load
- Target: 38–42% thermal efficiency

**Tier 3 Large Cylinders**
- 600–900 cc per unit, 2 units
- Direct injection with cooled EGR for knock suppression
- Port water/methanol injection as supplemental knock control under peak load
- Fires only during hard acceleration, grades, towing — active <10% of drive time in MUV/SUV use
- Target: 36–40% thermal (acceptable — brief use, battery + buffer supplement)

### What Needs to Be Engineered Fresh

**Linear Generator Design** (not off-the-shelf):
- High-flux permanent-magnet array on piston crown
- Laminated stator core wound for high-frequency output (pistons oscillate at 30–80 Hz)
- Output is raw AC at variable frequency → dedicated rectifier per cylinder module
- Target continuous output per Tier 2 cylinder: 15–25 kW electrical

**Piston Return Mechanism — Petrol Critical Issue:** Petrol combustion is faster/more violent
than diesel. **Opposed-piston architecture** (two pistons per cylinder firing toward each other)
is strongly recommended:
- Eliminates bounce-chamber complexity
- Naturally balanced — opposing momentum cancels vibration
- Both pistons can carry magnet arrays, doubling generator output per cylinder

**Combustion Sealing:** No crankshaft oil seal in linear motion. Needs:
- Non-contact labyrinth seals between combustion and generator sections
- Magnetic seal assist to prevent combustion gas migration into generator cavity
- A real manufacturing challenge — no production solution exists today

---

## LAYER 2: Electrical Architecture

### DC Bus Design

```
ATPE Generators (variable AC)
    → Per-cylinder rectifiers
    → 800V High Voltage DC Bus
    → Bidirectional inverter bank
    → PCMRITMS rotor drives (3× ~30kW inverters)
    → Traction motor inverter
    → 12V/48V auxiliary bus (via DC-DC converter)
    → Battery pack (buffer + energy storage)
```

**Why 800V not 400V:**
- Higher voltage = lower current = thinner, lighter cables throughout
- Faster buffer rotor recharge (critical for PCMRITMS refill between spikes)
- Aligns with where global EV infrastructure is heading — future-proofs the platform
- Penalty: 800V requires SiC (Silicon Carbide) inverters throughout — more expensive but more efficient

### Battery Pack Requirements

Serves three functions: energy buffer between generation and demand spikes; EV-only range for
short trips; cold-start energy source before ATPE reaches operating temperature.

**Recommended sizing: 15–25 kWh usable**
- Not a full BEV pack — cost and weight controlled
- 60–90 km EV-only range (meets global PHEV incentive thresholds)
- **LFP (Lithium Iron Phosphate)** chemistry preferred — better thermal stability, longer cycle life, lower cost/kWh, handles high charge/discharge rates from ATPE variable output better than NMC

### Power Electronics Package — New Engineering Required

Six distinct power-conversion stages need coordination:
1. Per-cylinder AC→DC rectifiers (6–8 units)
2. DC bus voltage regulation
3. Three PCMRITMS rotor inverters (bidirectional)
4. Main traction motor inverter
5. Battery BMS with bidirectional charging
6. Auxiliary 48V bus converter

These cannot be separate boxes — thermal density and packaging demand a **unified power
electronics module with shared cooling**. No commercial off-the-shelf solution exists for this combination.

---

## LAYER 3: The Unified AI Control System

The most critical and novel engineering element. Neither system works well without it.

### Two-Loop Hierarchy

**Fast Loop — PCMRITMS (1–5 ms cycle)**
- Reads: wheel torque demand, vehicle speed, road gradient, driver input rate-of-change
- Controls: rotor phase, amplitude, discharge rate
- Authority: absolute on transients — overrides everything else
- Hardware: dedicated FPGA or real-time DSP

**Slow Loop — ATPE Cylinder Management (20–100 ms cycle)**
- Reads: battery SoC, buffer rotor energy state, thermal conditions, fuel consumption, emissions sensors
- Controls: which tier fires, injection timing, air-fuel ratio, EGR rate
- Authority: energy-budget management — keeps the system fed
- Hardware: automotive-grade ECU (AUTOSAR-compliant for global homologation)

**Coordination Layer — Predictive Arbitration (100–500 ms)**
- Reads: navigation data (upcoming grades, junctions), driver behavior pattern, traffic
- Predicts demand 3–10 s ahead; pre-charges buffer rotors; pre-activates cylinder tiers before needed
- Makes the system feel seamless rather than mechanical
- Hardware: SoC with ML inference (Qualcomm Snapdragon Ride or equivalent)

### Control Inputs Required

| Sensor | Purpose | New engineering? |
|--------|---------|------------------|
| Per-cylinder pressure transducer | Combustion TDC detection, knock sensing | Yes — motorsport grade |
| Linear piston position encoder | Free-piston location at microsecond resolution | Custom required |
| Per-rotor speed/phase encoder | PCMRITMS phase coordination | Adapted from existing |
| DC bus voltage/current (×8 nodes) | Power flow management | Yes |
| 6-axis IMU | Grade, lateral G, pitch/roll for gyroscopic compensation | Yes |
| Exhaust lambda sensor (wideband) | Air-fuel ratio per cylinder tier | Yes |
| Exhaust temperature (per tier) | Thermal management, catalyst protection | Yes |
| Navigation/ADAS feed | Predictive demand horizon | Yes — integration work |

---

## LAYER 4: Emissions System — Global Multi-Market Critical

Petrol + global market creates the most engineering work. Euro 7 and China 6b are currently most stringent.

### Cold Start Problem — Acute for This Architecture

Free-piston engines have no thermal-mass flywheel, so they reach operating temperature faster — an advantage. But:
- Tier 1 HCCI cylinders cannot fire until coolant reaches ~60 °C (HCCI is temperature-sensitive)
- Cold start must use Tier 2 spark-ignition cylinders only
- Battery powers traction motor during the ~90-second warm-up window
- Electrically heated catalyst (EHC) mandatory — battery powers it directly at start

### Catalyst Architecture Required

```
Tier 1 exhaust → Close-coupled TWC (Three-Way Catalyst)
Tier 2 exhaust → Close-coupled TWC + GPF (Gasoline Particulate Filter)
Tier 3 exhaust → TWC + GPF + underfloor secondary TWC
All tiers → merge → Common lambda control
```

**Why separate catalyst paths matter:** Tiers activate/deactivate independently. If all
exhaust merged before the catalyst, an inactive Tier 3 would push cold air through the
catalyst during Tier 1-only operation, killing catalyst light-off temperature. Each tier needs
its own close-coupled catalyst that stays hot when that tier is active.

### OBD-III Compliance

Global markets increasingly require real-world emissions monitoring (not just lab cycles). The
AI control system must log/report per-cylinder combustion quality, catalyst temperature and
conversion efficiency, and real-world NOx/PM in-use. Software work, but significant.

---

## LAYER 5: Mechanical Integration Package

### Packaging for MUV/SUV

**Option A — Flat Underbody Module**
- ATPE cylinders horizontal in floor tunnel; PCMRITMS ahead of rear axle; traction motors at each axle (AWD native)
- Advantage: lowers center of gravity significantly
- Disadvantage: ground-clearance conflict for SUV use — needs careful packaging

**Option B — Front Transverse Power Unit**
- ATPE cylinders in two opposing banks (like a linear boxer); PCMRITMS directly behind, inline; single traction motor
- Advantage: familiar layout for service and homologation
- Disadvantage: tight packaging, more NVH transmission to cabin

### NVH Engineering — Significant New Work

Free-piston linear motion creates reciprocating forces with no inherent rotational balance. Required:
- Active vibration cancellation using PCMRITMS rotors as tuned mass dampers (a secondary function)
- Hydraulic engine mounts with variable stiffness
- Acoustic enclosure around the ATPE module
- This is where PCMRITMS earns extra value — it can cancel vibration signatures from tier switching, solving a problem unique to ATPE

### Thermal Management System

Unified liquid cooling loop serving: ATPE cylinder heads and generator windings; PCMRITMS
rotor bearing cooling; power electronics module; battery pack thermal conditioning; cabin
heating (waste-heat recovery — a significant advantage over pure EVs). Single loop with smart
valve routing — not separate circuits. More complexity, but weight and pump count saved.

---

## LAYER 6: Vehicle Integration Requirements

- **Transmission:** None needed. Traction motor torque is full from 0 RPM. Single-speed reduction gear per axle. PCMRITMS handles torque shaping a multi-speed gearbox previously provided — a significant simplification and cost saving.
- **AWD System:** Traction motors at both axles → software-defined torque vectoring with millisecond precision; better than any mechanical AWD. No transfer case required.
- **Towing Capability:** MUV/SUV class demands 2,000–3,500 kg tow rating. Sustained towing is the hardest case — ATPE Tier 3 must maintain output 30–60 minutes continuously, which defines Tier 3 thermal limits more than any other use case.
- **Brake Regeneration:** PCMRITMS rotors capture braking energy first (fastest response), then battery captures remainder. Two-stage regen is more efficient than battery-only because rotors charge at higher power density than the battery accepts.

### Body Variants on the Shared Powertrain (twin-derived)

The same Phase-1 powertrain (ATPE stack + inertial buffer + battery + 150 kW traction motor) can
be dropped into different bodies — only the chassis changes. The digital twin
([08-digital-twin-design.md](08-digital-twin-design.md) §"Vehicle-Body Variants") quantifies how the
identical engine performs per body. The **AWD SUV is the primary target**; the rest are reference points:

| Body | Mass (kg) | 0–100 (s) | Top (km/h) | Grade @100 (%) |
|------|----------:|----------:|-----------:|---------------:|
| **AWD SUV** (primary) | 2200 | 8.2 | 192.6 | 20.5 |
| Sedan | 1650 | 5.8 | 228.6 | 30.0 |
| Hatchback | 1400 | 4.8 | 226.8 | 36.0 |
| Crossover | 1850 | 6.7 | 208.8 | 25.5 |
| Pickup | 2500 | 9.9 | 169.2 | 16.5 |
| Van / MPV | 2300 | 8.6 | 183.6 | 19.5 |

The 0–100 spread (4.8 s hatch → 9.9 s pickup) is pure body physics on one engine; the heavier SUV
body is *why* the twin's ERS 0–100 check lands at 8.2 s rather than the sub-6 s a lighter body
achieves. See [09-atpe-ers-and-insights.md](09-atpe-ers-and-insights.md) §9.

---

## Phase 1 → Phase 2 Transition: MUV/SUV to Performance

| Element | MUV/SUV (Phase 1) | Performance (Phase 2) |
|---------|-------------------|------------------------|
| Tier 3 cylinders | 600–900cc, 2 units | 900–1200cc, 4 units |
| PCMRITMS rotors | 3 rotors, 118 kJ | 5–6 rotors, 200+ kJ |
| Battery | 15–25 kWh LFP | 5–10 kWh NMC (lighter, power-focused) |
| Traction motors | 2× 150 kW | 4× 200 kW (one per wheel) |
| Peak system output | ~350–450 kW | 700–900 kW |
| 0–100 km/h | ~8.2 s (2200 kg SUV, twin) · ~5.8 s sedan body | ~2.5–3.2 s |
| Control loop | 5 ms fast loop | 1 ms fast loop |
| Gyroscopic management | Paired counter-rotation | Active precession compensation |

The performance version doesn't redesign the system — it **scales** it. That's the
architectural advantage of building modular from the start.

---

## What Needs Invention vs. What Can Be Adapted

| Element | Status |
|---------|--------|
| Free-piston opposed linear generator | Needs invention — no production equivalent |
| Multi-tier AI cylinder management | Needs invention — novel software |
| Unified power electronics module | Needs significant adaptation |
| PCMRITMS multi-rotor assembly | Needs invention — per whitepaper |
| 800V SiC inverter bank | Adapt from existing EV suppliers |
| LFP battery pack | Off-the-shelf with custom BMS |
| Traction motors | Adapt from existing EV programs |
| Emissions catalyst system | Adapt with custom packaging |
| Active magnetic bearings | Adapt from aerospace/industrial |
| Predictive AI coordination layer | Needs invention — novel integration |

---

## Realistic Timeline to MUV/SUV Production-Ready

| Phase | Timeline | Milestone |
|-------|----------|-----------|
| Component validation | 0–18 months | Individual subsystems bench-tested |
| Subsystem integration | 18–36 months | ATPE + PCMRITMS on shared test bench |
| Mule vehicle | 36–48 months | Running in development vehicle |
| Regulatory validation | 48–60 months | Global emissions homologation |
| Production engineering | 60–72 months | Manufacturing processes defined |
| SOP (Start of Production) | ~6 years | First retail vehicles |

Six years is aggressive but achievable with dedicated funding. A conventional new-engine
program typically takes 4–5 years — this is roughly one additional cycle for the novel
integration work.

The MUV/SUV platform is the right first vehicle: it tolerates the weight, justifies the
premium, and its use case — mixed urban/highway, occasional towing, all-weather AWD —
exercises every capability of the integrated system in real-world conditions.
