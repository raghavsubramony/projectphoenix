"""Physical constants and shared helpers for Phoenix V3."""

from __future__ import annotations

from pathlib import Path

R_AIR = 287.0  # J/(kg·K)
GAMMA = 1.35

PACKAGE_DIR = Path(__file__).resolve().parent
DESIGNS_DIR = PACKAGE_DIR.parent
REPO_ROOT = DESIGNS_DIR.parent


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(v, hi))
