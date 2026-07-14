"""Cartridge tier artifacts — 100 / 300 / 750 cc V3 sims and tuning files.

Convention (opposed piston):
  total_displacement_cc = 2 × displacement_per_side_cc
  e.g. 100 cc total = 50 cc/side, 300 cc = 150 cc/side (Gate 1 reference).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from designs.phoenix_v3.constants import DESIGNS_DIR

OUT_DIR = DESIGNS_DIR


@dataclass(frozen=True)
class TierCartridgeProfile:
    """One ATPE cartridge size class with its own corpus + best-tuning JSON."""

    key: str
    tier_index: int
    name: str
    total_displacement_cc: float
    corpus_filename: str
    best_tuning_filename: str

    @property
    def corpus_path(self) -> Path:
        return OUT_DIR / self.corpus_filename

    @property
    def best_tuning_path(self) -> Path:
        return OUT_DIR / self.best_tuning_filename

    @property
    def displacement_per_side_cc(self) -> float:
        return self.total_displacement_cc * 0.5


TIER_MICRO = TierCartridgeProfile(
    key="micro",
    tier_index=0,
    name="Tier 1 (micro)",
    total_displacement_cc=100.0,
    corpus_filename="phoenix_v3_tuning_corpus_micro.csv",
    best_tuning_filename="phoenix_v3_best_tuning_micro.json",
)

TIER_MEDIUM = TierCartridgeProfile(
    key="medium",
    tier_index=1,
    name="Tier 2 (medium)",
    total_displacement_cc=300.0,
    corpus_filename="phoenix_v3_tuning_corpus_v3.csv",
    best_tuning_filename="phoenix_v3_best_tuning_v3.json",
)

TIER_LARGE = TierCartridgeProfile(
    key="large",
    tier_index=2,
    name="Tier 3 (large)",
    total_displacement_cc=750.0,
    corpus_filename="phoenix_v3_tuning_corpus_large.csv",
    best_tuning_filename="phoenix_v3_best_tuning_large.json",
)

TIER_PROFILES: tuple[TierCartridgeProfile, ...] = (
    TIER_MICRO,
    TIER_MEDIUM,
    TIER_LARGE,
)

TIER_BY_KEY: dict[str, TierCartridgeProfile] = {p.key: p for p in TIER_PROFILES}
TIER_BY_INDEX: dict[int, TierCartridgeProfile] = {p.tier_index: p for p in TIER_PROFILES}

# Legacy medium paths (unchanged behaviour when --tier not passed).
LEGACY_BEST_TUNING_CANDIDATES: tuple[str, ...] = (
    "phoenix_v3_best_tuning_v3.json",
    "phoenix_v3_best_tuning_v2.json",
    "phoenix_v3_best_tuning.json",
)


def profile_for_tier_index(tier_index: int) -> TierCartridgeProfile:
    return TIER_BY_INDEX[max(0, min(tier_index, 2))]


def profile_for_key(key: str) -> TierCartridgeProfile:
    normalized = key.strip().lower()
    aliases = {
        "micro": "micro",
        "tier1": "micro",
        "tier_1": "micro",
        "100": "micro",
        "medium": "medium",
        "tier2": "medium",
        "tier_2": "medium",
        "300": "medium",
        "large": "large",
        "tier3": "large",
        "tier_3": "large",
        "750": "large",
    }
    resolved = aliases.get(normalized, normalized)
    if resolved not in TIER_BY_KEY:
        raise ValueError(
            f"Unknown tier {key!r}; use micro | medium | large (100 / 300 / 750 cc)"
        )
    return TIER_BY_KEY[resolved]


def resolve_tier_tuning_path(
    tier_index: int,
    explicit: Path | str | None = None,
) -> Path | None:
    """Best-tuning JSON for a tier, with optional explicit override."""
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_file() else None
    profile = profile_for_tier_index(tier_index)
    if profile.best_tuning_path.is_file():
        return profile.best_tuning_path
    if tier_index == 1:
        for name in LEGACY_BEST_TUNING_CANDIDATES:
            path = OUT_DIR / name
            if path.is_file():
                return path
    return None
