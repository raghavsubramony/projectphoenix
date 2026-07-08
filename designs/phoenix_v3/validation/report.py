"""Canonical efficiency report CLI — single command for all validation packs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.constants import DESIGNS_DIR
from designs.phoenix_v3.efficiency import (
    CANONICAL_EFFICIENCY_DEFINITION,
    compute_canonical_efficiency,
    print_canonical_efficiency_report,
)


def load_best_tuning_config(base: PhoenixV3Config | None = None) -> PhoenixV3Config:
    from designs.phoenix_v3_optimizer import TuningVector

    cfg = base or PhoenixV3Config()
    for name in (
        "phoenix_v3_best_tuning_v3.json",
        "phoenix_v3_best_tuning_v2.json",
        "phoenix_v3_best_tuning.json",
    ):
        path = DESIGNS_DIR / name
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return TuningVector.from_dict(payload["parameters"]).to_config(cfg)
    return cfg


def main() -> None:
    from designs.phoenix_v3_simulation import PhoenixV3Simulator, apply_generator_cooling

    parser = argparse.ArgumentParser(
        description="Phoenix V3 canonical efficiency report (single definition)",
    )
    parser.add_argument("--cycles", type=int, default=24)
    parser.add_argument("--best-tuning", action="store_true")
    parser.add_argument("--actuators", action="store_true", help="Enable actuator plant model")
    parser.add_argument("--stochastic", action="store_true", help="Enable stochastic combustion")
    parser.add_argument("--ring-plenum", action="store_true", help="Enable intake plenum derating")
    parser.add_argument("--cooling", default="water_jacket")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    cfg = PhoenixV3Config()
    if args.best_tuning:
        cfg = load_best_tuning_config(cfg)
    cfg = apply_generator_cooling(cfg, args.cooling)
    if args.actuators:
        from dataclasses import replace

        cfg = replace(cfg, actuator_model_enabled=True)
    if args.stochastic:
        from dataclasses import replace

        cfg = replace(cfg, stochastic_combustion_enabled=True, combustion_rng_seed=42)
    if args.ring_plenum:
        from dataclasses import replace

        cfg = replace(cfg, intake_plenum_enabled=True, intake_plenum_concurrent_intakes=3)

    result = PhoenixV3Simulator(cfg).simulate(cycles=args.cycles, record_history=False)
    report = compute_canonical_efficiency(result)
    print_canonical_efficiency_report(result)

    if args.json_out:
        payload = {
            "definition": CANONICAL_EFFICIENCY_DEFINITION,
            "cycles": args.cycles,
            "metrics": asdict(report),
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"Wrote {args.json_out}")


if __name__ == "__main__":
    main()
