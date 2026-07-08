"""Graceful degradation: one cylinder offline (ERS §4.8 fault tolerance).

When a single combustion module fails, the ATPE stack loses one cylinder's
share of its tier capacity but the vehicle must keep operating within reduced
limits. This module models that by removing one unit from a tier and re-running
the ERS acceptance harness — read-only, opt-in, pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .acceptance import Check, evaluate, phase1_targets_for
from .config import ATPEConfig, BodyStyle, PHASE1_BODIES, TierSpec, TwinConfig, phase1_config_for
from .powertrain import Powertrain


@dataclass(frozen=True)
class DegradedScenario:
    """One fault tolerance scenario: which tier loses how many cylinders."""

    tier_index: int
    units_offline: int = 1
    label: str = ""


@dataclass(frozen=True)
class DegradedBodyResult:
    """ERS acceptance outcome for one body under a degraded engine config."""

    body: str
    scenario: str
    peak_power_kw: float
    continuous_power_kw: float
    checks_passed: int
    checks_total: int
    failed_checks: tuple[str, ...]

    @property
    def all_passed(self) -> bool:
        return self.checks_passed == self.checks_total


def atpe_with_cylinder_offline(atpe: ATPEConfig, tier_index: int,
                               units_offline: int = 1) -> ATPEConfig:
    """Return a copy of `atpe` with `units_offline` cylinders removed from one tier."""
    tiers: list[TierSpec] = []
    for i, tier in enumerate(atpe.tiers):
        if i != tier_index:
            tiers.append(tier)
            continue
        remaining = max(0, tier.units - units_offline)
        if remaining == 0:
            continue
        per_unit_w = tier.max_electric_w / max(1, tier.units)
        tiers.append(replace(
            tier,
            units=remaining,
            max_electric_w=per_unit_w * remaining,
        ))
    if not tiers:
        raise ValueError("cannot remove all cylinders from the ATPE stack")
    return replace(atpe, tiers=tuple(tiers))


def degraded_config_for(body: BodyStyle, scenario: DegradedScenario) -> TwinConfig:
    """Body config with one tier partially offline."""
    cfg = phase1_config_for(body)
    atpe = atpe_with_cylinder_offline(
        cfg.atpe, scenario.tier_index, scenario.units_offline)
    return replace(cfg, atpe=atpe)


def default_scenarios() -> tuple[DegradedScenario, ...]:
    """Representative single-cylinder fault modes across the three tiers."""
    return (
        DegradedScenario(0, 1, "Tier 1 micro cylinder offline"),
        DegradedScenario(1, 1, "Tier 2 medium cylinder offline"),
        DegradedScenario(2, 1, "Tier 3 large cylinder offline"),
    )


def evaluate_degraded_body(body: BodyStyle,
                         scenario: DegradedScenario | None = None) -> DegradedBodyResult:
    """Run ERS checks for one body with a degraded ATPE stack."""
    sc = scenario or DegradedScenario(0, 1, "Tier 1 micro cylinder offline")
    cfg = degraded_config_for(body, sc)
    targets = phase1_targets_for(body.name)

    def build() -> Powertrain:
        return Powertrain(cfg)

    checks: list[Check] = evaluate(build, targets)
    failed = tuple(c.name for c in checks if not c.passed)
    atpe = cfg.atpe
    return DegradedBodyResult(
        body=body.name,
        scenario=sc.label or f"tier {sc.tier_index} -{sc.units_offline}",
        peak_power_kw=atpe.max_electric_w / 1000.0,
        continuous_power_kw=atpe.max_electric_w / 1000.0,
        checks_passed=sum(1 for c in checks if c.passed),
        checks_total=len(checks),
        failed_checks=failed,
    )


def fleet_graceful_degradation(
    bodies: tuple[BodyStyle, ...] = PHASE1_BODIES,
    scenarios: tuple[DegradedScenario, ...] | None = None,
) -> list[DegradedBodyResult]:
    """Evaluate every body under each degraded scenario."""
    scs = scenarios or default_scenarios()
    return [
        evaluate_degraded_body(body, sc)
        for body in bodies
        for sc in scs
    ]


def graceful_degradation_table(results: list[DegradedBodyResult] | None = None) -> str:
    """Human-readable report of fault-tolerance acceptance."""
    rows = results if results is not None else fleet_graceful_degradation()
    lines = [
        "=== Graceful degradation: one cylinder offline (ERS §4.8) ===",
        "  Body          Scenario                         Peak kW  ERS checks",
        "  " + "-" * 68,
    ]
    for r in rows:
        mark = "PASS" if r.all_passed else "FAIL"
        lines.append(
            f"  {r.body:<12} {r.scenario:<32} {r.peak_power_kw:6.0f}  "
            f"{r.checks_passed}/{r.checks_total} [{mark}]")
        if r.failed_checks:
            lines.append(f"    failed: {', '.join(r.failed_checks)}")
    all_ok = all(r.all_passed for r in rows)
    lines.append("")
    lines.append(
        "  Fleet fault-tolerant: "
        + ("yes — every body passes all ERS checks with one cylinder offline"
           if all_ok else "no — see failures above"))
    return "\n".join(lines)
