"""Decision labels: the targets a future Unified AI must learn to produce.

The rule-based controller's observable *decision* each tick is which generation
tier it engaged. That is exactly the discrete choice a learned policy would
replace, so it is the classification target of this study:

    class 0 -> EV       (engine off, battery + buffer only)
    class 1 -> Tier 1   (micro cylinders)
    class 2 -> Tier 2   (medium cylinders)
    class 3 -> Tier 3   (large cylinders)

Mapping: ``StepResult.active_index`` is ``-1`` when the engine is off and
``0..2`` for an engaged tier, so ``class = active_index + 1``.

The continuous companion target is the realised generation power
(``generation_w``), which a regressor learns alongside the tier decision.

Incremental escalation rule
---------------------------
Per the project's "increment by 1, not by 10" learning policy, the tier decision
escalates or de-escalates **one step at a time**: EV -> T1 -> T2 -> T3 (and
back), never skipping a tier within a single control update.
:func:`clamp_increment` enforces that on any candidate decision.
"""

from __future__ import annotations

# Ordered decision classes; index == class id.
DECISION_LABELS: tuple[str, ...] = ("EV", "Tier 1", "Tier 2", "Tier 3")
N_DECISIONS: int = len(DECISION_LABELS)

# The smallest unit of change the policy is allowed to make per control tick.
LEARNING_INCREMENT: int = 1


def decision_from_step(rec) -> int:
    """Class id for a telemetry row: ``active_index + 1`` clamped to range.

    Accepts any object exposing ``active_index`` (i.e.
    :class:`digital_twin.powertrain.StepResult`).
    """
    cls = rec.active_index + 1
    if cls < 0:
        return 0
    if cls >= N_DECISIONS:
        return N_DECISIONS - 1
    return cls


def label_name(cls: int) -> str:
    """Human-readable name for a decision class id."""
    if cls < 0 or cls >= N_DECISIONS:
        raise ValueError(f"unknown decision class {cls}")
    return DECISION_LABELS[cls]


def clamp_increment(previous: int, target: int,
                    step: int = LEARNING_INCREMENT) -> int:
    """Limit a decision change to ``+/- step`` classes per tick.

    Embodies the "increment by 1, not by 10" rule: a policy may move toward the
    target decision but only one tier at a time, preventing abrupt EV -> Tier 3
    jumps that the hardware (and the buffer energy budget) cannot honour.
    """
    if target > previous + step:
        return previous + step
    if target < previous - step:
        return previous - step
    return target
