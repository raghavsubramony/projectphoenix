"""Data-gathering bridge from the digital twin into a study :class:`Dataset`.

This is the *gathering* half of the library: it drives the existing physics twin
over drive cycles and records, for every timestep, the raw observation (in its
native varying ranges) paired with the controller's decision and generation
command. The result is a range-faithful corpus ready for incremental learning.

The import of :mod:`digital_twin` is deferred into the functions so the rest of
``ml_study`` (features, scaling, models, dataset) stays usable as a standalone
ML toolkit even without the twin present.
"""

from __future__ import annotations

from typing import Callable, Sequence

from .dataset import Dataset
from .features import observation_from_step
from .labels import decision_from_step

# A no-arg factory returning a fresh twin (matches digital_twin's TwinBuilder).
TwinBuilder = Callable[[], object]


def collect_dataset(
    twin_builder: TwinBuilder | None = None,
    cycles: Sequence[object] | None = None,
) -> Dataset:
    """Run ``twin_builder`` over ``cycles`` and gather a labelled dataset.

    Args:
        twin_builder: zero-arg callable returning a fresh ``Powertrain``.
            Defaults to the Phase-1 charge-sustaining SUV twin.
        cycles: drive cycles to evaluate. Defaults to the standard fleet set.

    Returns:
        A :class:`Dataset` of raw observations -> (tier decision, generation W).
    """
    from digital_twin import run, standard_cycles
    from digital_twin.config import phase1_config
    from digital_twin.powertrain import Powertrain
    from dataclasses import replace

    if twin_builder is None:
        def twin_builder() -> Powertrain:  # charge-sustaining Phase-1 SUV
            cfg = phase1_config()
            cfg = replace(cfg, battery=replace(cfg.battery,
                                               initial_soc=cfg.battery.soc_target))
            return Powertrain(cfg)

    cycle_list = list(cycles) if cycles is not None else standard_cycles()

    dataset = Dataset()
    for cycle in cycle_list:
        result = run(twin_builder(), cycle)
        for rec in result.records:
            dataset.add(
                observation_from_step(rec),
                decision_from_step(rec),
                rec.generation_w,
            )
    return dataset


def default_target_max_w() -> float:
    """Total ATPE electrical capacity - the regression target's natural scale."""
    from digital_twin.config import phase1_config
    return phase1_config().atpe.max_electric_w
