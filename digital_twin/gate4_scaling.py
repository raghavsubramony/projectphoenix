"""Virtual Gate 4 scaling — multi-cylinder layout search without a hardware ring.

Sweeps micro / medium / large cartridge counts across named ring sizes (X4, X6, X8,
X10, X12, X14, X16, …), evaluates every tier mix at each ring size, and ranks
layouts on ERS acceptance plus charge-sustaining fuel economy.

Fuel columns use ``initial_soc == soc_target`` (0.55) and the standard highway /
mixed cycles — the same accounting as ``charge_sustaining_bodies()`` and the
validated 4.46 L/100 km AWD SUV headline.

Two cartridge power profiles:
  - **phase1** — validated vehicle stack (7.5 / 40 / 60 kW per cartridge)
  - **storyboard** — PHOENIX nameplate peaks (20 / 78 / 120 kW per cartridge)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, replace
from pathlib import Path

from .acceptance import evaluate, phase1_targets_for
from .config import (
    ATPEConfig,
    PHASE1_BODIES,
    TierSpec,
    TwinConfig,
    phase1_config_for,
)
from .drive_cycles import DriveCycles
from .powertrain import Powertrain
from .simulation import run
from .single_cylinder import PHOENIX_X12_CARTRIDGE_KW, TIER_CARTRIDGE_PEAK_KW

_KW = 1_000.0

# Per-cartridge ratings for Phase-1 vehicle study (from validated 4+2+2 stack).
PHASE1_CARTRIDGE_KW: tuple[float, float, float] = (7.5, 40.0, 60.0)
PHASE1_DISPLACEMENT_CC: tuple[float, float, float] = (100.0, 300.0, 750.0)
PHASE1_THERMAL_EFF: tuple[float, float, float] = (0.44, 0.41, 0.38)

# Storyboard / Gate 1 nameplate peaks (micro / medium / large cartridge).
STORYBOARD_CARTRIDGE_KW: tuple[float, float, float] = TIER_CARTRIDGE_PEAK_KW

TIER_NAMES: tuple[str, ...] = ("Tier 1 (micro)", "Tier 2 (medium)", "Tier 3 (large)")

# Named ring sizes for the virtual Gate 4 configuration study.
DEFAULT_RING_SIZES: tuple[int, ...] = (4, 6, 8, 10, 12, 14, 16)

# Validated Phase-1 reference layout (8 cylinders, 230 kW rated).
REFERENCE_LAYOUT: tuple[int, int, int] = (4, 2, 2)

# ERS continuous generation target used to penalise over-sized stacks (kW).
P1_CONTINUOUS_KW = 100.0

# Canonical PHOENIX-X12 production ring (12 x 78 kW medium cartridges).
CANONICAL_X12_LAYOUT: tuple[int, int, int] = (0, 12, 0)

RATING_PROFILES: dict[str, tuple[float, float, float]] = {
    "phase1": PHASE1_CARTRIDGE_KW,
    "storyboard": STORYBOARD_CARTRIDGE_KW,
}


@dataclass(frozen=True)
class CylinderLayout:
    """Cartridge counts per size class (micro, medium, large)."""

    n_micro: int
    n_medium: int
    n_large: int

    @property
    def total_cylinders(self) -> int:
        return self.n_micro + self.n_medium + self.n_large

    @property
    def label(self) -> str:
        return f"{self.n_micro}/{self.n_medium}/{self.n_large}"

    @property
    def counts(self) -> tuple[int, int, int]:
        return (self.n_micro, self.n_medium, self.n_large)


def ring_label(ring_size: int) -> str:
    """Return storyboard-style ring name, e.g. 8 -> 'X8'."""
    return f"X{ring_size}"


def count_active_tiers(layout: CylinderLayout) -> int:
    """Number of tier size classes with at least one cartridge (1, 2, or 3)."""
    return sum(1 for c in layout.counts if c > 0)


def is_all_three_tiers(layout: CylinderLayout) -> bool:
    """True when micro, medium, and large cartridges are all present."""
    return layout.n_micro > 0 and layout.n_medium > 0 and layout.n_large > 0


def layout_tier_mix_kind(layout: CylinderLayout) -> str:
    """Classify layout as homogeneous single-tier or mixed."""
    active = count_active_tiers(layout)
    if active != 1:
        return "mixed"
    if layout.n_micro > 0:
        return "homogeneous_micro"
    if layout.n_medium > 0:
        return "homogeneous_medium"
    return "homogeneous_large"


def is_design_aligned(
    layout: CylinderLayout,
    *,
    ring_size: int | None = None,
    rating_profile: str = "phase1",
) -> bool:
    """True when the layout matches PHOENIX tiered / medium-cartridge ring architecture.

    Storyboard rings must include at least one **medium** (78 kW) cartridge — the
    X12 production unit. Homogeneous all-micro rings are excluded: they exploit
    a higher table efficiency (0.44 vs 0.41) in simulation but are not the ring
    design. Phase-1 layouts must use **at least two tier sizes** for load matching.
    """
    if layout.counts == CANONICAL_X12_LAYOUT:
        return True
    if rating_profile == "storyboard":
        if layout.n_medium < 1:
            return False
        return True
    # phase1 vehicle stack: multi-tier load matching, not single-size stacks.
    active_tiers = sum(1 for c in layout.counts if c > 0)
    return active_tiers >= 2


def is_canonical_x12(layout: CylinderLayout, ring_size: int | None) -> bool:
    return ring_size == 12 and layout.counts == CANONICAL_X12_LAYOUT


@dataclass(frozen=True)
class Gate4LayoutResult:
    """Simulation outcome for one cylinder layout on the reference SUV."""

    layout: CylinderLayout
    rated_kw: float
    highway_fuel_l_per_100km: float
    mixed_fuel_l_per_100km: float
    ers_passed: int
    ers_total: int
    shortfall_events: int
    score: float
    ring_size: int | None = None
    ring_name: str = ""
    rating_profile: str = "phase1"
    tier_mix_kind: str = "mixed"
    design_aligned: bool = True
    is_canonical_x12_target: bool = False
    is_reference: bool = False

    @property
    def all_ers_pass(self) -> bool:
        return self.ers_passed == self.ers_total


@dataclass(frozen=True)
class Gate4ScalingSummary:
    """Rollup of a layout sweep."""

    results: tuple[Gate4LayoutResult, ...]
    body: str
    mode: str

    @property
    def passing(self) -> tuple[Gate4LayoutResult, ...]:
        return tuple(r for r in self.results if r.all_ers_pass)

    @property
    def design_aligned_passing(self) -> tuple[Gate4LayoutResult, ...]:
        return tuple(r for r in self.passing if r.design_aligned)

    @property
    def best(self) -> Gate4LayoutResult:
        ranked = sorted(self.results, key=lambda r: r.score, reverse=True)
        return ranked[0]

    @property
    def best_design_aligned(self) -> Gate4LayoutResult | None:
        pool = self.design_aligned_passing
        if not pool:
            return None
        return max(pool, key=lambda r: r.score)

    def top(self, n: int = 10) -> tuple[Gate4LayoutResult, ...]:
        ranked = sorted(self.results, key=lambda r: r.score, reverse=True)
        return tuple(ranked[:n])

    def top_design_aligned(self, n: int = 10) -> tuple[Gate4LayoutResult, ...]:
        pool = sorted(self.design_aligned_passing, key=lambda r: r.score, reverse=True)
        return tuple(pool[:n])

    def for_ring(self, ring_size: int) -> tuple[Gate4LayoutResult, ...]:
        return tuple(r for r in self.results if r.ring_size == ring_size)

    def best_per_ring(
        self,
        *,
        design_aligned_only: bool = False,
        all_three_tiers_only: bool = False,
    ) -> dict[int, Gate4LayoutResult]:
        out: dict[int, Gate4LayoutResult] = {}
        sizes = sorted({r.ring_size for r in self.results if r.ring_size is not None})
        for size in sizes:
            pool = list(self.for_ring(size))
            if design_aligned_only:
                pool = [r for r in pool if r.design_aligned]
            if all_three_tiers_only:
                pool = [r for r in pool if is_all_three_tiers(r.layout)]
            if pool:
                out[size] = max(pool, key=lambda r: r.score)
        return out

    def best_with_tier_depth(
        self,
        tier_depth: int,
        *,
        design_aligned_only: bool = False,
        passing_only: bool = True,
    ) -> Gate4LayoutResult | None:
        """Best layout using exactly ``tier_depth`` active tier sizes (1, 2, or 3)."""
        pool = list(self.results)
        if passing_only:
            pool = [r for r in pool if r.all_ers_pass]
        if design_aligned_only:
            pool = [r for r in pool if r.design_aligned]
        pool = [r for r in pool if count_active_tiers(r.layout) == tier_depth]
        if not pool:
            return None
        return max(pool, key=lambda r: r.score)

    def reference_result(self) -> Gate4LayoutResult | None:
        return next((r for r in self.results if r.is_reference), None)

    def passing_by_tier_depth(self) -> dict[int, int]:
        """Count ERS-passing layouts grouped by active tier depth."""
        counts: dict[int, int] = {1: 0, 2: 0, 3: 0}
        for r in self.passing:
            depth = count_active_tiers(r.layout)
            if depth in counts:
                counts[depth] += 1
        return counts


def build_atpe_from_layout(
    layout: CylinderLayout,
    *,
    cartridge_kw: tuple[float, float, float] = PHASE1_CARTRIDGE_KW,
    displacement_cc: tuple[float, float, float] = PHASE1_DISPLACEMENT_CC,
    thermal_eff: tuple[float, float, float] = PHASE1_THERMAL_EFF,
) -> ATPEConfig:
    """Build an ATPE stack from cartridge counts (smallest tier first)."""
    tiers: list[TierSpec] = []
    counts = layout.counts
    for i, n in enumerate(counts):
        if n <= 0:
            continue
        tiers.append(TierSpec(
            name=TIER_NAMES[i],
            units=n,
            displacement_cc=displacement_cc[i],
            max_electric_w=cartridge_kw[i] * _KW * n,
            thermal_efficiency=thermal_eff[i],
        ))
    if not tiers:
        raise ValueError("layout must include at least one cartridge")
    return ATPEConfig(tiers=tuple(tiers))


def build_x12_homogeneous_atpe(n_cartridges: int) -> ATPEConfig:
    """Homogeneous medium-cartridge ring (PHOENIX-X12 storyboard scaling)."""
    if n_cartridges < 1:
        raise ValueError("need at least one cartridge")
    return build_atpe_from_layout(
        CylinderLayout(0, n_cartridges, 0),
        cartridge_kw=STORYBOARD_CARTRIDGE_KW,
    )


def twin_with_layout(
    layout: CylinderLayout,
    body_index: int = 0,
    *,
    atpe: ATPEConfig | None = None,
    cartridge_kw: tuple[float, float, float] = PHASE1_CARTRIDGE_KW,
) -> TwinConfig:
    """Phase-1 body config with a custom ATPE cylinder layout."""
    body = PHASE1_BODIES[body_index]
    cfg = phase1_config_for(body)
    engine = atpe if atpe is not None else build_atpe_from_layout(
        layout, cartridge_kw=cartridge_kw,
    )
    return replace(cfg, atpe=engine)


def charge_sustaining_fuel_config(cfg: TwinConfig) -> TwinConfig:
    """Align fuel-economy runs with the fleet charge-sustaining convention.

    Trip energy must be supplied by the engine, not net battery depletion, so
    fuel columns use ``initial_soc == soc_target`` (0.55 on Phase-1 LFP).
    Matches ``charge_sustaining_bodies()`` and the validated 4.46 L/100 km headline.
    """
    return replace(cfg, battery=replace(cfg.battery, initial_soc=cfg.battery.soc_target))


def _layout_score(
    *,
    all_ers_pass: bool,
    highway_fuel: float,
    total_cylinders: int,
    rated_kw: float,
    ers_passed: int,
) -> float:
    """Higher is better. Failing layouts sort below any passing layout."""
    if not all_ers_pass:
        return -1e9 + ers_passed
    oversize_kw = max(0.0, rated_kw - (P1_CONTINUOUS_KW + 30.0))
    return (
        1000.0
        - highway_fuel * 20.0
        - total_cylinders * 2.0
        - oversize_kw * 0.05
    )


def evaluate_layout(
    layout: CylinderLayout,
    *,
    body_index: int = 0,
    atpe: ATPEConfig | None = None,
    cartridge_kw: tuple[float, float, float] = PHASE1_CARTRIDGE_KW,
    highway_duration_s: float | None = None,
    mixed_duration_s: float | None = None,
    ring_size: int | None = None,
    rating_profile: str = "phase1",
) -> Gate4LayoutResult:
    """Score one layout on ERS + charge-sustaining fuel for the reference body."""
    body = PHASE1_BODIES[body_index]
    cfg = twin_with_layout(
        layout, body_index, atpe=atpe, cartridge_kw=cartridge_kw,
    )
    fuel_cfg = charge_sustaining_fuel_config(cfg)
    engine = cfg.atpe

    def _build() -> Powertrain:
        return Powertrain(cfg)

    targets = phase1_targets_for(body.name)
    checks = evaluate(_build, targets)
    ers_passed = sum(1 for c in checks if c.passed)
    ers_total = len(checks)

    hwy_kw: dict[str, float] = {}
    if highway_duration_s is not None:
        hwy_kw["duration_s"] = highway_duration_s
    mix_kw: dict[str, float] = {}
    if mixed_duration_s is not None:
        mix_kw["duration_s"] = mixed_duration_s

    highway = run(
        Powertrain(fuel_cfg), DriveCycles.highway(**hwy_kw),
    )
    mixed = run(
        Powertrain(fuel_cfg), DriveCycles.mixed(**mix_kw),
    )

    rated_kw = engine.max_electric_w / _KW
    all_pass = ers_passed == ers_total
    ref = (
        layout.counts == REFERENCE_LAYOUT
        and rating_profile == "phase1"
        and cartridge_kw == PHASE1_CARTRIDGE_KW
    )
    rs = ring_size if ring_size is not None else layout.total_cylinders
    aligned = is_design_aligned(
        layout, ring_size=rs, rating_profile=rating_profile,
    )

    return Gate4LayoutResult(
        layout=layout,
        rated_kw=rated_kw,
        highway_fuel_l_per_100km=highway.fuel_l_per_100km,
        mixed_fuel_l_per_100km=mixed.fuel_l_per_100km,
        ers_passed=ers_passed,
        ers_total=ers_total,
        shortfall_events=highway.shortfall_events + mixed.shortfall_events,
        score=_layout_score(
            all_ers_pass=all_pass,
            highway_fuel=highway.fuel_l_per_100km,
            total_cylinders=layout.total_cylinders,
            rated_kw=rated_kw,
            ers_passed=ers_passed,
        ),
        ring_size=rs,
        ring_name=ring_label(rs) if rs else "",
        rating_profile=rating_profile,
        tier_mix_kind=layout_tier_mix_kind(layout),
        design_aligned=aligned,
        is_canonical_x12_target=is_canonical_x12(layout, rs),
        is_reference=ref,
    )


def enumerate_layouts(
    *,
    min_total: int = 2,
    max_total: int = 16,
    max_per_tier: int = 8,
) -> tuple[CylinderLayout, ...]:
    """All non-empty micro/medium/large combinations within bounds."""
    layouts: list[CylinderLayout] = []
    for n_micro in range(0, max_per_tier + 1):
        for n_medium in range(0, max_per_tier + 1):
            for n_large in range(0, max_per_tier + 1):
                total = n_micro + n_medium + n_large
                if total < min_total or total > max_total:
                    continue
                layouts.append(CylinderLayout(n_micro, n_medium, n_large))
    return tuple(layouts)


def enumerate_ring_layouts(ring_size: int) -> tuple[CylinderLayout, ...]:
    """Every tier mix that sums to exactly ``ring_size`` cartridges."""
    if ring_size < 1:
        return ()
    layouts: list[CylinderLayout] = []
    for n_micro in range(0, ring_size + 1):
        for n_medium in range(0, ring_size + 1 - n_micro):
            n_large = ring_size - n_micro - n_medium
            layouts.append(CylinderLayout(n_micro, n_medium, n_large))
    return tuple(layouts)


def run_gate4_sweep(
    *,
    body_index: int = 0,
    min_total: int = 2,
    max_total: int = 12,
    max_per_tier: int = 6,
) -> Gate4ScalingSummary:
    """Sweep heterogeneous layouts for the Phase-1 vehicle cartridge ratings."""
    results: list[Gate4LayoutResult] = []
    for layout in enumerate_layouts(
        min_total=min_total, max_total=max_total, max_per_tier=max_per_tier,
    ):
        results.append(evaluate_layout(
            layout,
            body_index=body_index,
            cartridge_kw=PHASE1_CARTRIDGE_KW,
            rating_profile="phase1",
        ))
    body = PHASE1_BODIES[body_index].name
    return Gate4ScalingSummary(tuple(results), body, "phase1_open")


def run_ring_size_study(
    ring_sizes: tuple[int, ...] = DEFAULT_RING_SIZES,
    *,
    rating_profile: str = "storyboard",
    body_index: int = 0,
) -> Gate4ScalingSummary:
    """Evaluate every tier mix at each named ring size (X4, X8, X12, …)."""
    cartridge_kw = RATING_PROFILES[rating_profile]
    results: list[Gate4LayoutResult] = []
    for size in ring_sizes:
        for layout in enumerate_ring_layouts(size):
            results.append(evaluate_layout(
                layout,
                body_index=body_index,
                cartridge_kw=cartridge_kw,
                ring_size=size,
                rating_profile=rating_profile,
            ))
    body = PHASE1_BODIES[body_index].name
    mode = f"{rating_profile}_ring"
    return Gate4ScalingSummary(tuple(results), body, mode)


def run_x12_ring_sweep(
    cartridge_counts: tuple[int, ...] = (4, 6, 8, 10, 12, 14, 16),
    *,
    body_index: int = 0,
) -> Gate4ScalingSummary:
    """Sweep homogeneous storyboard medium-cartridge rings (legacy helper)."""
    results: list[Gate4LayoutResult] = []
    for n in cartridge_counts:
        layout = CylinderLayout(0, n, 0)
        results.append(evaluate_layout(
            layout,
            body_index=body_index,
            cartridge_kw=STORYBOARD_CARTRIDGE_KW,
            ring_size=n,
            rating_profile="storyboard",
        ))
    body = PHASE1_BODIES[body_index].name
    return Gate4ScalingSummary(tuple(results), body, "x12_homogeneous")


def run_full_gate4_study(
    *,
    ring_sizes: tuple[int, ...] = DEFAULT_RING_SIZES,
    body_index: int = 0,
    include_phase1_open: bool = True,
) -> tuple[Gate4ScalingSummary, ...]:
    """Complete virtual Gate 4 dossier: open Phase-1 sweep + ring studies."""
    summaries: list[Gate4ScalingSummary] = []
    if include_phase1_open:
        ref_total = sum(REFERENCE_LAYOUT)
        ref_max_tier = max(REFERENCE_LAYOUT)
        summaries.append(run_gate4_sweep(
            body_index=body_index,
            max_total=max(max(ring_sizes), ref_total),
            max_per_tier=max(max(ring_sizes), ref_max_tier),
        ))
    summaries.append(run_ring_size_study(
        ring_sizes, rating_profile="storyboard", body_index=body_index,
    ))
    summaries.append(run_ring_size_study(
        ring_sizes, rating_profile="phase1", body_index=body_index,
    ))
    return tuple(summaries)


GATE4_CSV_COLUMNS: tuple[str, ...] = (
    "mode",
    "rating_profile",
    "ring_name",
    "ring_size",
    "tier_mix_kind",
    "active_tier_count",
    "all_three_tiers",
    "n_micro",
    "n_medium",
    "n_large",
    "total_cylinders",
    "rated_kw",
    "highway_l_per_100km",
    "mixed_l_per_100km",
    "ers_passed",
    "ers_total",
    "shortfall_events",
    "score",
    "design_aligned",
    "is_canonical_x12_target",
    "is_reference",
)

TIER_ADVANTAGE_CSV_COLUMNS: tuple[str, ...] = (
    "study",
    "ring_name",
    "ring_size",
    "category",
    "layout",
    "active_tier_count",
    "rated_kw",
    "highway_l_per_100km",
    "mixed_l_per_100km",
    "ers_passed",
    "ers_total",
    "shortfall_events",
    "all_ers_pass",
    "design_aligned",
    "notes",
)


@dataclass(frozen=True)
class TierArchitectureRow:
    """One row in the single- vs two- vs three-tier comparison."""

    category: str
    layout_label: str
    active_tier_count: int
    rated_kw: float
    highway_fuel_l_per_100km: float
    mixed_fuel_l_per_100km: float
    ers_passed: int
    ers_total: int
    shortfall_events: int
    all_ers_pass: bool
    design_aligned: bool
    notes: str = ""

    @classmethod
    def from_result(cls, category: str, result: Gate4LayoutResult, *, notes: str = "") -> TierArchitectureRow:
        return cls(
            category=category,
            layout_label=result.layout.label,
            active_tier_count=count_active_tiers(result.layout),
            rated_kw=result.rated_kw,
            highway_fuel_l_per_100km=result.highway_fuel_l_per_100km,
            mixed_fuel_l_per_100km=result.mixed_fuel_l_per_100km,
            ers_passed=result.ers_passed,
            ers_total=result.ers_total,
            shortfall_events=result.shortfall_events,
            all_ers_pass=result.all_ers_pass,
            design_aligned=result.design_aligned,
            notes=notes,
        )


def _best_homogeneous_single_tier(
    summary: Gate4ScalingSummary,
    tier_index: int,
) -> Gate4LayoutResult | None:
    """Best-scoring layout that is only micro (0), medium (1), or large (2)."""
    pool = [
        r for r in summary.results
        if count_active_tiers(r.layout) == 1 and r.layout.counts[tier_index] > 0
    ]
    if not pool:
        return None
    return max(pool, key=lambda r: r.score)


def build_tier_architecture_comparison(
    summary: Gate4ScalingSummary,
) -> tuple[TierArchitectureRow, ...]:
    """Compare best single-, two-, and three-tier layouts plus reference."""
    rows: list[TierArchitectureRow] = []

    tier_names = ("micro", "medium", "large")
    for i, name in enumerate(tier_names):
        homo = _best_homogeneous_single_tier(summary, i)
        if homo is not None:
            note = "homogeneous single-tier"
            if not homo.all_ers_pass:
                note += f"; fails ERS ({homo.ers_passed}/{homo.ers_total})"
            rows.append(TierArchitectureRow.from_result(
                f"single_{name}", homo, notes=note,
            ))

    for depth, label in ((2, "two_tier"), (3, "three_tier")):
        best = summary.best_with_tier_depth(
            depth, design_aligned_only=True, passing_only=True,
        )
        if best is not None:
            rows.append(TierArchitectureRow.from_result(label, best))

    ref = summary.reference_result()
    if ref is None and summary.mode == "phase1_open":
        ref = evaluate_layout(
            CylinderLayout(*REFERENCE_LAYOUT), rating_profile="phase1",
        )
    if ref is not None:
        rows.append(TierArchitectureRow.from_result(
            "reference_4_2_2", ref, notes="validated Phase-1 vehicle stack",
        ))

    return tuple(rows)


def build_ring_tier_advantage_rows(
    summary: Gate4ScalingSummary,
    ring_sizes: tuple[int, ...],
) -> list[dict[str, str | float | int | bool]]:
    """Per-ring rows: highway-min vs all-three-tier design-aligned picks."""
    rows: list[dict[str, str | float | int | bool]] = []
    study = summary.mode
    for size in ring_sizes:
        ring_name = ring_label(size)
        best_hwy = summary.best_per_ring(design_aligned_only=True).get(size)
        best_3 = summary.best_per_ring(
            design_aligned_only=True, all_three_tiers_only=True,
        ).get(size)
        for category, pick in (
            ("highway_min_design_aligned", best_hwy),
            ("all_three_tier_design_aligned", best_3),
        ):
            if pick is None:
                continue
            rows.append({
                "study": study,
                "ring_name": ring_name,
                "ring_size": size,
                "category": category,
                "layout": pick.layout.label,
                "active_tier_count": count_active_tiers(pick.layout),
                "rated_kw": round(pick.rated_kw, 1),
                "highway_l_per_100km": round(pick.highway_fuel_l_per_100km, 3),
                "mixed_l_per_100km": round(pick.mixed_fuel_l_per_100km, 3),
                "ers_passed": pick.ers_passed,
                "ers_total": pick.ers_total,
                "shortfall_events": pick.shortfall_events,
                "all_ers_pass": pick.all_ers_pass,
                "design_aligned": pick.design_aligned,
                "notes": pick.tier_mix_kind,
            })
    return rows


def gate4_tier_architecture_table(
    summary: Gate4ScalingSummary,
) -> str:
    """Why three tiers beat one or two — ranked comparison for investors."""
    rows = build_tier_architecture_comparison(summary)
    depth_counts = summary.passing_by_tier_depth()
    lines = [
        f"=== Three-tier thesis — {summary.mode} ({summary.body}) ===",
        "  Question: why not one cartridge size? Simulation compares tier depth.",
        f"  ERS-passing layouts: 1-tier={depth_counts[1]}  "
        f"2-tier={depth_counts[2]}  3-tier={depth_counts[3]}",
        f"  {'Category':<22} {'Layout':<10} {'Depth':>5} {'Rated':>7} "
        f"{'Hwy L':>7} {'Mix L':>7} {'ERS':>5} {'Shrt':>4}",
        "  " + "-" * 78,
    ]
    for row in rows:
        ers = f"{row.ers_passed}/{row.ers_total}"
        shrt = str(row.shortfall_events) if row.shortfall_events else "-"
        lines.append(
            f"  {row.category:<22} {row.layout_label:<10} {row.active_tier_count:>5} "
            f"{row.rated_kw:>6.0f}kW {row.highway_fuel_l_per_100km:>7.2f} "
            f"{row.mixed_fuel_l_per_100km:>7.2f} {ers:>5} {shrt:>4}"
        )
        if row.notes:
            lines.append(f"      -> {row.notes}")

    lines.append(_three_tier_thesis_bullets(rows))
    return "\n".join(lines)


def _three_tier_thesis_bullets(rows: tuple[TierArchitectureRow, ...]) -> str:
    """Plain-language advantages extracted from the comparison table."""
    by_cat = {r.category: r for r in rows}
    lines = [
        "",
        "  Three-tier advantages (simulation):",
    ]
    single_rows = [r for r in rows if r.category.startswith("single_")]
    if single_rows and not any(r.all_ers_pass for r in single_rows):
        fails = ", ".join(
            f"{r.category.split('_', 1)[1]} {r.ers_passed}/{r.ers_total}"
            for r in single_rows
        )
        lines.append(
            f"    1. No homogeneous single-tier passes all ERS checks ({fails})."
        )
    two = by_cat.get("two_tier")
    three = by_cat.get("three_tier")
    ref = by_cat.get("reference_4_2_2")
    if two and three and ref:
        if ref.all_ers_pass and not two.all_ers_pass:
            lines.append(
                f"    2. Best two-tier ({two.layout_label}) fails ERS "
                f"{two.ers_passed}/{two.ers_total}; "
                f"three-tier ({three.layout_label}) passes {three.ers_passed}/{three.ers_total}."
            )
        elif ref.all_ers_pass:
            lines.append(
                f"    2. Three-tier stacks pass full ERS ({ref.ers_passed}/{ref.ers_total} "
                f"on reference {ref.layout_label}); two-tier best is {two.layout_label} "
                f"({two.ers_passed}/{two.ers_total})."
            )
        if two.shortfall_events and not ref.shortfall_events:
            lines.append(
                f"    3. Power shortfalls: two-tier={two.shortfall_events} events vs "
                f"reference three-tier=0 — large tier covers peak without oversizing cruise."
            )
        elif three and ref:
            lines.append(
                f"    3. Load matching: micro (cruise) + medium (highway) + large (peak) "
                f"vs running one displacement for all loads."
            )
    lines.append(
        "    4. ATPE fills smallest tier first — three sizes = granular efficiency "
        "across the drive cycle, not one fixed displacement."
    )
    return "\n".join(lines)


def gate4_csv_rows(
    *summaries: Gate4ScalingSummary,
) -> list[dict[str, str | float | int | bool]]:
    """Flatten one or more sweep summaries for CSV export."""
    rows: list[dict[str, str | float | int | bool]] = []
    for summary in summaries:
        for r in summary.results:
            rows.append({
                "mode": summary.mode,
                "rating_profile": r.rating_profile,
                "ring_name": r.ring_name,
                "ring_size": r.ring_size if r.ring_size is not None else "",
                "tier_mix_kind": r.tier_mix_kind,
                "active_tier_count": count_active_tiers(r.layout),
                "all_three_tiers": is_all_three_tiers(r.layout),
                "n_micro": r.layout.n_micro,
                "n_medium": r.layout.n_medium,
                "n_large": r.layout.n_large,
                "total_cylinders": r.layout.total_cylinders,
                "rated_kw": round(r.rated_kw, 1),
                "highway_l_per_100km": round(r.highway_fuel_l_per_100km, 3),
                "mixed_l_per_100km": round(r.mixed_fuel_l_per_100km, 3),
                "ers_passed": r.ers_passed,
                "ers_total": r.ers_total,
                "shortfall_events": r.shortfall_events,
                "score": round(r.score, 2),
                "design_aligned": r.design_aligned,
                "is_canonical_x12_target": r.is_canonical_x12_target,
                "is_reference": r.is_reference,
            })
    return rows


def write_gate4_tier_advantage_csv(
    path: Path | str,
    *,
    phase1_open: Gate4ScalingSummary,
    storyboard: Gate4ScalingSummary,
    ring_sizes: tuple[int, ...],
) -> Path:
    """Write per-ring and Phase-1 tier-depth comparison summary."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str | float | int | bool]] = []
    for row in build_tier_architecture_comparison(phase1_open):
        rows.append({
            "study": phase1_open.mode,
            "ring_name": "",
            "ring_size": "",
            "category": row.category,
            "layout": row.layout_label,
            "active_tier_count": row.active_tier_count,
            "rated_kw": round(row.rated_kw, 1),
            "highway_l_per_100km": round(row.highway_fuel_l_per_100km, 3),
            "mixed_l_per_100km": round(row.mixed_fuel_l_per_100km, 3),
            "ers_passed": row.ers_passed,
            "ers_total": row.ers_total,
            "shortfall_events": row.shortfall_events,
            "all_ers_pass": row.all_ers_pass,
            "design_aligned": row.design_aligned,
            "notes": row.notes,
        })
    rows.extend(build_ring_tier_advantage_rows(storyboard, ring_sizes))
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TIER_ADVANTAGE_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def write_gate4_scaling_csv(
    path: Path | str,
    *summaries: Gate4ScalingSummary,
) -> Path:
    """Write one or more sweep summaries to CSV."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = gate4_csv_rows(*summaries)
    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=GATE4_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return out


def gate4_scaling_table(
    summary: Gate4ScalingSummary,
    *,
    top_n: int = 12,
    design_aligned_only: bool = False,
) -> str:
    """Compact ranked table of the best layouts."""
    label = "design-aligned " if design_aligned_only else ""
    lines = [
        f"=== Gate 4 virtual scaling — {label}{summary.mode} ({summary.body}) ===",
        f"  Layouts evaluated: {len(summary.results)}  "
        f"ERS-passing: {len(summary.passing)}  "
        f"design-aligned pass: {len(summary.design_aligned_passing)}",
        f"  {'Layout':<12} {'Ring':>4} {'Cyl':>3} {'Rated':>7} {'Hwy L':>7} "
        f"{'Mix L':>7} {'ERS':>5} {'Mix':>10} {'Score':>8}",
        "  " + "-" * 78,
    ]
    rows = summary.top_design_aligned(top_n) if design_aligned_only else summary.top(top_n)
    for r in rows:
        mark = "*" if r.is_reference else ("+" if r.is_canonical_x12_target else " ")
        ers = f"{r.ers_passed}/{r.ers_total}"
        ring = r.ring_name or "-"
        lines.append(
            f" {mark}{r.layout.label:<11} {ring:>4} {r.layout.total_cylinders:>3} "
            f"{r.rated_kw:>6.0f}kW {r.highway_fuel_l_per_100km:>7.2f} "
            f"{r.mixed_fuel_l_per_100km:>7.2f} {ers:>5} "
            f"{r.tier_mix_kind[:10]:>10} {r.score:>8.1f}"
        )
    lines.append("  (* = Phase-1 reference 4/2/2, + = canonical X12 target 0/12/0)")
    return "\n".join(lines)


def gate4_ring_sweet_spot_table(
    summary: Gate4ScalingSummary,
    *,
    design_aligned_only: bool = True,
    include_three_tier_column: bool = True,
) -> str:
    """Per-ring-size best layout: highway-min and all-three-tier design-aligned."""
    scope = "design-aligned " if design_aligned_only else ""
    lines = [
        f"=== Sweet spot per ring — {scope}{summary.mode} ({summary.body}) ===",
    ]
    if include_three_tier_column:
        lines += [
            f"  {'Ring':>4} {'Hwy-min':<10} {'3-tier':<10} {'Hwy L':>7} "
            f"{'3-tier L':>8} {'ERS':>5} {'3t ERS':>7}",
            "  " + "-" * 68,
        ]
    else:
        lines += [
            f"  {'Ring':>4} {'Best layout':<12} {'Kind':<18} {'Rated':>7} "
            f"{'Hwy L':>7} {'ERS':>5}",
            "  " + "-" * 62,
        ]
    best_map = summary.best_per_ring(design_aligned_only=design_aligned_only)
    best_3_map = summary.best_per_ring(
        design_aligned_only=design_aligned_only,
        all_three_tiers_only=True,
    ) if include_three_tier_column else {}
    for size, best in sorted(best_map.items()):
        if include_three_tier_column:
            three = best_3_map.get(size)
            three_label = three.layout.label if three else "-"
            three_hwy = f"{three.highway_fuel_l_per_100km:.2f}" if three else "-"
            three_ers = (
                f"{three.ers_passed}/{three.ers_total}" if three else "-"
            )
            lines.append(
                f"  {ring_label(size):>4} {best.layout.label:<10} {three_label:<10} "
                f"{best.highway_fuel_l_per_100km:>7.2f} {three_hwy:>8} "
                f"{best.ers_passed}/{best.ers_total} {three_ers:>7}"
            )
        else:
            lines.append(
                f"  {ring_label(size):>4} {best.layout.label:<12} "
                f"{best.tier_mix_kind:<18} {best.rated_kw:>6.0f}kW "
                f"{best.highway_fuel_l_per_100km:>7.2f} {best.ers_passed}/{best.ers_total}"
            )
        if summary.mode == "storyboard_ring":
            homo_medium = next(
                (r for r in summary.for_ring(size)
                 if r.layout.counts == (0, size, 0)),
                None,
            )
            if homo_medium:
                status = "PASS" if homo_medium.all_ers_pass else f"{homo_medium.ers_passed}/9"
                lines.append(
                    f"       canonical all-medium 0/{size}/0: "
                    f"{homo_medium.highway_fuel_l_per_100km:.2f}L ERS {status}"
                )
    if include_three_tier_column:
        lines.append(
            "  Hwy-min = best design-aligned score; 3-tier = best with micro+medium+large."
        )
    return "\n".join(lines)


def gate4_scaling_report(
    *,
    ring_sizes: tuple[int, ...] = DEFAULT_RING_SIZES,
    max_total: int | None = None,
    max_per_tier: int | None = None,
    include_phase1_open: bool = True,
) -> str:
    """Full virtual Gate 4 report for demos and evidence pack."""
    if max_total is None:
        max_total = max(ring_sizes)
    if max_per_tier is None:
        max_per_tier = max(ring_sizes)

    summaries = run_full_gate4_study(
        ring_sizes=ring_sizes,
        include_phase1_open=include_phase1_open,
    )
    storyboard = next(s for s in summaries if s.mode == "storyboard_ring")
    phase1_ring = next(s for s in summaries if s.mode == "phase1_ring")
    phase1_open = next(
        (s for s in summaries if s.mode == "phase1_open"), None,
    )

    global_best = storyboard.best_design_aligned or storyboard.best

    lines = [
        "=== Virtual Gate 4 multi-cylinder scaling (simulation — not measured) ===",
        f"  Ring sizes studied: {', '.join(ring_label(n) for n in ring_sizes)}",
        f"  Tier mixes per ring: all micro/medium/large combinations summing to N",
        f"  Rating profiles: phase1 (7.5/40/60 kW) + storyboard (20/78/120 kW)",
        f"  Fuel accounting: charge-sustaining SoC ({phase1_config_for(PHASE1_BODIES[0]).battery.soc_target:.2f})",
        "",
        "  ARCHITECTURE RULE: storyboard rings require >=1 medium (78 kW) cartridge.",
        "  All-micro homogeneous layouts are simulation artifacts (tier-1 eta 0.44",
        "  beats medium 0.41 on paper) — NOT valid ring recommendations.",
        "",
    ]

    if phase1_open is not None:
        lines += [
            gate4_tier_architecture_table(phase1_open),
            "",
            gate4_scaling_table(phase1_open, top_n=8, design_aligned_only=True),
            "",
        ]

    lines += [
        "=== Storyboard rings — highway-min vs all-three-tier (medium required) ===",
        gate4_ring_sweet_spot_table(
            storyboard, design_aligned_only=True, include_three_tier_column=True,
        ),
        "",
        gate4_scaling_table(storyboard, top_n=10, design_aligned_only=True),
        "",
        "=== Phase-1 rings — design-aligned sweet spots ===",
        gate4_ring_sweet_spot_table(phase1_ring, design_aligned_only=True),
        "",
        gate4_scaling_table(phase1_ring, top_n=8, design_aligned_only=True),
        "",
        "=== Global recommendation (AWD SUV, design-aligned, simulation only) ===",
    ]
    ref = phase1_open.reference_result() if phase1_open else None
    if ref and ref.all_ers_pass:
        lines += [
            f"  Validated three-tier vehicle: {ref.layout.label} "
            f"({ref.rated_kw:.0f} kW, ERS {ref.ers_passed}/{ref.ers_total})",
            f"    Highway CS fuel   : {ref.highway_fuel_l_per_100km:.2f} L/100 km",
            f"    Mixed cycle fuel  : {ref.mixed_fuel_l_per_100km:.2f} L/100 km",
            "",
        ]
    if global_best:
        lines += [
            f"  Best design-aligned : {global_best.ring_name or 'open'} "
            f"{global_best.layout.label} ({global_best.rating_profile})",
            f"    Tier mix          : {global_best.tier_mix_kind}",
            f"    Rated power       : {global_best.rated_kw:.0f} kW",
            f"    Highway CS fuel   : {global_best.highway_fuel_l_per_100km:.2f} L/100 km",
            f"    Mixed cycle fuel  : {global_best.mixed_fuel_l_per_100km:.2f} L/100 km",
            f"    ERS               : {global_best.ers_passed}/{global_best.ers_total}",
        ]
    lines += [
        "",
        _storyboard_ring_notes(storyboard, ring_sizes),
        "",
        _design_artifact_warning(storyboard),
        "",
        "  DISCLAIMER: layout search uses tier-efficiency tables and does not",
        "  model ring phasing, NVH, or manufacturing. Prefix claims with",
        "  'simulation shows…'. Hardware sync validation still required.",
    ]
    return "\n".join(lines)


def _design_artifact_warning(storyboard: Gate4ScalingSummary) -> str:
    """Explain why all-micro homogeneous rows are not design recommendations."""
    lines = [
        "  Why all-micro (N/0/0) ranked high before — and why it is wrong:",
        "    - Tier-1 table efficiency is 0.44 vs tier-2 medium 0.41 (fixed inputs).",
        "    - That lets micro pass brake-thermal ERS; all-medium 12/0/0 fails 7/9.",
        "    - Highway fuel on storyboard rings clusters near CS levels (~4.1–4.6 L/100km)",
        "      when layouts are oversized for steady cruise; the twin does not yet model",
        "    - PHOENIX ring design is medium-cartridge centric (78 kW), not all-micro.",
        "  Use design_aligned=true rows in CSV for architecture decisions.",
    ]
    raw_x8 = storyboard.best_per_ring(design_aligned_only=False).get(8)
    aligned_x8 = storyboard.best_per_ring(design_aligned_only=True).get(8)
    if raw_x8 and aligned_x8 and raw_x8.layout.label != aligned_x8.layout.label:
        lines.append(
            f"  Example X8: raw-best {raw_x8.layout.label} ({raw_x8.tier_mix_kind})"
            f" vs design-aligned {aligned_x8.layout.label} ({aligned_x8.tier_mix_kind})"
        )
    return "\n".join(lines)


def _storyboard_ring_notes(
    storyboard: Gate4ScalingSummary,
    ring_sizes: tuple[int, ...],
) -> str:
    lines = ["  Storyboard ring notes (design-aligned):"]
    for size in ring_sizes:
        ring_results = storyboard.for_ring(size)
        aligned_pass = sum(
            1 for r in ring_results if r.all_ers_pass and r.design_aligned
        )
        best = storyboard.best_per_ring(design_aligned_only=True).get(size)
        note = (
            f"    {ring_label(size)}: {aligned_pass}/{len(ring_results)} "
            f"design-aligned pass"
        )
        if best:
            note += (
                f"; best={best.layout.label} ({best.tier_mix_kind}, "
                f"{best.highway_fuel_l_per_100km:.2f} L/100km)"
            )
        homo_medium = next(
            (r for r in ring_results if r.layout.counts == (0, size, 0)),
            None,
        )
        if homo_medium and not homo_medium.all_ers_pass:
            note += (
                f"; canonical all-medium 0/{size}/0 fails ERS "
                f"({homo_medium.ers_passed}/9) — needs eta boost or tier blend"
            )
        lines.append(note)
    x12_canon = next(
        (r for r in storyboard.for_ring(12) if r.is_canonical_x12_target), None,
    )
    if x12_canon:
        lines.append(
            f"    X12 production target 0/12/0: {x12_canon.rated_kw:.0f} kW rated, "
            f"ERS {x12_canon.ers_passed}/9 — hardware must hit medium-tier BTE"
        )
    return "\n".join(lines)
