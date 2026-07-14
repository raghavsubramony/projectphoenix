"""Audit Phoenix V3 tuning corpora and validate exported best-tuning JSON files."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.tier_profiles import TIER_LARGE, TIER_MEDIUM, TIER_MICRO
from designs.phoenix_v3_optimizer import (
    PARAM_NAMES,
    TuningVector,
    search_bounds_for_tier,
    validate_best_for_export,
)

DESIGNS = _REPO / "designs"


def _stable_mask(df: pd.DataFrame) -> pd.Series:
    return df["stable"].astype(str).isin(["1", "1.0", "True", "true"])


def audit_corpus(name: str, path: Path) -> dict:
    df = pd.read_csv(path)
    stable = df[_stable_mask(df)]
    ke = stable["ke_power_fraction"].fillna(0) if "ke_power_fraction" in stable else pd.Series(0, index=stable.index)
    harvest = stable[ke < 0.15]
    anti = stable[(stable["max_bdc_mm"] > 21) & (ke > 0.10)]

    top_score = harvest.nlargest(5, "score")
    sens: list[tuple[str, float]] = []
    y = harvest["net_efficiency"].to_numpy()
    if len(harvest) > 50 and np.std(y) > 1e-9:
        for param in PARAM_NAMES:
            if param not in harvest.columns:
                continue
            x = harvest[param].to_numpy(dtype=float)
            if np.std(x) < 1e-12:
                continue
            corr = float(np.corrcoef(x, y)[0, 1])
            if not np.isnan(corr):
                sens.append((param, corr))
    sens.sort(key=lambda item: abs(item[1]), reverse=True)

    return {
        "name": name,
        "rows": len(df),
        "stable": len(stable),
        "harvest_like": len(harvest),
        "anti_pattern": len(anti),
        "net_p50": float(harvest["net_efficiency"].median()) if len(harvest) else 0.0,
        "net_p90": float(harvest["net_efficiency"].quantile(0.9)) if len(harvest) else 0.0,
        "net_max": float(harvest["net_efficiency"].max()) if len(harvest) else 0.0,
        "bdc_p50": float(harvest["max_bdc_mm"].median()) if len(harvest) else 0.0,
        "ke_p90": float(ke.quantile(0.9)) if len(harvest) else 0.0,
        "top_score": top_score,
        "sens": sens[:6],
    }


def print_audit(a: dict, path: Path) -> None:
    print(f"\n--- {a['name']} ({path.name}) ---")
    print(
        f"  trials: {a['rows']:,}  stable: {a['stable']:,}  "
        f"harvest-like (KE<15%): {a['harvest_like']:,}"
    )
    anti_pct = 100.0 * a["anti_pattern"] / max(a["stable"], 1)
    print(f"  anti-pattern (BDC>21 and KE>10%): {a['anti_pattern']:,} ({anti_pct:.1f}% of stable)")
    print(
        f"  net efficiency: p50={a['net_p50']:.1%}  p90={a['net_p90']:.1%}  max={a['net_max']:.1%}"
    )
    print(f"  BDC median={a['bdc_p50']:.1f}mm  KE p90={a['ke_p90']:.1%}")
    print("  Top 3 by harvest score (corpus):")
    for _, row in a["top_score"].head(3).iterrows():
        ke = row.get("ke_power_fraction", 0.0)
        print(
            f"    score={row['score']:.4f} net={row['net_efficiency']:.1%} "
            f"cap={row['capture_fraction']:.1%} bdc={row['max_bdc_mm']:.1f}mm "
            f"freq={row['frequency_hz']:.1f}Hz elec={row['elec_energy_j']:.0f}J ke={ke:.1%}"
        )
    if a["sens"]:
        print("  Param sensitivity (net eff, stable harvest-like):")
        for param, corr in a["sens"]:
            sign = "+" if corr >= 0 else "-"
            print(f"    {sign} {param:28s} {corr:+.3f}")


def validate_json(label: str, jpath: Path, tier_index: int | None) -> None:
    payload = json.loads(jpath.read_text(encoding="utf-8"))
    bounds = search_bounds_for_tier(tier_index) if tier_index is not None else None
    vec = TuningVector.from_dict(payload["parameters"], bounds=bounds)
    trial, extras = validate_best_for_export(vec, tier_index=tier_index)
    ring = payload.get("ring_validation", extras)
    print(f"\n[{label}] {jpath.name}")
    print(
        f"  net={trial.net_efficiency:.1%} score={trial.score:.4f} "
        f"cap={trial.capture_fraction:.1%}"
    )
    freq = vec.to_dict(bounds)["frequency_hz"]
    print(
        f"  bdc={trial.max_bdc_mm:.1f}mm elec={trial.elec_energy_j:.0f}J "
        f"freq={freq:.1f}Hz"
    )
    print(
        f"  spr={trial.spring_recovery:.1%} ke={trial.ke_power_fraction:.1%} "
        f"unharv={trial.unharvested_fraction:.1%}"
    )
    ring_eff = ring.get("ring_efficiency", extras.get("ring_efficiency", 0.0))
    ring_gap = ring.get("ring_gap_pp", extras.get("ring_gap_pp", 0.0))
    ring_valid = ring.get("ring_valid", extras.get("ring_valid"))
    print(f"  ring={ring_eff:.1%} gap={ring_gap * 100:.3f}pp valid={ring_valid}")


def main() -> None:
    profiles = [
        ("v2 legacy", DESIGNS / "phoenix_v3_tuning_corpus_v2.csv"),
        ("micro 100cc", TIER_MICRO.corpus_path),
        ("medium 300cc", TIER_MEDIUM.corpus_path),
        ("large 750cc", TIER_LARGE.corpus_path),
        ("actuator", DESIGNS / "phoenix_v3_tuning_corpus_actuator.csv"),
    ]

    print("=" * 78)
    print("PHOENIX V3 CORPUS AUDIT")
    print("=" * 78)

    for label, path in profiles:
        if not path.exists():
            print(f"\n[{label}] MISSING {path.name}")
            continue
        print_audit(audit_corpus(label, path), path)

    print("\n" + "=" * 78)
    print("EXPORTED BEST-TUNING VALIDATION (24-cycle + ring)")
    print("=" * 78)

    configs = [
        ("v2 baseline", DESIGNS / "phoenix_v3_best_tuning_v2.json", None),
        ("v3 medium", DESIGNS / "phoenix_v3_best_tuning_v3.json", TIER_MEDIUM.tier_index),
        ("micro", DESIGNS / "phoenix_v3_best_tuning_micro.json", TIER_MICRO.tier_index),
        ("large", DESIGNS / "phoenix_v3_best_tuning_large.json", TIER_LARGE.tier_index),
    ]
    for label, jpath, tier_index in configs:
        if jpath.is_file():
            validate_json(label, jpath, tier_index)

    print("\n" + "=" * 78)
    print("HEAD-TO-HEAD (medium tier physics, validated)")
    print("=" * 78)
    for label, fname in [
        ("v2 @ medium tier", "phoenix_v3_best_tuning_v2.json"),
        ("v3 medium export", "phoenix_v3_best_tuning_v3.json"),
    ]:
        payload = json.loads((DESIGNS / fname).read_text(encoding="utf-8"))
        bounds = search_bounds_for_tier(TIER_MEDIUM.tier_index)
        vec = TuningVector.from_dict(payload["parameters"], bounds=bounds)
        trial, extras = validate_best_for_export(vec, tier_index=TIER_MEDIUM.tier_index)
        print(
            f"  {label}: net={trial.net_efficiency:.1%} cap={trial.capture_fraction:.1%} "
            f"bdc={trial.max_bdc_mm:.1f}mm score={trial.score:.4f} ring={extras['ring_efficiency']:.1%}"
        )


if __name__ == "__main__":
    main()
