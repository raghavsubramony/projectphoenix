"""Feature schema for the Unified-AI learning study.

This module defines *what the controller brain observes* and the **varying data
ranges** each signal spans. These are the raw inputs the rule-based
``UnifiedController`` uses today (demand, vehicle speed, battery state, rotor /
buffer state); capturing them with explicit ranges lets a future learned
"Unified AI" be mapped onto the exact same observation space.

Design notes
------------
* The features mirror the live decision inputs in
  :mod:`digital_twin.controller` so a learned policy is a drop-in study against
  the rule-based one.
* Every signal carries an explicit ``(low, high)`` operating range. The ranges
  are deliberately *generous* (they bound the physically reachable envelope, not
  just one cycle) so a streaming normaliser never has to extrapolate.
* All values are SI base units unless the name carries a unit suffix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class FeatureSpec:
    """One observed signal: its name, operating range, unit and meaning."""

    name: str
    low: float
    high: float
    unit: str
    description: str

    @property
    def span(self) -> float:
        return self.high - self.low


# The observation vector, in fixed column order. These are the *varying data
# ranges* the study is built around: speed and demand swing across wide
# physical envelopes, while the two state-of-charge signals are already
# normalised [0, 1]. Keeping them together documents the heterogeneity a
# learner must cope with.
FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec("speed_ms", 0.0, 60.0, "m/s",
                "Vehicle road speed (0 - ~216 km/h)."),
    FeatureSpec("demand_w", -60_000.0, 220_000.0, "W",
                "Net DC-bus power demand (negative = regenerative braking)."),
    FeatureSpec("battery_soc", 0.0, 1.0, "-",
                "Traction-battery state of charge."),
    FeatureSpec("buffer_soc", 0.0, 1.0, "-",
                "PCMRITMS inertial-buffer (rotor) state of charge."),
)

FEATURE_NAMES: tuple[str, ...] = tuple(s.name for s in FEATURE_SPECS)
N_FEATURES: int = len(FEATURE_SPECS)

# Ordered (low, high) ranges, handy for building scalers.
FEATURE_RANGES: tuple[tuple[float, float], ...] = tuple(
    (s.low, s.high) for s in FEATURE_SPECS
)


def observation(speed_ms: float, demand_w: float,
                battery_soc: float, buffer_soc: float) -> tuple[float, ...]:
    """Assemble a raw observation vector from live signals.

    This is the runtime entry point a future Unified-AI policy would call each
    control tick. Column order matches :data:`FEATURE_SPECS`.
    """
    return (speed_ms, demand_w, battery_soc, buffer_soc)


def observation_from_step(rec) -> tuple[float, ...]:
    """Extract the raw observation vector from a ``StepResult`` telemetry row.

    Accepts any object exposing ``speed_ms``, ``demand_w``, ``battery_soc`` and
    ``buffer_soc`` (i.e. :class:`digital_twin.powertrain.StepResult`). Kept
    duck-typed so this study package has no hard import of the twin.
    """
    return (rec.speed_ms, rec.demand_w, rec.battery_soc, rec.buffer_soc)


def describe_ranges() -> str:
    """Human-readable table of the varying data ranges (for study reports)."""
    lines = ["Observation feature ranges:"]
    width = max(len(s.name) for s in FEATURE_SPECS)
    for s in FEATURE_SPECS:
        lines.append(
            f"  {s.name:<{width}}  [{s.low:>10.1f}, {s.high:>10.1f}] "
            f"{s.unit:<4}  {s.description}"
        )
    return "\n".join(lines)


def validate_length(vector: Sequence[float]) -> None:
    """Raise ``ValueError`` if ``vector`` does not match the feature schema."""
    if len(vector) != N_FEATURES:
        raise ValueError(
            f"expected {N_FEATURES} features {FEATURE_NAMES}, got {len(vector)}"
        )
