"""One-shot: validate v2 tuning on current physics and write v3 JSON."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from designs.phoenix_v3_optimizer import BEST_TUNING_V3, TuningVector, validate_best_for_export


def main() -> None:
    v2 = _REPO / "designs" / "phoenix_v3_best_tuning_v2.json"
    payload = json.loads(v2.read_text(encoding="utf-8"))
    vec = TuningVector.from_dict(payload["parameters"])
    trial, extras = validate_best_for_export(vec, single_cycles=24, ring_cycles=60)
    out = {
        "score": trial.score,
        "stable": trial.stable,
        "metrics": {
            "net_efficiency": trial.net_efficiency,
            "capture_fraction": trial.capture_fraction,
            "max_bdc_mm": trial.max_bdc_mm,
            "elec_energy_j": trial.elec_energy_j,
            "peak_pressure_bar": trial.peak_pressure_bar,
            "spring_recovery": trial.spring_recovery,
            "ke_power_fraction": trial.ke_power_fraction,
            "unharvested_fraction": trial.unharvested_fraction,
        },
        "parameters": vec.to_dict(),
        "search_version": "v3",
        "physics_note": "Validated on current V3 physics (late-window + ring 60cy)",
        "ring_validation": extras,
        "seeded_from": v2.name,
    }
    BEST_TUNING_V3.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {BEST_TUNING_V3}")
    print(f"  net_eff={trial.net_efficiency:.1%}")
    print(f"  ring={extras['ring_efficiency']:.1%}")
    print(f"  physics_validated={extras['physics_validated']}")


if __name__ == "__main__":
    main()
