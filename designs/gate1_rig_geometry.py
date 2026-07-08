"""Parametric Gate 1 lab-rig geometry derived from the digital twin.

All dimensions trace to ``tier_physics_profile()`` and ``Gate1BenchTargets`` in
``digital_twin.single_cylinder``. Used by the interactive viewer and FreeCAD macro.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

from digital_twin.single_cylinder import (
    Gate1BenchTargets,
    PHOENIX_X12_STROKE_MM,
    tier_physics_profile,
)


@dataclass(frozen=True)
class Gate1RigGeometry:
    """Millimetre-scale layout for a single opposed-piston test cartridge on a bedplate."""

    tier_index: int
    tier_label: str
    displacement_cc: float
    compression_ratio: float
    stroke_mm: float
    bore_mm: float
    piston_length_mm: float
    bounce_length_mm: float
    chamber_gap_mm: float
    bedplate_length_mm: float
    bedplate_width_mm: float
    bedplate_height_mm: float
    shield_clearance_mm: float
    coil_length_mm: float
    load_bank_offset_mm: float

    @property
    def piston_radius_mm(self) -> float:
        return self.bore_mm * 0.5

    @property
    def half_stroke_mm(self) -> float:
        return self.stroke_mm * 0.5


TIER_LABELS: tuple[str, ...] = ("Tier 1 (micro)", "Tier 2 (medium)", "Tier 3 (large)")
TIER_DISPLACEMENT_CC: tuple[float, ...] = (100.0, 300.0, 750.0)


def bore_mm_from_area_m2(area_m2: float) -> float:
    return math.sqrt(max(area_m2, 1e-12) * 4.0 / math.pi) * 1000.0


def build_rig_geometry(tier_index: int = 1) -> Gate1RigGeometry:
    """Build rig layout for tier 0=micro, 1=medium (default), 2=large."""
    idx = max(0, min(tier_index, 2))
    compression_ratio, fp_cfg = tier_physics_profile(idx)
    stroke_mm = fp_cfg.stroke_m * 1000.0
    bore_mm = bore_mm_from_area_m2(fp_cfg.piston_area_m2)
    bounce_mm = fp_cfg.bounce_clearance_m * 1000.0 + stroke_mm * 0.35
    piston_len = max(28.0, stroke_mm * 0.55)
    chamber_gap = stroke_mm * 0.15
    span = 2.0 * bounce_mm + 2.0 * piston_len + chamber_gap
    return Gate1RigGeometry(
        tier_index=idx,
        tier_label=TIER_LABELS[idx],
        displacement_cc=TIER_DISPLACEMENT_CC[idx],
        compression_ratio=compression_ratio,
        stroke_mm=stroke_mm,
        bore_mm=bore_mm,
        piston_length_mm=piston_len,
        bounce_length_mm=bounce_mm,
        chamber_gap_mm=chamber_gap,
        bedplate_length_mm=span + 320.0,
        bedplate_width_mm=max(260.0, bore_mm * 4.5),
        bedplate_height_mm=35.0,
        shield_clearance_mm=80.0,
        coil_length_mm=piston_len * 0.85,
        load_bank_offset_mm=span * 0.5 + 180.0,
    )


def piston_centers_mm(
    geom: Gate1RigGeometry,
    left_travel_mm: float,
) -> tuple[float, float, float]:
    """Return (left_piston_x, right_piston_x, combustion_center_x) in rig coordinates."""
    left_bounce_outer = -geom.bounce_length_mm
    left_piston_x = left_bounce_outer + geom.piston_length_mm * 0.5 + left_travel_mm
    right_travel = geom.stroke_mm - left_travel_mm
    right_bounce_outer = geom.bounce_length_mm + geom.chamber_gap_mm + 2.0 * geom.piston_length_mm
    right_piston_x = right_bounce_outer - geom.piston_length_mm * 0.5 - right_travel
    combustion_center = (left_piston_x + right_piston_x) * 0.5
    return left_piston_x, right_piston_x, combustion_center


def rig_axis_extent_mm(geom: Gate1RigGeometry) -> tuple[float, float]:
    """Min/max X for the cartridge assembly (excluding load bank)."""
    left = -geom.bounce_length_mm - geom.coil_length_mm - 40.0
    right = (
        geom.bounce_length_mm
        + geom.chamber_gap_mm
        + 2.0 * geom.piston_length_mm
        + geom.coil_length_mm
        + 40.0
    )
    return left, right


def bench_targets() -> Gate1BenchTargets:
    return Gate1BenchTargets()
