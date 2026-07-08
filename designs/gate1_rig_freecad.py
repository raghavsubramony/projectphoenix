"""FreeCAD macro — Gate 1 opposed-piston lab rig solid model.

Run inside FreeCAD (0.21+):

  1. Open FreeCAD
  2. Macro → Macros… → gate1_rig_freecad.py → Execute
  3. Or: ``freecadcmd designs/gate1_rig_freecad.py`` (headless export)

Exports STEP + FCStd next to this script. Dimensions match
``designs/gate1_rig_geometry.py`` / digital twin Tier 2 medium cartridge.

Note: FreeCAD is NOT a project dependency — this file is optional for mechanical CAD.
"""

from __future__ import annotations

import math
import os
import sys

# Allow importing twin geometry when launched from FreeCAD macro path.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_SCRIPT_DIR)
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

try:
    import FreeCAD as App
    import Part
    import FreeCADGui as Gui
except ImportError as exc:
    raise SystemExit(
        "FreeCAD Python modules not found. Run this script inside FreeCAD, not plain python.\n"
        f"Original error: {exc}"
    ) from exc

from gate1_rig_geometry import build_rig_geometry, piston_centers_mm  # noqa: E402


def _cylinder_part(
    doc: App.Document,
    name: str,
    center_x: float,
    radius: float,
    length: float,
    color: tuple[float, float, float, float],
) -> Part.Feature:
    cyl = Part.makeCylinder(radius, length, App.Vector(center_x - length * 0.5, 0.0, 0.0), App.Vector(1, 0, 0))
    feat = doc.addObject("Part::Feature", name)
    feat.Shape = cyl
    if Gui:
        feat.ViewObject.ShapeColor = color
        feat.ViewObject.Transparency = int(color[3] * 100) if len(color) > 3 else 0
    return feat


def _box_part(
    doc: App.Document,
    name: str,
    cx: float,
    cy: float,
    cz: float,
    lx: float,
    ly: float,
    lz: float,
    color: tuple[float, float, float],
    transparency: int = 0,
) -> Part.Feature:
    box = Part.makeBox(lx, ly, lz, App.Vector(cx - lx * 0.5, cy - ly * 0.5, cz - lz * 0.5))
    feat = doc.addObject("Part::Feature", name)
    feat.Shape = box
    if Gui:
        feat.ViewObject.ShapeColor = color
        feat.ViewObject.Transparency = transparency
    return feat


def build_gate1_rig_document(tier_index: int = 1, travel_mm: float | None = None) -> App.Document:
    geom = build_rig_geometry(tier_index)
    if travel_mm is None:
        travel_mm = geom.stroke_mm * 0.35

    doc = App.newDocument("Gate1LabRig")
    r = geom.piston_radius_mm
    z_c = geom.bore_mm * 0.45

    _box_part(
        doc,
        "Bedplate",
        0.0,
        0.0,
        geom.bedplate_height_mm * 0.5,
        geom.bedplate_length_mm,
        geom.bedplate_width_mm,
        geom.bedplate_height_mm,
        (0.29, 0.33, 0.41),
    )

    _box_part(
        doc,
        "BlastShield",
        0.0,
        0.0,
        geom.bore_mm * 0.9,
        geom.bedplate_length_mm * 0.55,
        geom.bedplate_width_mm * 0.7,
        geom.bore_mm * 1.6,
        (0.56, 0.80, 0.96),
        transparency=85,
    )

    left_bounce_x = -geom.bounce_length_mm * 0.5
    right_bounce_x = geom.bounce_length_mm * 0.5 + geom.chamber_gap_mm + 2.0 * geom.piston_length_mm
    _cylinder_part(doc, "BounceLeft", left_bounce_x, r * 1.05, geom.bounce_length_mm, (0.63, 0.68, 0.75, 0.0))
    _cylinder_part(doc, "BounceRight", right_bounce_x, r * 1.05, geom.bounce_length_mm, (0.63, 0.68, 0.75, 0.0))

    _cylinder_part(
        doc,
        "CombustionLiner",
        0.0,
        r * 0.98,
        geom.chamber_gap_mm + geom.piston_length_mm * 0.4,
        (0.95, 0.45, 0.45, 0.35),
    )

    left_x, right_x, _ = piston_centers_mm(geom, travel_mm)
    _cylinder_part(doc, "PistonLeft", left_x, r, geom.piston_length_mm, (0.18, 0.20, 0.23, 0.0))
    _cylinder_part(doc, "PistonRight", right_x, r, geom.piston_length_mm, (0.18, 0.20, 0.23, 0.0))

    for tag, px, sign in (("CoilLeft", left_x, -1.0), ("CoilRight", right_x, 1.0)):
        cx = px + sign * (geom.piston_length_mm * 0.5 + geom.coil_length_mm * 0.45)
        _cylinder_part(doc, tag, cx, r * 1.15, geom.coil_length_mm, (0.62, 0.48, 0.92, 0.0))

    _box_part(
        doc,
        "LoadBank",
        geom.load_bank_offset_mm,
        0.0,
        z_c,
        140.0,
        90.0,
        70.0,
        (0.28, 0.73, 0.47),
    )
    _box_part(
        doc,
        "DCBus",
        geom.load_bank_offset_mm - 90.0,
        -geom.bedplate_width_mm * 0.35,
        z_c * 0.5,
        80.0,
        40.0,
        25.0,
        (0.93, 0.79, 0.29),
    )

    # Instrument stand-ins
    for i, sx in enumerate((left_x - 30.0, 0.0, right_x + 30.0)):
        _cylinder_part(doc, f"Sensor_{i+1}", sx, 4.0, 12.0, (0.96, 0.68, 0.33, 0.0))

    doc.recompute()
    if Gui:
        Gui.activeDocument().activeView().viewIsometric()
        Gui.SendMsgToActiveView("ViewFit")
    return doc


def export_artifacts(doc: App.Document, out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    fcstd = os.path.join(out_dir, "gate1_rig_lab.FCStd")
    step = os.path.join(out_dir, "gate1_rig_lab.step")
    doc.saveAs(fcstd)
    objects = [obj for obj in doc.Objects if hasattr(obj, "Shape")]
    Part.export(objects, step)
    print(f"Saved {fcstd}")
    print(f"Saved {step}")


def main() -> None:
    tier = int(os.environ.get("GATE1_TIER", "1"))
    doc = build_gate1_rig_document(tier_index=tier)
    export_artifacts(doc, _SCRIPT_DIR)
    print(
        f"Gate 1 rig CAD built — {build_rig_geometry(tier).tier_label}, "
        f"{build_rig_geometry(tier).stroke_mm:.0f} mm stroke"
    )


if __name__ == "__main__":
    main()
