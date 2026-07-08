# Refined Core Concepts (v2)

This document sharpens the original ATPE + PCMRITMS concept into a set of **precise,
parameterized engineering definitions** that the digital twin implements directly. Where the
original docs were qualitative, this version commits to numbers, control laws, and interfaces.

See also: [08-digital-twin-design.md](08-digital-twin-design.md) for how these map to code.

## 1. System Decomposition

The powertrain is a **series-hybrid electric architecture** with four energy domains connected
by a high-voltage DC bus:

```
 Chemical ──► ATPE ──► Electrical ──► DC Bus ──► Traction ──► Kinetic
 (petrol)   (gen)      (variable AC          (motor)      (vehicle)
                        → rectified DC)
                              ▲  ▼
                      ┌───────┴──┴───────┐
                      │  Buffers (fast)   │
                      │  • PCMRITMS rotor │  (kinetic, ms response)
                      │  • LFP battery    │  (electrochemical, s response)
                      └───────────────────┘
```

**Key refinement:** treat the buffers as a *two-bandwidth* energy store. The PCMRITMS owns the
high-frequency band (transients < ~2 s), the battery owns the low-frequency band (state-of-charge
management over minutes). The ATPE is a quasi-steady generator that should almost never chase
transients directly — that is the whole efficiency argument.

## 2. ATPE — Parameterized Definition

Each cylinder **tier** is defined by a tuple
$(\,n,\ V_d,\ P_{\text{elec}}^{\max},\ \eta_{\text{th}},\ f_{\text{osc}}\,)$:

| Tier | Units $n$ | Disp $V_d$ (cc) | Max elec (kW, total) | Thermal eff $\eta_{th}$ | Osc. freq (Hz) | Combustion |
|------|-----------|-----------------|----------------------|--------------------------|----------------|------------|
| 1 (micro)  | 4 | 100 | 30  | 0.44 | 60–80 | HCCI / lean |
| 2 (medium) | 2 | 300 | 80  | 0.41 | 45–60 | DI spark, Miller |
| 3 (large)  | 2 | 750 | 120 | 0.38 | 30–45 | DI spark + cooled EGR |

**Generation model.** Active tiers are filled from smallest upward until demand is met. Each tier
$i$ contributes electrical power $P_i$ at fuel power $P_{fuel,i} = P_i / \eta_i$; total fuel power
is $\sum P_{fuel,i}$ (energy-weighted blend). Because pistons are free, each tier holds its *own*
optimal operating point, so $\eta_i$ is treated as flat across that tier's active band (the central
refinement vs. a crank engine's wide, lossy BSFC island).

**Tier selection** is discrete and additive: the controller picks the smallest tier set whose
combined $P_{\text{elec}}^{\max}$ covers the generation setpoint. `active_tier` telemetry reports
the governing (largest active) tier.

## 3. PCMRITMS — Parameterized Definition

Modeled as a bounded **kinetic energy reservoir** with two-sided power limits and a split
round-trip efficiency:

| Parameter | Symbol | Value (Phase 1) |
|-----------|--------|-----------------|
| Usable stored energy | $E_{\max}$ | 118 kJ |
| Max discharge power | $P_{out}^{\max}$ | 90 kW (3 × 30) |
| Max charge power | $P_{in}^{\max}$ | 90 kW |
| Round-trip efficiency | $\eta_{rt}$ | 0.80 |
| Charge / discharge eff | $\sqrt{\eta_{rt}}$ | ≈ 0.894 each |

**Refinement — the honest constraint:** at full 90 kW discharge the reservoir empties in
$E_{\max}/P_{out}^{\max} \approx 1.3\text{ s}$. The twin enforces this so the "torque boost"
can never be oversold; sustained boost *must* be backed by the ATPE or battery.

## 4. Battery — Parameterized Definition

| Parameter | Value |
|-----------|-------|
| Usable capacity | 20 kWh (72 MJ) |
| Chemistry | LFP |
| Max discharge | 120 kW |
| Max charge (regen + ATPE) | 80 kW |
| Charge-sustaining SoC target | 0.55 |
| EV-only floor SoC | 0.25 |

## 5. Unified Control Law (the heart of the refinement)

The controller runs as **three nested loops** with strict authority ordering. At each step it
receives the DC-bus power demand $P_d$ (positive = traction, negative = regen).

**Loop A — Predictive / energy budget (slow, 100–500 ms):** choose operating *mode*.
- `EV` if $\text{SoC} >$ floor **and** filtered demand $\bar P_d <$ EV threshold (30 kW) → ATPE off.
- `CS` (charge-sustaining) otherwise → ATPE on.

**Loop B — ATPE generation setpoint (10–100 ms):** a low-pass + SoC-correction law

$$P_{gen}^{*} = \underbrace{\text{LPF}_{\tau}(P_d^{+})}_{\text{smoothed load}} \;+\; \underbrace{k_{soc}\,(\text{SoC}_{tgt}-\text{SoC})}_{\text{charge sustain}}$$

clamped to $[0,\ \sum P_{\text{elec}}^{\max}]$ and then quantized to a tier set. $\tau \approx 5$ s
deliberately keeps the engine *off* the transients.

**Loop C — Buffer arbitration (fast, 1–5 ms):** the instantaneous mismatch
$\Delta = P_d - P_{gen}$ is allocated by priority:
1. **PCMRITMS first** (fastest, no chemical wear) — supply/absorb up to its power & energy limits.
2. **Battery second** — cover the remainder within its limits.
3. **Clip & flag** — any residual unmet demand is recorded as a *capability shortfall* (the twin never silently fabricates energy).

Surplus generation ($P_{gen} > P_d$) flows in reverse priority: **refill PCMRITMS to target
first**, then charge the battery.

## 6. Conservation Guarantees (what makes it a *twin*, not a cartoon)

The simulation enforces, every timestep:
- **Power balance:** $P_{gen} + P_{batt} + P_{buffer} = P_d + P_{losses}$ (residual ⇒ flagged shortfall).
- **Energy bookkeeping:** fuel energy in = $\int \sum_i (P_i/\eta_i)\,dt$ over active tiers; buffer/battery energies integrate their net flows with efficiency penalties applied on every transfer.
- **Bounded states:** SoC ∈ [0,1], buffer energy ∈ [0, $E_{\max}$], never violated.

These invariants are the difference between a marketing animation and an engineering model.

## 7. Phase-2 Scaling Hooks

The same parameter tuples scale to the performance variant by swapping the config table
(larger Tier 3, more rotors, NMC battery, four motors). No control-law change — only numbers —
which validates the "modular from the start" claim from
[03-integration-viability.md](03-integration-viability.md).
