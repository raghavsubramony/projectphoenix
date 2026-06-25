"""Component right-sizing study: the minimum battery discharge power per body.

Moves C and E established that transient capability in this architecture is
governed by *energy* and *power* limits, not by the inertial buffer's surge
ceiling. This study closes that thread with the design question the whole
project has been building toward: **how big do the components actually need to
be?**

Sweeping each capability lever in isolation shows a clear, single answer: the
dominant lever is **battery discharge power**. Buffer energy and generation
slew move the shortfall count only marginally, and the PCMRITMS rotor surge
(140 kW brief burst) makes *no* difference to the sizing at all - exactly as
Move E predicted. So the sizing study sweeps battery discharge power per body
and reports the smallest pack power (and its C-rate, for a 20 kWh pack) that
keeps the body capable.

The headline conclusion is a *down-sizing* one: the validated default pack power
(120 kW = 6C) is generously over-specced. Most bodies stay capable at ~3C
(60 kW); only the heavy AWD SUV needs ~4.5C. A lower-C-rate pack is cheaper and
more energy-dense, so right-sizing the power is a real cost lever - and the
small-pack economics story of Move D survives it intact.

Pure standard library. Reuses the fleet stress harness; changes nothing about
the validated steady-cycle numbers (this is a read-only design sweep).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .config import PHASE1_BODIES, BodyStyle, phase1_config_for, KW, KWH_TO_J
from .fleet import standard_cycles
from .powertrain import Powertrain
from .simulation import run

# The validated default battery discharge power (see config._phase1_powertrain).
DEFAULT_PACK_POWER_W = 120 * KW
# The pack energy the power rating is referred to, for the C-rate context.
DEFAULT_PACK_KWH = 20.0


@dataclass(frozen=True)
class SizingConfig:
    """Knobs for the battery-power right-sizing sweep."""

    # Realistic generator ramp limit so the buffer/battery actually matter (a
    # free-piston generator cannot step instantly to full power).
    slew_w_per_s: float = 60_000.0
    # A body is "capable" if its total shortfall steps across the standard
    # cycle set is at or below this. The residual 1-2 are single-step blips on
    # the harshest tow/grade launches, not sustained capability loss.
    capability_target: int = 2
    # Candidate battery discharge powers to try (ascending), in watts.
    power_levels_w: tuple[float, ...] = (
        50 * KW, 60 * KW, 70 * KW, 80 * KW, 90 * KW, 100 * KW, 110 * KW, 120 * KW)
    pack_kwh: float = DEFAULT_PACK_KWH
    rotor_coupled: bool = True


@dataclass(frozen=True)
class SizingPoint:
    """One body evaluated at one candidate battery discharge power."""

    battery_power_w: float
    c_rate: float
    shortfall_events: int
    max_shortfall_kw: float
    feasible: bool


@dataclass(frozen=True)
class BodySizing:
    """The right-sizing outcome for one body."""

    body: str
    feasible: bool                       # any swept power met the target?
    recommended_power_w: float           # min feasible (or highest swept)
    recommended_c_rate: float
    default_power_w: float
    downsize_w: float                    # default - recommended (>= 0)
    downsize_pct: float
    sweep: list[SizingPoint]


def _total_shortfalls(cfg) -> tuple[int, float]:
    """Total shortfall steps and worst shortfall across the standard cycles."""
    events = 0
    worst_kw = 0.0
    for cycle in standard_cycles():
        res = run(Powertrain(cfg), cycle)
        events += res.shortfall_events
        worst_kw = max(worst_kw, res.max_shortfall_kw)
    return events, worst_kw


def _build_cfg(body: BodyStyle, power_w: float, sizing: SizingConfig):
    cfg = phase1_config_for(body, rotor_coupled=sizing.rotor_coupled)
    return replace(
        cfg,
        atpe=replace(cfg.atpe, max_slew_w_per_s=sizing.slew_w_per_s),
        battery=replace(cfg.battery, max_discharge_w=power_w,
                        initial_soc=cfg.battery.soc_target),
    )


def size_battery_power(body: BodyStyle,
                       sizing: SizingConfig | None = None) -> BodySizing:
    """Sweep battery discharge power for one body; report the min capable size."""
    sizing = sizing or SizingConfig()
    pack_j = sizing.pack_kwh * KWH_TO_J
    points: list[SizingPoint] = []
    recommended_w: float | None = None
    for power_w in sizing.power_levels_w:
        cfg = _build_cfg(body, power_w, sizing)
        events, worst_kw = _total_shortfalls(cfg)
        feasible = events <= sizing.capability_target
        # C-rate = power / energy (per hour): W / (J/3600) = W * 3600 / J.
        c_rate = power_w * 3600.0 / pack_j if pack_j > 0 else 0.0
        points.append(SizingPoint(power_w, c_rate, events, worst_kw, feasible))
        if feasible and recommended_w is None:
            recommended_w = power_w

    any_feasible = recommended_w is not None
    if recommended_w is None:
        # Nothing met the target; recommend the strongest swept option.
        recommended_w = sizing.power_levels_w[-1]
    rec_c = recommended_w * 3600.0 / pack_j if pack_j > 0 else 0.0
    downsize_w = max(0.0, DEFAULT_PACK_POWER_W - recommended_w)
    downsize_pct = (downsize_w / DEFAULT_PACK_POWER_W * 100.0
                    if DEFAULT_PACK_POWER_W > 0 else 0.0)
    return BodySizing(
        body=body.name,
        feasible=any_feasible,
        recommended_power_w=recommended_w,
        recommended_c_rate=rec_c,
        default_power_w=DEFAULT_PACK_POWER_W,
        downsize_w=downsize_w,
        downsize_pct=downsize_pct,
        sweep=points,
    )


def fleet_battery_sizing(
        sizing: SizingConfig | None = None,
        bodies: tuple[BodyStyle, ...] = PHASE1_BODIES) -> list[BodySizing]:
    """Right-size the battery discharge power for every body."""
    sizing = sizing or SizingConfig()
    return [size_battery_power(b, sizing) for b in bodies]


def sizing_table(results: list[BodySizing],
                 sizing: SizingConfig | None = None) -> str:
    """Compact per-body right-sizing recommendation table."""
    sizing = sizing or SizingConfig()
    lines = [
        f"=== Battery power right-sizing (target <= {sizing.capability_target} "
        f"shortfall steps, {sizing.pack_kwh:.0f} kWh pack) ===",
        f"  {'Body':<12}{'Min kW':>8}{'C-rate':>8}{'vs 120kW':>10}"
        f"{'Capable':>9}",
        "  " + "-" * 47,
    ]
    for r in results:
        capable = "yes" if r.feasible else "NO"
        lines.append(
            f"  {r.body:<12}{r.recommended_power_w/KW:>8.0f}"
            f"{r.recommended_c_rate:>7.1f}C"
            f"{-r.downsize_pct:>9.0f}%{capable:>9}")
    lines.append("  (Min kW = smallest pack discharge power that stays capable;")
    lines.append("   vs 120kW = headroom freed by right-sizing off the default.)")
    return "\n".join(lines)
