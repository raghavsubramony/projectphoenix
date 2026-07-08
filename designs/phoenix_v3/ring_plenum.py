"""Minimum viable shared intake plenum for multi-cartridge ring coupling."""

from __future__ import annotations

from dataclasses import dataclass

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.constants import clamp
from designs.phoenix_v3.controls import intake_open_at_local_ms


def plenum_intake_pressure_bar(
    nominal_bar: float,
    concurrent_intakes: int,
    plenum_volume_m3: float,
    loss_per_open_port: float,
) -> float:
    """Reduce manifold pressure when multiple cartridges draw simultaneously."""
    if concurrent_intakes <= 1:
        return nominal_bar
    n = concurrent_intakes
    drop = loss_per_open_port * (n - 1)
    volume_penalty = 0.0015 * (n - 1) / max(plenum_volume_m3 * 1e3, 1e-9)
    return nominal_bar * clamp(1.0 - drop - volume_penalty, 0.55, 1.0)


def mean_concurrent_intakes_during_scavenge(
    cartridge_count: int,
    cycle_ms: float,
    cfg: PhoenixV3Config,
    *,
    samples_per_cartridge: int = 24,
) -> float:
    """Average number of cartridges with intake open during any cartridge scavenge."""
    if cartridge_count <= 1:
        return 1.0
    spacing_ms = cycle_ms / cartridge_count
    offsets = [i * spacing_ms for i in range(cartridge_count)]
    total = 0.0
    count = 0
    scavenge_ms = 1.5 * (cycle_ms / 10.0)
    push_start = (cfg.exhaust_open_ms + cfg.staged_exhaust_lead_ms) * (cycle_ms / 10.0)
    push_end = 10.0 * (cycle_ms / 10.0)

    for off_i in offsets:
        for phase in ("head", "push"):
            if phase == "head":
                t_range = scavenge_ms
                t_start = 0.0
            else:
                t_range = max(push_end - push_start, 1e-9)
                t_start = push_start
            for s in range(samples_per_cartridge):
                t_local = t_start + s * t_range / max(samples_per_cartridge - 1, 1)
                t_global = (off_i + t_local) % cycle_ms
                open_count = 0
                for off_j in offsets:
                    local_j = (t_global - off_j) % cycle_ms
                    if intake_open_at_local_ms(local_j, cfg):
                        open_count += 1
                total += open_count
                count += 1
    return total / max(count, 1)


@dataclass(frozen=True)
class RingPlenumAssignment:
    cartridge_index: int
    concurrent_intakes: int
    effective_intake_bar: float


def ring_plenum_assignments(
    base_cfg: PhoenixV3Config,
    cartridge_count: int,
) -> tuple[RingPlenumAssignment, ...]:
    cycle_ms = base_cfg.cycle_time_s * 1e3
    mean_concurrent = mean_concurrent_intakes_during_scavenge(
        cartridge_count, cycle_ms, base_cfg,
    )
    concurrent = max(1, int(round(mean_concurrent)))
    eff_bar = plenum_intake_pressure_bar(
        base_cfg.intake_ring_pressure_bar,
        concurrent,
        base_cfg.intake_plenum_volume_m3,
        base_cfg.intake_plenum_loss_per_open_port,
    )
    return tuple(
        RingPlenumAssignment(
            cartridge_index=i,
            concurrent_intakes=concurrent,
            effective_intake_bar=eff_bar,
        )
        for i in range(cartridge_count)
    )
