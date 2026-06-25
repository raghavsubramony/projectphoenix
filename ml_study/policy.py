"""LearnedController: drive the twin with the learned Unified-AI policy.

This is where the study stops being an offline imitation experiment and becomes
an actual closed-loop controller. It wraps a fitted :class:`~ml_study.study.
LearningStudy` and exposes the exact ``decide(...) -> ControlState`` interface the
:class:`digital_twin.powertrain.Powertrain` expects, so it is a drop-in
replacement for the rule-based :class:`~digital_twin.controller.UnifiedController`.

Each control tick it:

1. assembles the same observation the study was trained on
   ``(speed_ms, demand_w, battery_soc, buffer_soc)``;
2. asks the classifier for the tier decision, clamped to a single-tier move per
   tick (the project's "increment by 1, not by 10" rule);
3. asks the regressor for the generation setpoint;
4. returns a ``ControlState`` the powertrain can act on.

The twin import is deferred so the rest of ``ml_study`` stays twin-free.
"""

from __future__ import annotations

from .features import observation
from .labels import clamp_increment
from .study import LearningStudy


class LearnedController:
    """Adapter exposing a fitted study as a powertrain control policy."""

    def __init__(self, study: LearningStudy, increment_clamp: bool = True,
                 spike_threshold_w: float | None = None) -> None:
        self.study = study
        self.increment_clamp = increment_clamp
        # Optional safety reflex: force the engine on above this instantaneous
        # demand even if the learned policy would stay in EV. Mirrors the
        # rule-based controller's spike override; None disables it so the
        # learned policy is judged on its own.
        self.spike_threshold_w = spike_threshold_w
        self._prev_decision = 0  # start in EV

    def decide(self, demand_w: float, battery_soc: float,
               max_generation_w: float, dt_s: float,
               speed_ms: float = 0.0, buffer_soc: float = 1.0):
        # Deferred import keeps ml_study importable without the twin present.
        from digital_twin.controller import ControlState

        obs = observation(speed_ms, demand_w, battery_soc, buffer_soc)
        target = self.study.predict_decision_id(obs)
        if self.increment_clamp:
            decision = clamp_increment(self._prev_decision, target)
        else:
            decision = target

        # Optional spike reflex overrides a too-timid EV choice.
        if (self.spike_threshold_w is not None
                and demand_w > self.spike_threshold_w and decision == 0):
            decision = min(self._prev_decision + 1, 3)

        self._prev_decision = decision

        if decision == 0:
            return ControlState("EV", 0.0, demand_w)

        # Engine on: take the regressor's setpoint, floored to cover the spike
        # so the engine assists the buffers instead of trailing the estimate.
        _, gen_w = self.study.predict_decision(obs)
        if self.spike_threshold_w is not None and demand_w > self.spike_threshold_w:
            gen_w = max(gen_w, min(demand_w, max_generation_w))
        gen_w = max(0.0, min(gen_w, max_generation_w))
        return ControlState("CS", gen_w, demand_w)

    def reset(self) -> None:
        """Reset the per-episode escalation memory (call between cycles)."""
        self._prev_decision = 0


def learned_bodies(study: LearningStudy, rotor_coupled: bool = False,
                   spike_reflex: bool = True):
    """Per-body charge-sustaining builders driven by the learned policy.

    Mirrors :func:`digital_twin.fleet.charge_sustaining_bodies` (same bodies,
    same starting SoC) but installs a fresh :class:`LearnedController` on each
    twin, so a fleet A/B isolates *only* the control policy. The optional spike
    reflex mirrors the rule-based engine-on override for a fair comparison.
    """
    from digital_twin import phase1_variants
    from digital_twin.powertrain import Powertrain
    from dataclasses import replace

    builders = {}
    for name, cfg in phase1_variants(rotor_coupled=rotor_coupled).items():
        cs_cfg = replace(cfg, battery=replace(cfg.battery,
                                              initial_soc=cfg.battery.soc_target))
        threshold = cs_cfg.control.spike_threshold_w if spike_reflex else None

        def _build(c=cs_cfg, thr=threshold):
            ctrl = LearnedController(study, spike_threshold_w=thr)
            return Powertrain(c, controller=ctrl)

        builders[name] = _build
    return builders

