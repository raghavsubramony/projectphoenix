"""ERS acceptance checks: evaluate the twin against Project PHOENIX targets.

Implements the Engine Requirements Specification targets from
docs/09-atpe-ers-and-insights.md as objective, simulation-backed pass/fail
checks. Performance figures (0-100, top speed, gradeability) are derived from
the same physics models the rest of the twin uses, draining the real buffer and
battery state so the numbers stay honest.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable

from .config import GRAVITY, TractionConfig, VehicleConfig
from .powertrain import Powertrain

TwinBuilder = Callable[[], Powertrain]


@dataclass(frozen=True)
class ERSTargets:
    """Project PHOENIX requirement targets (see docs/09)."""

    name: str
    peak_power_w: float
    continuous_power_w: float
    peak_motor_torque_nm: float
    accel_0_100_s: float
    top_speed_kmh: float
    gradeability_pct: float
    gradeability_speed_kmh: float
    brake_thermal_efficiency: float
    generator_efficiency: float
    fuel_to_wheel_efficiency: float


def phase1_targets() -> ERSTargets:
    return ERSTargets(
        name="PHOENIX-P1 (MUV/SUV)",
        peak_power_w=150_000,
        continuous_power_w=100_000,
        peak_motor_torque_nm=350,
        accel_0_100_s=8.0,
        top_speed_kmh=180,
        gradeability_pct=20,
        gradeability_speed_kmh=100,
        brake_thermal_efficiency=0.45,
        generator_efficiency=0.95,
        fuel_to_wheel_efficiency=0.38,
    )


# --- per-body acceptance targets -------------------------------------------

# The Phase-1 powertrain is shared, so powertrain-level targets (power, torque,
# efficiency) are identical for every body. Only the chassis-dependent dynamics
# targets (0-100, top speed, gradeability) vary by vehicle class, set to
# representative market expectations for each body type.

_P1_POWERTRAIN_TARGETS: dict[str, float] = dict(
    peak_power_w=150_000,
    continuous_power_w=100_000,
    peak_motor_torque_nm=350,
    brake_thermal_efficiency=0.45,
    generator_efficiency=0.95,
    fuel_to_wheel_efficiency=0.38,
    gradeability_speed_kmh=100,
)

# (accel_0_100_s, top_speed_kmh, gradeability_pct) per body class.
_P1_BODY_DYNAMICS: dict[str, dict[str, float]] = {
    "AWD SUV":   dict(accel_0_100_s=8.0, top_speed_kmh=180, gradeability_pct=20),
    "Sedan":     dict(accel_0_100_s=7.5, top_speed_kmh=200, gradeability_pct=20),
    "Hatchback": dict(accel_0_100_s=9.5, top_speed_kmh=175, gradeability_pct=18),
    "Crossover": dict(accel_0_100_s=8.5, top_speed_kmh=185, gradeability_pct=20),
    "Pickup":    dict(accel_0_100_s=11.0, top_speed_kmh=160, gradeability_pct=18),
    "Van / MPV": dict(accel_0_100_s=10.0, top_speed_kmh=170, gradeability_pct=18),
}


def phase1_targets_for(body_name: str) -> ERSTargets:
    """Class-appropriate ERS targets for a specific Phase-1 vehicle body.

    Powertrain-level targets are shared; performance targets reflect realistic
    expectations for the vehicle class. Raises KeyError for unknown bodies.
    """
    dynamics = _P1_BODY_DYNAMICS[body_name]
    return ERSTargets(
        name=f"PHOENIX-P1 ({body_name})",
        **_P1_POWERTRAIN_TARGETS,
        **dynamics,
    )


def phase1_body_targets() -> dict[str, ERSTargets]:
    """Per-body ERS targets keyed by body name (AWD SUV first)."""
    return {name: phase1_targets_for(name) for name in _P1_BODY_DYNAMICS}


@dataclass(frozen=True)
class Check:
    name: str
    target: float
    actual: float
    unit: str
    passed: bool
    higher_is_better: bool = True

    def line(self) -> str:
        mark = "PASS" if self.passed else "FAIL"
        comp = ">=" if self.higher_is_better else "<="
        return (f"  [{mark}] {self.name:<28} target {comp} {self.target:>7.1f} "
                f"{self.unit:<5} actual {self.actual:>7.1f} {self.unit}")


# --- physics helpers --------------------------------------------------------

def _resistive_force_n(veh: VehicleConfig, speed_ms: float,
                       grade_rad: float = 0.0) -> float:
    """Road-load resistive force (roll + aero + grade), excluding inertia."""
    from .config import AIR_DENSITY
    f_grade = veh.mass_kg * GRAVITY * math.sin(grade_rad)
    f_roll = veh.rolling_resistance * veh.mass_kg * GRAVITY * math.cos(grade_rad)
    f_aero = (0.5 * AIR_DENSITY * veh.drag_coefficient * veh.frontal_area_m2
              * speed_ms * speed_ms)
    return f_grade + f_roll + f_aero


def _deliver_w(twin: Powertrain, request_w: float, dt_s: float) -> float:
    """Electrical power the sources can actually deliver this step (gen+buffer+batt)."""
    gen = twin.atpe.generate(min(request_w, twin.atpe.max_electric_w), dt_s).electric_w
    remaining = request_w - gen
    buf = twin.buffer.exchange(remaining, dt_s) if remaining > 0 else 0.0
    remaining -= buf
    bat = twin.battery.exchange(remaining, dt_s) if remaining > 0 else 0.0
    return gen + buf + bat


# --- capability simulations -------------------------------------------------

def accel_0_to_kmh(twin: Powertrain, target_kmh: float = 100.0,
                   dt_s: float = 0.02, t_max_s: float = 60.0) -> float:
    """Full-throttle 0 -> target time (s). Returns inf if target not reached."""
    veh = twin.cfg.vehicle
    tr = twin.cfg.traction
    f_torque_limit = tr.peak_torque_nm * tr.gear_ratio / tr.wheel_radius_m
    v = 0.0
    t = 0.0
    v_target = target_kmh / 3.6
    while v < v_target and t < t_max_s:
        delivered_w = _deliver_w(twin, tr.peak_power_w, dt_s)
        wheel_power_w = delivered_w * veh.driveline_efficiency
        f_power = wheel_power_w / v if v > 0.1 else f_torque_limit
        f_traction = min(f_torque_limit, f_power)
        accel = (f_traction - _resistive_force_n(veh, v)) / veh.mass_kg
        if accel <= 0.0:
            return math.inf
        v += accel * dt_s
        t += dt_s
    return t if v >= v_target else math.inf


def top_speed_kmh(twin: Powertrain) -> float:
    """Highest speed where sustained power balances road load (km/h)."""
    veh = twin.cfg.vehicle
    tr = twin.cfg.traction
    avail_w = min(tr.continuous_power_w, twin.atpe.max_electric_w) * veh.driveline_efficiency
    v = 1.0
    while v < 150.0:  # 540 km/h ceiling guard
        if _resistive_force_n(veh, v) * v > avail_w:
            break
        v += 0.5
    return v * 3.6


def max_grade_pct(twin: Powertrain, speed_kmh: float) -> float:
    """Maximum sustainable grade (%) at `speed_kmh` using peak power."""
    veh = twin.cfg.vehicle
    tr = twin.cfg.traction
    v = speed_kmh / 3.6
    avail_w = min(tr.peak_power_w, twin.atpe.max_electric_w) * veh.driveline_efficiency
    grade = 0.0
    while grade < 0.6:  # 60% ceiling guard
        if _resistive_force_n(veh, v, math.atan(grade)) * v > avail_w:
            break
        grade += 0.005
    return grade * 100.0


# --- evaluation -------------------------------------------------------------

def evaluate(build_twin: TwinBuilder, targets: ERSTargets) -> list[Check]:
    """Run every ERS check on fresh twin instances and return the results."""
    sample = build_twin()
    veh = sample.cfg.vehicle
    tr = sample.cfg.traction
    atpe = sample.cfg.atpe

    # Deliverable peak power = burst sum of all sources (one fresh step).
    peak_w = _deliver_w(build_twin(), 1e9, dt_s=0.1)
    # Sustained power = engine continuous generation capability.
    continuous_w = atpe.max_electric_w

    bte = atpe.best_brake_thermal_efficiency()
    best_combined = max(t.thermal_efficiency for t in atpe.tiers)
    fuel_to_wheel = best_combined * veh.driveline_efficiency

    accel_s = accel_0_to_kmh(build_twin(), 100.0)
    v_top = top_speed_kmh(build_twin())
    grade = max_grade_pct(build_twin(), targets.gradeability_speed_kmh)

    return [
        Check("Peak power", targets.peak_power_w / 1000,
              peak_w / 1000, "kW", peak_w >= targets.peak_power_w),
        Check("Continuous power", targets.continuous_power_w / 1000,
              continuous_w / 1000, "kW", continuous_w >= targets.continuous_power_w),
        Check("Peak motor torque", targets.peak_motor_torque_nm,
              tr.peak_torque_nm, "Nm", tr.peak_torque_nm >= targets.peak_motor_torque_nm),
        Check("0-100 km/h", targets.accel_0_100_s,
              accel_s, "s", accel_s <= targets.accel_0_100_s, higher_is_better=False),
        Check("Top speed", targets.top_speed_kmh,
              v_top, "km/h", v_top >= targets.top_speed_kmh),
        Check(f"Gradeability @ {targets.gradeability_speed_kmh:.0f}km/h",
              targets.gradeability_pct, grade, "%", grade >= targets.gradeability_pct),
        Check("Brake thermal eff (best)", targets.brake_thermal_efficiency * 100,
              bte * 100, "%", bte >= targets.brake_thermal_efficiency),
        Check("Generator efficiency", targets.generator_efficiency * 100,
              atpe.generator_efficiency * 100, "%",
              atpe.generator_efficiency >= targets.generator_efficiency),
        Check("Fuel-to-wheel eff (best)", targets.fuel_to_wheel_efficiency * 100,
              fuel_to_wheel * 100, "%", fuel_to_wheel >= targets.fuel_to_wheel_efficiency),
    ]


def report(build_twin: TwinBuilder, targets: ERSTargets) -> str:
    checks = evaluate(build_twin, targets)
    passed = sum(1 for c in checks if c.passed)
    header = f"=== ERS Acceptance: {targets.name} ({passed}/{len(checks)} passed) ==="
    body = "\n".join(c.line() for c in checks)
    return f"{header}\n{body}"


def compare_bodies(build_twins: dict[str, TwinBuilder],
                   targets: ERSTargets | None = None) -> str:
    """Side-by-side performance figures for several vehicle bodies.

    Each entry shares the same powertrain; only the chassis differs. Figures are
    computed from the same capability simulations as the acceptance checks. If
    `targets` is given, the 0-100 column is marked against the accel target.
    """
    accel_target = targets.accel_0_100_s if targets else None
    rows: list[tuple[str, float, float, float, float, float, bool]] = []
    for name, build in build_twins.items():
        twin = build()
        mass = twin.cfg.vehicle.mass_kg
        peak_kw = _deliver_w(build(), 1e9, dt_s=0.1) / 1000
        accel = accel_0_to_kmh(build(), 100.0)
        v_top = top_speed_kmh(build())
        grade = max_grade_pct(build(), 100.0)
        meets = accel_target is not None and accel <= accel_target
        rows.append((name, mass, peak_kw, accel, v_top, grade, meets))

    head = (f"  {'Body':<12} {'Mass':>6} {'Peak':>7} {'0-100':>7} "
            f"{'Top':>7} {'Grade':>6}")
    units = (f"  {'':<12} {'kg':>6} {'kW':>7} {'s':>7} "
             f"{'km/h':>7} {'%':>6}")
    sep = "  " + "-" * 48
    lines = [head, units, sep]
    for name, mass, peak, accel, v_top, grade, meets in rows:
        flag = "" if accel_target is None else ("  <=tgt" if meets else "  >tgt")
        lines.append(f"  {name:<12} {mass:>6.0f} {peak:>7.0f} {accel:>7.2f} "
                     f"{v_top:>7.1f} {grade:>6.1f}{flag}")
    title = "=== Vehicle-body comparison (same Phase-1 powertrain) ==="
    note = ("  (same ATPE + buffer + battery; 150 kW motor, 160 kW on the heavy "
            "SUV/pickup)")
    return title + "\n" + note + "\n" + "\n".join(lines)


def report_bodies(build_twins: dict[str, TwinBuilder],
                  targets_by_body: dict[str, ERSTargets]) -> str:
    """Per-body ERS verdict: evaluate each body against its own class targets.

    `build_twins` and `targets_by_body` must share keys (the body names). Each
    body is judged against the targets appropriate for its vehicle class, then a
    summary roll-up lists the pass count per body.
    """
    blocks: list[str] = []
    summary: list[tuple[str, int, int]] = []
    for name, build in build_twins.items():
        targets = targets_by_body[name]
        checks = evaluate(build, targets)
        passed = sum(1 for c in checks if c.passed)
        blocks.append(report(build, targets))
        summary.append((name, passed, len(checks)))

    roll = ["=== Per-body ERS summary ==="]
    for name, passed, total in summary:
        mark = "OK " if passed == total else "  -"
        roll.append(f"  [{mark}] {name:<12} {passed}/{total} checks passed")
    return "\n\n".join(blocks) + "\n\n" + "\n".join(roll)


# --- recommended motor sizing ----------------------------------------------

def _with_motor(build: TwinBuilder, peak_w: float,
                continuous_w: float) -> TwinBuilder:
    """Return a builder identical to `build` but with a resized traction motor."""
    base_cfg = build().cfg
    new_traction = replace(base_cfg.traction, peak_power_w=peak_w,
                           continuous_power_w=continuous_w)
    new_cfg = replace(base_cfg, traction=new_traction)
    return lambda: Powertrain(new_cfg)


def _min_peak_for_accel_grade(build: TwinBuilder, targets: ERSTargets,
                              ceiling_w: float) -> float:
    """Smallest motor peak power that clears both 0-100 and gradeability.

    Both improve monotonically with peak power, so bisect to ~1 kW. Returns inf
    if even the source-limited ceiling cannot satisfy the targets.
    """
    def ok(peak_w: float) -> bool:
        b = _with_motor(build, peak_w, peak_w)
        accel = accel_0_to_kmh(b(), 100.0)
        grade = max_grade_pct(b(), targets.gradeability_speed_kmh)
        return accel <= targets.accel_0_100_s and grade >= targets.gradeability_pct

    if not ok(ceiling_w):
        return math.inf
    lo, hi = 40_000.0, ceiling_w
    while hi - lo > 1_000.0:
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _min_cont_for_top(build: TwinBuilder, targets: ERSTargets,
                      engine_cap_w: float) -> float:
    """Smallest continuous power that clears top speed (capped by the engine).

    Top speed uses min(continuous_power, engine output), so beyond the engine
    cap more motor rating does nothing. Returns inf if top speed is
    engine-limited (cannot be fixed by motor sizing alone).
    """
    def ok(cont_w: float) -> bool:
        b = _with_motor(build, max(cont_w, engine_cap_w), cont_w)
        return top_speed_kmh(b()) >= targets.top_speed_kmh

    if not ok(engine_cap_w):
        return math.inf
    lo, hi = 30_000.0, engine_cap_w
    while hi - lo > 1_000.0:
        mid = 0.5 * (lo + hi)
        if ok(mid):
            hi = mid
        else:
            lo = mid
    return hi


def _round_up_kw(power_w: float, step_kw: float = 5.0) -> float:
    """Round a power (W) up to the next `step_kw` increment, in kW."""
    kw = power_w / 1000.0
    return math.ceil(kw / step_kw) * step_kw


@dataclass(frozen=True)
class MotorRecommendation:
    body: str
    current_peak_kw: float
    recommended_peak_kw: float       # to clear 0-100 + gradeability
    recommended_continuous_kw: float  # to clear top speed
    top_speed_engine_limited: bool   # True if top speed cannot be met by motor alone
    drivers: tuple[str, ...]         # checks the current motor fails

    def line(self) -> str:
        peak = (f"{self.recommended_peak_kw:>5.0f} kW"
                if math.isfinite(self.recommended_peak_kw) else "  n/a ")
        if self.top_speed_engine_limited:
            cont = "engine-lim"
        else:
            cont = f"{self.recommended_continuous_kw:>5.0f} kW"
        delta = self.recommended_peak_kw - self.current_peak_kw
        note = ("meets all on current motor" if not self.drivers
                else "fails: " + ", ".join(self.drivers))
        margin = (f"  ({delta:+.0f} kW vs current)"
                  if math.isfinite(self.recommended_peak_kw) else "")
        return (f"  {self.body:<12} peak {self.current_peak_kw:>5.0f} -> "
                f"{peak}{margin:<22}  cont {cont:>10}  | {note}")


def recommend_motor(build: TwinBuilder, targets: ERSTargets) -> MotorRecommendation:
    """Auto-size the traction motor to clear a body's ERS targets.

    Recommends the minimum peak power (for 0-100 + gradeability) and continuous
    power (for top speed). Powertrain-level checks are unaffected by motor size.
    """
    sample = build()
    current_peak_w = sample.cfg.traction.peak_power_w
    # Source burst ceiling: most the gen+buffer+battery can feed the motor.
    ceiling_w = _deliver_w(build(), 1e9, dt_s=0.1)
    engine_cap_w = sample.cfg.atpe.max_electric_w

    rec_peak_w = _min_peak_for_accel_grade(build, targets, ceiling_w)
    rec_cont_w = _min_cont_for_top(build, targets, engine_cap_w)

    # Which of the current motor's checks fail (drives the recommendation).
    drivers: list[str] = []
    accel = accel_0_to_kmh(build(), 100.0)
    grade = max_grade_pct(build(), targets.gradeability_speed_kmh)
    v_top = top_speed_kmh(build())
    if accel > targets.accel_0_100_s:
        drivers.append("0-100")
    if grade < targets.gradeability_pct:
        drivers.append("gradeability")
    if v_top < targets.top_speed_kmh:
        drivers.append("top speed")

    return MotorRecommendation(
        body=targets.name,
        current_peak_kw=current_peak_w / 1000.0,
        recommended_peak_kw=(_round_up_kw(rec_peak_w)
                             if math.isfinite(rec_peak_w) else math.inf),
        recommended_continuous_kw=(_round_up_kw(rec_cont_w)
                                   if math.isfinite(rec_cont_w) else math.inf),
        top_speed_engine_limited=not math.isfinite(rec_cont_w),
        drivers=tuple(drivers),
    )


def recommend_motors(build_twins: dict[str, TwinBuilder],
                     targets_by_body: dict[str, ERSTargets]) -> str:
    """Per-body recommended motor sizing to clear each body's ERS targets."""
    title = "=== Recommended motor sizing (to clear each body's targets) ==="
    note = ("  peak sizes 0-100 + gradeability; cont sizes top speed "
            "(capped by engine output)")
    lines = [title, note]
    for name, build in build_twins.items():
        rec = recommend_motor(build, targets_by_body[name])
        # Show the short body name rather than the full target name.
        rec = replace(rec, body=name)
        lines.append(rec.line())
    return "\n".join(lines)


# --- motor sweep study ------------------------------------------------------

@dataclass(frozen=True)
class SweepPoint:
    """One body evaluated at one motor size."""

    motor_kw: float
    accel_0_100_s: float
    top_speed_kmh: float
    gradeability_pct: float
    passes: bool  # clears all three chassis-dependent class targets


def sweep_motor(build: TwinBuilder, targets: ERSTargets,
                lo_kw: float = 60.0, hi_kw: float = 250.0,
                step_kw: float = 10.0) -> list[SweepPoint]:
    """Evaluate one body across a range of motor sizes.

    Peak and continuous power are both set to the swept value (a single-rating
    motor), so 0-100, gradeability *and* top speed all respond. Top speed is
    still capped by the engine's continuous output, so it plateaus once the
    motor exceeds the generator capacity.
    """
    points: list[SweepPoint] = []
    kw = lo_kw
    while kw <= hi_kw + 1e-9:
        power_w = kw * 1000.0
        b = _with_motor(build, power_w, power_w)
        accel = accel_0_to_kmh(b(), 100.0)
        v_top = top_speed_kmh(b())
        grade = max_grade_pct(b(), targets.gradeability_speed_kmh)
        passes = (accel <= targets.accel_0_100_s
                  and v_top >= targets.top_speed_kmh
                  and grade >= targets.gradeability_pct)
        points.append(SweepPoint(kw, accel, v_top, grade, passes))
        kw += step_kw
    return points


def _min_passing_kw(points: list[SweepPoint]) -> float:
    for p in points:
        if p.passes:
            return p.motor_kw
    return math.inf


def sweep_grid(build_twins: dict[str, TwinBuilder],
               targets_by_body: dict[str, ERSTargets],
               lo_kw: float = 60.0, hi_kw: float = 250.0,
               step_kw: float = 10.0) -> str:
    """Pass/fail grid of every body across the motor-size range.

    Rows are motor sizes (lo..hi), columns are bodies; a cell is the first
    letter-coded verdict (`.`=pass, `x`=fail). A trailing summary lists the
    minimum passing motor per body. This is the comparative study: read down a
    column to see where each body crosses from fail to pass.
    """
    names = list(build_twins.keys())
    sweeps = {n: sweep_motor(build_twins[n], targets_by_body[n], lo_kw, hi_kw, step_kw)
              for n in names}

    # Column widths sized to the body names.
    widths = {n: max(len(n), 4) for n in names}
    header = "  kW   " + "  ".join(f"{n:>{widths[n]}}" for n in names)
    sep = "  " + "-" * (len(header) - 2)
    lines = [
        "=== Motor-size sweep study (. = passes class targets, x = fails) ===",
        f"  range {lo_kw:.0f}-{hi_kw:.0f} kW, {step_kw:.0f} kW steps; "
        "each body vs its own class targets",
        header,
        sep,
    ]
    n_rows = len(next(iter(sweeps.values())))
    for i in range(n_rows):
        kw = sweeps[names[0]][i].motor_kw
        cells = []
        for n in names:
            mark = "." if sweeps[n][i].passes else "x"
            cells.append(f"{mark:>{widths[n]}}")
        lines.append(f"  {kw:>3.0f}   " + "  ".join(cells))

    lines.append("")
    lines.append("  Minimum passing motor (and what each body asks for):")
    for n in names:
        mn = _min_passing_kw(sweeps[n])
        if math.isfinite(mn):
            # The binding target at the minimum passing size.
            pt = next(p for p in sweeps[n] if p.motor_kw == mn)
            tgt = targets_by_body[n]
            binder = _binding_metric(pt, tgt)
            lines.append(f"    {n:<12} {mn:>5.0f} kW   (binding: {binder})")
        else:
            lines.append(f"    {n:<12}   none in range")
    return "\n".join(lines)


def _binding_metric(pt: SweepPoint, tgt: ERSTargets) -> str:
    """Name the target that is tightest (closest to its limit) at this size."""
    # Normalised slack: how far past the target each metric sits (smaller=tighter).
    accel_slack = (tgt.accel_0_100_s - pt.accel_0_100_s) / tgt.accel_0_100_s
    top_slack = (pt.top_speed_kmh - tgt.top_speed_kmh) / tgt.top_speed_kmh
    grade_slack = (pt.gradeability_pct - tgt.gradeability_pct) / tgt.gradeability_pct
    ranked = sorted(
        [("0-100", accel_slack), ("top speed", top_slack),
         ("gradeability", grade_slack)],
        key=lambda kv: kv[1],
    )
    return ranked[0][0]


def sweep_body_detail(build: TwinBuilder, targets: ERSTargets,
                      lo_kw: float = 60.0, hi_kw: float = 250.0,
                      step_kw: float = 10.0) -> str:
    """Full numeric sweep for a single body (0-100 / top / grade at each size)."""
    pts = sweep_motor(build, targets, lo_kw, hi_kw, step_kw)
    title = f"=== Motor sweep: {targets.name} ==="
    sub = (f"  targets: 0-100 <= {targets.accel_0_100_s:.1f}s, "
           f"top >= {targets.top_speed_kmh:.0f} km/h, "
           f"grade >= {targets.gradeability_pct:.0f}%")
    head = f"  {'kW':>4} {'0-100 s':>9} {'top km/h':>9} {'grade %':>8}  verdict"
    sep = "  " + "-" * (len(head) - 2)
    lines = [title, sub, head, sep]
    for p in pts:
        verdict = "PASS" if p.passes else "fail"
        lines.append(f"  {p.motor_kw:>4.0f} {p.accel_0_100_s:>9.2f} "
                     f"{p.top_speed_kmh:>9.1f} {p.gradeability_pct:>8.1f}  {verdict}")
    return "\n".join(lines)
