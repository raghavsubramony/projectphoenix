#!/usr/bin/env python3
"""Export Gate 1 lab-rig specification from validated Phoenix V3 physics."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from digital_twin.phoenix_v3_bridge import export_gate1_rig_dossier


def main() -> None:
    path = export_gate1_rig_dossier()
    print(f"Gate 1 rig dossier: {path.resolve()}")
    print(f"JSON spec: {(path.parent / 'GATE1-RIG-SPEC-FROM-V3.json').resolve()}")


if __name__ == "__main__":
    main()
