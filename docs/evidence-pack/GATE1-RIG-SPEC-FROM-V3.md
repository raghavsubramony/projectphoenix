# Gate 1 Lab Rig — Specification from Phoenix V3 Simulation

*Generated: 2026-07-08T13:40:46Z · tuning: `phoenix_v3_best_tuning_v3.json`*

## Cartridge geometry (Rig β / γ)

| Parameter | Target |
|-----------|--------|
| Bore | 96.0 mm |
| Peak-to-peak stroke | 50.0 mm |
| Operating frequency | 42.0 Hz (23.8 ms/cycle) |
| Peak chamber pressure | ≤ 118 bar (sim) |
| Generator rated force | 7059 N per piston |
| Capture lockout BDC | ≥ 13 mm before full extraction |

## Performance targets

| Metric | Simulation | Hardware expectation |
|--------|------------|---------------------|
| Fuel → electrical η | **54.0%** | 36%–46% |
| Generator capture | 50.3% of power-stroke expansion | — |
| Electrical power (1 cart) | 19.8 kW | scale to achieved load |
| Ring power (12×, sim) | 237 kW | Gate 4 scope |

## Cooling

Long-run thermal sustainability at ~54% output requires **`water_jacket`** in simulation. Passive cooling fails within minutes at this output level.

## First-rig acceptance (maps to virtual 48-cell matrix)

- ≥ **100** consecutive stable cycles without runaway amplitude
- ≥ **5/6** Gate 1 bench checks at sweet spot (`load_fraction` ≈ 0.95)
- Measured η within **12 percentage points** of sim band on first build
- Log CSV per §7 of [GATE1-LAB-RIG-DESIGN.md](../GATE1-LAB-RIG-DESIGN.md)

## Instrumentation minimum

1. Cylinder pressure (piezo, ≥50 kHz)
2. Piston position ×2 (LVDT, ≥10 kHz)
3. Fuel mass flow
4. DC bus V/I or power analyser
5. Exhaust gas temperature
6. Generator winding / coolant temperature

Machine-readable spec: `GATE1-RIG-SPEC-FROM-V3.json`
