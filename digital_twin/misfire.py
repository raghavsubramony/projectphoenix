"""Misfire / partial-burn injection for Gate 1 virtual bench realism.

Models a cartridge combustion event that fails to release the expected heat —
the dominant first-fire failure mode on a free-piston lab rig. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .single_cylinder import (
    Gate1BenchResult,
    SingleCylinderInputs,
    SingleCylinderResult,
    evaluate_gate1_bench,
    gate1_bench_at_load,
    measure_gate1_bench,
    simulate_1d_combustion,
    simulate_free_piston,
    tier_physics_profile,
)


@dataclass(frozen=True)
class MisfireSpec:
    """How hard the misfire hits indicated work / peak pressure."""

    # 1.0 = full misfire (no useful work); 0.3 = partial burn.
    severity: float = 1.0
    label: str = "full_misfire"

    def __post_init__(self) -> None:
        object.__setattr__(self, "severity", max(0.0, min(1.0, float(self.severity))))


def apply_misfire(
    result: SingleCylinderResult,
    spec: MisfireSpec | None = None,
) -> SingleCylinderResult:
    """Scale down combustion work products; leave crank geometry/trace length."""
    spec = spec or MisfireSpec()
    keep = 1.0 - spec.severity
    # Residual compression pumping even on total misfire.
    residual = 0.05
    scale = residual + (1.0 - residual) * keep
    return replace(
        result,
        indicated_efficiency=result.indicated_efficiency * scale,
        electric_efficiency=result.electric_efficiency * scale,
        imep_pa=result.imep_pa * scale,
        peak_pressure_pa=max(
            result.trace.pressure_pa[0] if result.trace.pressure_pa else 1e5,
            result.peak_pressure_pa * (0.35 + 0.65 * keep),
        ),
        knock_index=result.knock_index * keep,
        physics_backend=f"{result.physics_backend}+misfire:{spec.label}",
    )


def gate1_bench_with_misfire(
    *,
    speed_rpm: float = 2600.0,
    load_fraction: float = 0.75,
    tier_index: int = 1,
    spec: MisfireSpec | None = None,
    prefer_cantera: bool = False,
) -> Gate1BenchResult:
    """Sweet-spot Gate 1 bench point with an injected misfire event."""
    spec = spec or MisfireSpec()
    displacement_cc = (100.0, 300.0, 750.0)[max(0, min(2, tier_index))]
    compression_ratio, fp_cfg = tier_physics_profile(tier_index)
    inp = SingleCylinderInputs(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_m3=max(1e-7, displacement_cc * 1e-6),
        compression_ratio=compression_ratio,
        generator_efficiency=0.96,
    )
    comb = apply_misfire(
        simulate_1d_combustion(inp, prefer_cantera=prefer_cantera),
        spec,
    )
    fp = simulate_free_piston(comb, fp_cfg, speed_rpm=speed_rpm)
    meas = measure_gate1_bench(comb, fp, fp_cfg, inp, tier_index, 0.96)
    return evaluate_gate1_bench(
        meas, None, load_fraction=load_fraction, tier_index=tier_index,
    )


@dataclass(frozen=True)
class MisfireCoverageResult:
    """Compare healthy vs misfire bench power — proves the hole is visible."""

    healthy_peak_power_kw: float
    misfire_peak_power_kw: float
    healthy_efficiency: float
    misfire_efficiency: float
    power_ratio: float
    efficiency_ratio: float
    healthy_passed: bool
    misfire_passed: bool

    @property
    def power_drop_pct(self) -> float:
        if self.healthy_peak_power_kw <= 0.0:
            return 0.0
        return 100.0 * (1.0 - self.power_ratio)


def run_misfire_coverage(
    *,
    severity: float = 1.0,
    prefer_cantera: bool = False,
) -> MisfireCoverageResult:
    """Healthy sweet-spot vs full/partial misfire — lab acceptance must see the drop."""
    healthy = gate1_bench_at_load(prefer_cantera=prefer_cantera, tier_index=1)
    misfire = gate1_bench_with_misfire(
        spec=MisfireSpec(severity=severity),
        prefer_cantera=prefer_cantera,
    )
    hp = healthy.measurement.peak_power_kw
    mp = misfire.measurement.peak_power_kw
    he = healthy.measurement.electric_efficiency
    me = misfire.measurement.electric_efficiency
    return MisfireCoverageResult(
        healthy_peak_power_kw=hp,
        misfire_peak_power_kw=mp,
        healthy_efficiency=he,
        misfire_efficiency=me,
        power_ratio=(mp / hp) if hp > 0.0 else 0.0,
        efficiency_ratio=(me / he) if he > 0.0 else 0.0,
        healthy_passed=healthy.passed,
        misfire_passed=misfire.passed,
    )
