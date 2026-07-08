"""Phoenix V3 — 12-cartridge radial ring simulation.

Each cartridge is a full opposed-piston integrator with even phase spacing
on the shared intake ring.  Electrical output is summed on a common DC bus;
thermal derating on any cartridge reduces that cartridge's contribution.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_simulation import (  # noqa: E402
    OUT_DIR,
    PhoenixV3Config,
    PhoenixV3Simulator,
    SimulationResult,
    apply_generator_cooling,
    print_energy_audit,
    print_report,
)


@dataclass(frozen=True)
class RingConfig:
    """Radial engine layout — default 12 cartridges at even phase spacing."""

    cartridge_count: int = 12
    shared_bus_voltage_v: float = 48.0


@dataclass
class CartridgeSnapshot:
    """Per-cartridge summary from a ring run."""

    index: int
    phase_offset_ms: float
    result: SimulationResult
    mean_elec_power_w: float
    mean_net_efficiency: float
    asymmetry: float
    generator_derate: float


@dataclass
class RingSimulationResult:
    """Aggregate metrics for the full cartridge ring."""

    cfg: PhoenixV3Config
    ring: RingConfig
    cartridges: tuple[CartridgeSnapshot, ...]
    total_elec_power_w: float
    total_fuel_power_w: float
    ring_efficiency: float
    mean_cartridge_efficiency: float
    max_asymmetry: float
    min_generator_derate: float
    mean_wall_temp_k: float
    mean_generator_temp_k: float
    mean_valve_temp_k: float
    energy_balance_valid: bool
    raw_boundary_valid: bool


def _valid_cycle_reports(reports: tuple) -> list:
    return [
        r for r in reports
        if r.fuel_energy_j > 50.0 and r.max_piston_travel_mm > 5.0
    ]


def _late_window(reports: tuple) -> list:
    valid = _valid_cycle_reports(reports)
    return valid[-max(1, len(valid) // 10):] if valid else []


@dataclass(frozen=True)
class RingPhasePoint:
    index: int
    phase_offset_ms: float
    late_efficiency: float
    late_capture: float
    late_bdc_mm: float
    scavenge_efficiency: float


@dataclass(frozen=True)
class RingEfficiencyAudit:
    single_late_efficiency: float
    ring_late_efficiency: float
    efficiency_gap: float
    ring_power_efficiency: float
    phases: tuple[RingPhasePoint, ...]
    worst_index: int
    best_index: int


def analyze_ring_efficiency_gap(
    base_cfg: PhoenixV3Config,
    ring: RingConfig | None = None,
    *,
    cycles: int = 500,
) -> RingEfficiencyAudit:
    """Compare single-cartridge baseline to phased ring cartridges (late-window)."""
    ring_cfg = ring or RingConfig()
    single = PhoenixV3Simulator(base_cfg).simulate(cycles=cycles, record_history=False)
    single_late = _late_window(single.energy.cycle_reports)
    single_eff = float(np.mean([r.net_cartridge_efficiency for r in single_late])) if single_late else 0.0

    phases: list[RingPhasePoint] = []
    elec_j = 0.0
    fuel_j = 0.0
    for i, cart_cfg in enumerate(_cartridge_configs(base_cfg, ring_cfg)):
        result = PhoenixV3Simulator(cart_cfg).simulate(cycles=cycles, record_history=False)
        late = _late_window(result.energy.cycle_reports)
        if late:
            elec_j += float(np.mean([r.elec_energy_j for r in late]))
            fuel_j += float(np.mean([r.fuel_energy_j for r in late]))
        phases.append(RingPhasePoint(
            index=i,
            phase_offset_ms=cart_cfg.phase_offset_ms,
            late_efficiency=float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0,
            late_capture=float(np.mean([r.capture_fraction for r in late])) if late else 0.0,
            late_bdc_mm=float(np.mean([r.max_piston_travel_mm for r in late])) if late else 0.0,
            scavenge_efficiency=result.metrics.scavenge_efficiency,
        ))

    ring_late_eff = float(np.mean([p.late_efficiency for p in phases])) if phases else 0.0
    ring_power_eff = elec_j / max(fuel_j, 1e-9)
    worst = min(phases, key=lambda p: p.late_efficiency)
    best = max(phases, key=lambda p: p.late_efficiency)
    return RingEfficiencyAudit(
        single_late_efficiency=single_eff,
        ring_late_efficiency=ring_late_eff,
        efficiency_gap=single_eff - ring_late_eff,
        ring_power_efficiency=ring_power_eff,
        phases=tuple(phases),
        worst_index=worst.index,
        best_index=best.index,
    )


def print_ring_efficiency_audit(audit: RingEfficiencyAudit) -> None:
    print()
    print("=" * 72)
    print("RING EFFICIENCY AUDIT — scale-up gap analysis")
    print("=" * 72)
    print(f"  Single-cartridge (late):   {audit.single_late_efficiency:6.1%}")
    print(f"  Ring mean (late):          {audit.ring_late_efficiency:6.1%}")
    print(f"  Ring power-weighted:       {audit.ring_power_efficiency:6.1%}")
    print(f"  Scale-up gap:              {audit.efficiency_gap:6.1%} pp")
    print()
    print(f"  {'Cart':>4}  {'Phase':>7}  {'Enet':>6}  {'Capture':>7}  {'BDC':>5}  {'Scav':>5}")
    for p in audit.phases:
        print(
            f"  {p.index:4d}  {p.phase_offset_ms:6.2f}ms  "
            f"{p.late_efficiency:5.1%}  {p.late_capture:6.1%}  {p.late_bdc_mm:4.1f}  "
            f"{p.scavenge_efficiency:4.0%}"
        )
    print()
    print(f"  Best phase:  cart {audit.best_index}")
    print(f"  Worst phase: cart {audit.worst_index}")
    print("=" * 72)
    print()


def _cartridge_configs(base: PhoenixV3Config, ring: RingConfig) -> list[PhoenixV3Config]:
    cycle_ms = base.cycle_time_s * 1e3
    spacing_ms = cycle_ms / ring.cartridge_count
    configs: list[PhoenixV3Config] = []
    plenum_assignments = None
    if base.intake_plenum_enabled:
        from designs.phoenix_v3.ring_plenum import ring_plenum_assignments

        plenum_assignments = ring_plenum_assignments(base, ring.cartridge_count)
    for i in range(ring.cartridge_count):
        cart = replace(base, phase_offset_ms=i * spacing_ms)
        if plenum_assignments is not None:
            assign = plenum_assignments[i]
            cart = replace(
                cart,
                intake_ring_pressure_bar=assign.effective_intake_bar,
                intake_plenum_concurrent_intakes=assign.concurrent_intakes,
            )
        configs.append(cart)
    return configs


@dataclass(frozen=True)
class RingLongRunSummary:
    """Aggregate long-run stability across all cartridges on the ring."""

    ring: RingConfig
    cycles_per_cartridge: int
    cooling_mode: str
    total_elec_power_w: float
    ring_efficiency: float
    max_asymmetry: float
    max_generator_temp_k: float
    min_generator_derate: float
    mean_generator_efficiency: float
    collision_count: int
    coolant_temp_k: float
    thermal_sustainable: bool
    energy_balance_valid: bool
    raw_boundary_valid: bool
    cartridges: tuple[CartridgeSnapshot, ...]


def run_ring_long_stability(
    base_cfg: PhoenixV3Config | None = None,
    ring: RingConfig | None = None,
    *,
    cycles: int = 1000,
    cooling_mode: str = "water_jacket",
    shared_coolant: bool = True,
) -> RingLongRunSummary:
    """Run each cartridge for many cycles and aggregate ring-level thermal KPIs."""
    from designs.phoenix_v3_thermal import (
        CoolantBus,
        advance_coolant_bus,
        estimate_cartridge_heat_w,
        thermal_sustainable,
    )

    cfg = apply_generator_cooling(base_cfg or PhoenixV3Config(), cooling_mode)
    ring_cfg = ring or RingConfig()
    from designs.phoenix_v3_simulation import _thermal_config_from

    thermal_cfg = _thermal_config_from(cfg)
    snapshots: list[CartridgeSnapshot] = []
    collisions = 0
    max_gen_t = 0.0
    min_derate = 1.0
    gen_effs: list[float] = []
    coolant = CoolantBus(temp_k=cfg.ambient_temp_k)

    for i, cart_cfg in enumerate(_cartridge_configs(cfg, ring_cfg)):
        if shared_coolant:
            cart_cfg = replace(
                cart_cfg,
                shared_coolant_enabled=True,
                coolant_temp_k=coolant.temp_k,
            )
        result = PhoenixV3Simulator(cart_cfg).simulate(cycles=cycles, record_history=False)
        if shared_coolant:
            heat_w = estimate_cartridge_heat_w(
                result.energy.cycle_reports, cart_cfg.cycle_time_s,
            )
            coolant = advance_coolant_bus(
                coolant,
                ambient_temp_k=cfg.ambient_temp_k,
                coolant_thermal_mass_j_per_k=cfg.coolant_thermal_mass_j_per_k,
                coolant_radiator_w_per_k=cfg.coolant_radiator_w_per_k,
                heat_in_w=heat_w,
                duration_s=cycles * cart_cfg.cycle_time_s,
            )
        reports = _valid_cycle_reports(result.energy.cycle_reports)
        late = reports[-max(1, len(reports) // 10):] if reports else []
        collisions += sum(1 for r in reports if r.piston_collision)
        if late:
            max_gen_t = max(max_gen_t, max(r.generator_temp_k for r in late))
            min_derate = min(min_derate, min(r.generator_derate for r in late))
            gen_effs.extend([r.generator_efficiency for r in late])
        mean_elec = float(np.mean([r.elec_energy_j for r in late])) / max(cfg.cycle_time_s, 1e-9) if late else 0.0
        mean_eff = float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0
        derate = float(np.mean([r.generator_derate for r in late])) if late else 1.0
        snapshots.append(CartridgeSnapshot(
            index=i,
            phase_offset_ms=cart_cfg.phase_offset_ms,
            result=result,
            mean_elec_power_w=mean_elec,
            mean_net_efficiency=mean_eff,
            asymmetry=result.metrics.cylinder_variation,
            generator_derate=derate,
        ))

    total_elec = sum(s.mean_elec_power_w for s in snapshots)
    total_fuel = sum(
        float(np.mean([r.fuel_energy_j for r in _late_window(s.result.energy.cycle_reports)]))
        / max(cfg.cycle_time_s, 1e-9)
        for s in snapshots
        if _late_window(s.result.energy.cycle_reports)
    )
    ring_eff = total_elec / max(total_fuel, 1e-9)
    mean_gen_eff = float(np.mean(gen_effs)) if gen_effs else cfg.generator_efficiency

    return RingLongRunSummary(
        ring=ring_cfg,
        cycles_per_cartridge=cycles,
        cooling_mode=cooling_mode,
        total_elec_power_w=total_elec,
        ring_efficiency=ring_eff,
        max_asymmetry=max((s.asymmetry for s in snapshots), default=0.0),
        max_generator_temp_k=max_gen_t,
        min_generator_derate=min_derate,
        mean_generator_efficiency=mean_gen_eff,
        collision_count=collisions,
        coolant_temp_k=coolant.temp_k,
        thermal_sustainable=thermal_sustainable(max_gen_t, min_derate, thermal_cfg),
        energy_balance_valid=all(s.result.energy.energy_balance_valid for s in snapshots),
        raw_boundary_valid=all(s.result.energy.raw_boundary_valid for s in snapshots),
        cartridges=tuple(snapshots),
    )


def print_ring_long_run_report(summary: RingLongRunSummary) -> None:
    print()
    print("=" * 72)
    print(
        f"RING LONG-RUN — {summary.ring.cartridge_count} cartridges × "
        f"{summary.cycles_per_cartridge} cycles  (cooling: {summary.cooling_mode})"
    )
    print("=" * 72)
    print(f"  Ring electrical power:   {summary.total_elec_power_w:10.1f} W")
    print(f"  Ring efficiency:         {summary.ring_efficiency:10.1%}")
    print(f"  Max asymmetry:           {summary.max_asymmetry:10.2%}")
    print(f"  Collisions (all carts):  {summary.collision_count}")
    print(f"  Coolant return temp:     {summary.coolant_temp_k - 273.15:10.1f} C")
    print(f"  Max generator temp:      {summary.max_generator_temp_k - 273.15:10.1f} C")
    print(f"  Min generator derate:    {summary.min_generator_derate:10.1%}")
    print(f"  Mean generator eff:      {summary.mean_generator_efficiency:10.1%}")
    print(f"  Energy balance:          {'PASS' if summary.energy_balance_valid else 'FAIL'}")
    print(f"  Work boundary:           {'PASS' if summary.raw_boundary_valid else 'WARN'}")
    print(f"  Thermal sustainability:  {'PASS' if summary.thermal_sustainable else 'FAIL'}")
    print("=" * 72)
    print()


def simulate_ring(
    base_cfg: PhoenixV3Config | None = None,
    ring: RingConfig | None = None,
    *,
    cycles: int = 12,
    record_history: bool = False,
) -> RingSimulationResult:
    """Run all cartridges and aggregate bus-level KPIs."""
    cfg = base_cfg or PhoenixV3Config()
    ring_cfg = ring or RingConfig()
    snapshots: list[CartridgeSnapshot] = []

    for i, cart_cfg in enumerate(_cartridge_configs(cfg, ring_cfg)):
        result = PhoenixV3Simulator(cart_cfg).simulate(
            cycles=cycles,
            record_history=record_history,
        )
        reports = _valid_cycle_reports(result.energy.cycle_reports)
        late = reports[-max(1, len(reports) // 10):] if reports else []
        mean_elec = float(np.mean([r.elec_energy_j for r in late])) / max(cfg.cycle_time_s, 1e-9) if late else 0.0
        mean_fuel = float(np.mean([r.fuel_energy_j for r in late])) / max(cfg.cycle_time_s, 1e-9) if late else 0.0
        mean_eff = float(np.mean([r.net_cartridge_efficiency for r in late])) if late else 0.0
        derate = float(np.mean([r.generator_derate for r in reports])) if reports else 1.0
        snapshots.append(CartridgeSnapshot(
            index=i,
            phase_offset_ms=cart_cfg.phase_offset_ms,
            result=result,
            mean_elec_power_w=mean_elec,
            mean_net_efficiency=mean_eff,
            asymmetry=result.metrics.cylinder_variation,
            generator_derate=derate,
        ))

    total_elec = sum(s.mean_elec_power_w for s in snapshots)
    total_fuel = sum(
        float(np.mean([r.fuel_energy_j for r in _late_window(s.result.energy.cycle_reports)]))
        / max(cfg.cycle_time_s, 1e-9)
        for s in snapshots
        if _late_window(s.result.energy.cycle_reports)
    )
    ring_eff = total_elec / max(total_fuel, 1e-9)

    all_reports = [r for s in snapshots for r in _late_window(s.result.energy.cycle_reports)]
    return RingSimulationResult(
        cfg=cfg,
        ring=ring_cfg,
        cartridges=tuple(snapshots),
        total_elec_power_w=total_elec,
        total_fuel_power_w=total_fuel,
        ring_efficiency=ring_eff,
        mean_cartridge_efficiency=float(np.mean([s.mean_net_efficiency for s in snapshots])),
        max_asymmetry=max((s.asymmetry for s in snapshots), default=0.0),
        min_generator_derate=min((s.generator_derate for s in snapshots), default=1.0),
        mean_wall_temp_k=float(np.mean([r.wall_temp_k for r in all_reports])) if all_reports else 0.0,
        mean_generator_temp_k=float(np.mean([r.generator_temp_k for r in all_reports])) if all_reports else 0.0,
        mean_valve_temp_k=float(np.mean([r.valve_temp_k for r in all_reports])) if all_reports else 0.0,
        energy_balance_valid=all(s.result.energy.energy_balance_valid for s in snapshots),
        raw_boundary_valid=all(s.result.energy.raw_boundary_valid for s in snapshots),
    )


def print_ring_report(result: RingSimulationResult) -> None:
    ring = result.ring
    print()
    print("=" * 72)
    print(f"PHOENIX V3 RING — {ring.cartridge_count} CARTRIDGES")
    print("=" * 72)
    print(f"  Ring electrical power:   {result.total_elec_power_w:10.1f} W")
    print(f"  Ring fuel power:         {result.total_fuel_power_w:10.1f} W")
    print(f"  Ring efficiency:         {result.ring_efficiency:10.1%}")
    print(f"  Mean cartridge eff:      {result.mean_cartridge_efficiency:10.1%}")
    print(f"  Max piston asymmetry:    {result.max_asymmetry:10.2%}")
    print(f"  Min generator derate:    {result.min_generator_derate:10.1%}")
    print(f"  Mean wall temp:          {result.mean_wall_temp_k - 273.15:10.1f} °C")
    print(f"  Mean generator temp:     {result.mean_generator_temp_k - 273.15:10.1f} °C")
    print(f"  Mean valve temp:         {result.mean_valve_temp_k - 273.15:10.1f} °C")
    print(f"  Energy balance:          {'PASS' if result.energy_balance_valid else 'FAIL'}")
    print(f"  Raw work boundary:       {'PASS' if result.raw_boundary_valid else 'WARN'}")
    print()
    print(f"  {'Cart':>4}  {'Phase':>7}  {'Enet':>6}  {'Asym':>6}  {'Derate':>6}  {'Pelec':>8}")
    for s in result.cartridges:
        print(
            f"  {s.index:4d}  {s.phase_offset_ms:6.2f}ms  "
            f"{s.mean_net_efficiency:5.1%}  {s.asymmetry:5.2%}  "
            f"{s.generator_derate:5.1%}  {s.mean_elec_power_w:7.1f}W"
        )
    print("=" * 72)
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Phoenix V3 12-cartridge ring simulation")
    parser.add_argument("--cycles", type=int, default=12, help="Cycles per cartridge")
    parser.add_argument("--cartridges", type=int, default=12, help="Cartridge count on ring")
    parser.add_argument("--best-tuning", action="store_true", help="Use best-tuning JSON")
    parser.add_argument("--headless", action="store_true", help="Non-interactive mode")
    parser.add_argument("--record-history", action="store_true", help="Store per-step history (heavy)")
    parser.add_argument("--long-run", action="store_true", help="Long-duration stability per cartridge")
    parser.add_argument("--cooling", type=str, default="water_jacket",
                        choices=["passive", "water_jacket", "oil_loop", "cold_plate", "phase_change"],
                        help="Generator cooling mode")
    parser.add_argument("--ring-audit", action="store_true",
                        help="Print per-phase ring efficiency gap vs single cartridge")
    parser.add_argument("--no-shared-coolant", action="store_true",
                        help="Disable shared coolant bus coupling between cartridges")
    args = parser.parse_args()

    if args.headless:
        import matplotlib
        matplotlib.use("Agg")

    cfg = PhoenixV3Config()
    if args.best_tuning:
        for best_path in (
            OUT_DIR / "phoenix_v3_best_tuning_v3.json",
            OUT_DIR / "phoenix_v3_best_tuning_v2.json",
            OUT_DIR / "phoenix_v3_best_tuning.json",
        ):
            if best_path.exists():
                from designs.phoenix_v3_optimizer import TuningVector
                payload = json.loads(best_path.read_text(encoding="utf-8"))
                cfg = TuningVector.from_dict(payload["parameters"]).to_config(cfg)
                break

    ring = RingConfig(cartridge_count=args.cartridges)
    if args.ring_audit:
        audit = analyze_ring_efficiency_gap(cfg, ring, cycles=max(args.cycles, 100))
        print_ring_efficiency_audit(audit)
        return
    if args.long_run:
        summary = run_ring_long_stability(
            cfg,
            ring,
            cycles=max(args.cycles, 500),
            cooling_mode=args.cooling,
            shared_coolant=not args.no_shared_coolant,
        )
        print_ring_long_run_report(summary)
        return

    result = simulate_ring(
        cfg,
        ring,
        cycles=args.cycles,
        record_history=args.record_history,
    )
    print_ring_report(result)
    if result.cartridges:
        print_report(result.cartridges[0].result)
        print_energy_audit(result.cartridges[0].result)


if __name__ == "__main__":
    main()
