"""Stdlib HTTP server exposing the digital twin to the browser dashboard.

Endpoints
---------
GET /                      -> the dashboard single-page app
GET /static/<file>         -> CSS / JS assets
GET /api/options           -> available drive cycles + vehicle bodies
GET /api/simulate?...      -> run one simulation, return full per-step telemetry
GET /api/tests             -> run the unittest suite, return per-test pass/fail

Everything is pure standard library (http.server, json, unittest) so the
dashboard inherits the project's zero-runtime-dependency rule.
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import unittest
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from digital_twin import (
    DriveCycles,
    Powertrain,
    build_body_twins,
    build_default_twin,
    build_performance_twin,
    run,
    with_gate1,
)
from digital_twin.single_cylinder import gate1_cycle_trace, simulate_free_piston, tier_physics_profile

_STATIC = Path(__file__).resolve().parent / "static"
_REPO_ROOT = Path(__file__).resolve().parent.parent


# --- domain wiring ----------------------------------------------------------

def _cycle_factories() -> dict:
    """Display name -> (callable returning a DriveCycle, short description)."""
    return {
        "Urban stop-go": (DriveCycles.urban,
                           "City stop-and-go: 0->45 km/h surges with stops"),
        "Highway cruise": (DriveCycles.highway,
                           "Sustained 100-120 km/h cruise with overtakes"),
        "Towing + grade": (DriveCycles.towing_grade,
                           "Highway towing up a sustained 6% climb"),
        "Mixed": (DriveCycles.mixed,
                  "Urban + highway + a short aggressive sprint"),
        "Transient stress": (lambda: DriveCycles.transient_stress(dt_s=0.2),
                             "Repeated hard launches (exercises the buffer)"),
    }


def _body_builders(coupled: bool) -> dict:
    """Display name -> zero-arg builder returning a fresh Powertrain."""
    builders: dict = {
        "Default (Phase-1 SUV)": lambda: build_default_twin(rotor_coupled=coupled),
    }
    builders.update(build_body_twins(rotor_coupled=coupled))
    builders["Performance (Phase-2)"] = build_performance_twin
    return builders


def _gate_roadmap() -> list[dict]:
    """UI roadmap skeleton so future gate visuals can plug in consistently."""
    return [
        {
            "id": "gate1",
            "name": "Gate 1 - Single-Cylinder Computer Model",
            "status": "ready",
            "description": "1D combustion, free-piston dynamics, and P-V view.",
        },
        {
            "id": "gate2",
            "name": "Gate 2 - Multi-Cylinder Coupling",
            "status": "planned",
            "description": "Cylinder interaction, phasing, and aggregate torque ripple.",
        },
        {
            "id": "gate3",
            "name": "Gate 3 - Controls Co-Design",
            "status": "planned",
            "description": "Controller surfaces, stability margins, and policy overlays.",
        },
        {
            "id": "gate4+",
            "name": "Gate 4+ - Program Expansion",
            "status": "planned",
            "description": "Future validation and hardware-in-the-loop visual layers.",
        },
    ]


def _governing_tier_for_demand(demand_w: float, atpe_cfg) -> int:
    """1-based tier index that would govern at this bus demand (0 if none)."""
    if demand_w <= 0.0:
        return 0
    cumulative = 0.0
    for i, tier in enumerate(atpe_cfg.tiers):
        cumulative += tier.max_electric_w
        if demand_w <= cumulative + 1e-6:
            return i + 1
    return len(atpe_cfg.tiers)


def _step_tier_label(tier: int, mode: str) -> str:
    if tier > 0:
        return f"Tier {tier}"
    if mode == "CS":
        return "CS"
    return "EV"


def _trace_dict(comb) -> dict:
    """Serialize a combustion cycle trace for JSON/charting."""
    return {
        "crank_deg": [round(a, 3) for a in comb.trace.crank_deg],
        "pressure_bar": [round(p / 1e5, 3) for p in comb.trace.pressure_pa],
        "volume_cc": [round(v * 1e6, 3) for v in comb.trace.volume_m3],
        "heat_release_j": [round(q, 4) for q in comb.trace.heat_release_j],
    }


def _animation_dict(comb, fp_cfg, speed_rpm: float) -> dict:
    """Cross-section animation payload: piston travel, pressure, heat release."""
    volumes = comb.trace.volume_m3
    v_min = min(volumes)
    v_max = max(volumes)
    span = max(v_max - v_min, 1e-12)
    stroke_mm = fp_cfg.stroke_m * 1000.0
    bore_mm = 2.0 * math.sqrt(max(fp_cfg.piston_area_m2, 1e-9) / math.pi) * 1000.0
    heat = comb.trace.heat_release_j
    h_max = max(heat) if heat else 1.0
    return {
        "stroke_mm": round(stroke_mm, 2),
        "bore_mm": round(bore_mm, 2),
        "speed_rpm": round(speed_rpm, 1),
        "crank_deg": [round(a, 3) for a in comb.trace.crank_deg],
        "piston_mm": [
            round((v - v_min) / span * stroke_mm, 3) for v in volumes
        ],
        "pressure_bar": [round(p / 1e5, 3) for p in comb.trace.pressure_pa],
        "heat_norm": [
            round(h / h_max, 4) if h_max > 0 else 0.0 for h in heat
        ],
    }


def _tier_load_fraction(steps: list[dict], tier_num: int,
                        cumulative_cap_w: float) -> float:
    """Peak load fraction observed while this tier was governing."""
    tier_steps = [
        s for s in steps
        if s["tier"] == tier_num and s["generation_kw"] > 0.0
    ]
    if not tier_steps:
        return 0.65
    peak_kw = max(s["generation_kw"] for s in tier_steps)
    return max(0.05, min(1.0, peak_kw * 1000.0 / max(1.0, cumulative_cap_w)))


def _gate1_tier_entry(twin: Powertrain, tier_index: int, load_fraction: float,
                      prefer_cantera: bool, speed_rpm: float,
                      seen_in_run: bool) -> dict:
    """One tier's design-point combustion trace and headline metrics."""
    tier = twin.cfg.atpe.tiers[tier_index]
    cumulative_cap_w = sum(
        t.max_electric_w for t in twin.cfg.atpe.tiers[: tier_index + 1]
    )
    comb = gate1_cycle_trace(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_cc=tier.displacement_cc,
        generator_efficiency=twin.cfg.atpe.generator_efficiency,
        prefer_cantera=prefer_cantera,
        tier_index=tier_index,
    )
    _, fp_cfg = tier_physics_profile(tier_index)
    fp = simulate_free_piston(comb, fp_cfg)
    cr, _ = tier_physics_profile(tier_index)
    return {
        "tier": tier_index + 1,
        "tier_label": f"Tier {tier_index + 1}",
        "name": tier.name,
        "displacement_cc": tier.displacement_cc,
        "units": tier.units,
        "max_electric_kw": round(tier.max_electric_w / 1000.0, 1),
        "table_efficiency": round(tier.thermal_efficiency, 3),
        "compression_ratio": cr,
        "load_fraction": round(load_fraction, 4),
        "seen_in_run": seen_in_run,
        "metrics": {
            "imep_bar": round(comb.imep_pa / 1e5, 3),
            "knock_index": round(comb.knock_index, 3),
            "peak_pressure_bar": round(comb.peak_pressure_pa / 1e5, 3),
            "ca50_deg": round(comb.ca50_deg, 3),
            "electric_efficiency": round(comb.electric_efficiency, 4),
            "predicted_tdc_mm": round(fp.predicted_tdc_m * 1000.0, 3),
        },
        "trace": _trace_dict(comb),
        "animation": _animation_dict(comb, fp_cfg, speed_rpm),
    }


def _gate1_payload(twin: Powertrain, steps: list[dict]) -> dict:
    """Gate 1 combustion data for every ATPE tier plus the live active tier."""
    gate_cfg = twin.cfg.atpe.gate1
    gate_enabled = bool(gate_cfg and gate_cfg.enabled)
    prefer_cantera = bool(gate_cfg.prefer_cantera) if gate_cfg else False
    speed_rpm = gate_cfg.reference_speed_rpm if gate_cfg else 2600.0

    active = [s for s in steps if s["tier"] > 0 and s["generation_kw"] > 0.0]
    if not active:
        return {
            "enabled": gate_enabled,
            "has_data": False,
            "message": "No engine-on samples in this run.",
            "active_tier": 0,
            "tiers": [],
        }

    active_tier = max(active, key=lambda s: s["generation_kw"])["tier"]
    tiers_seen = {s["tier"] for s in active}

    tiers: list[dict] = []
    cumulative_cap_w = 0.0
    for tier_index, tier in enumerate(twin.cfg.atpe.tiers):
        cumulative_cap_w += tier.max_electric_w
        tier_num = tier_index + 1
        lf = _tier_load_fraction(steps, tier_num, cumulative_cap_w)
        tiers.append(_gate1_tier_entry(
            twin, tier_index, lf, prefer_cantera, speed_rpm,
            seen_in_run=tier_num in tiers_seen,
        ))

    focus = tiers[active_tier - 1]
    return {
        "enabled": gate_enabled,
        "has_data": True,
        "active_tier": active_tier,
        "message": "",
        "tiers": tiers,
        # Legacy single-trace fields (active tier) for older clients.
        "representative": {
            "tier": focus["tier_label"],
            "name": focus["name"],
            "speed_rpm": round(speed_rpm, 1),
            "load_fraction": focus["load_fraction"],
            **focus["metrics"],
        },
        "trace": focus["trace"],
    }


# --- simulation -> JSON -----------------------------------------------------

def _simulate(cycle_name: str, body_name: str, coupled: bool,
              gate1: bool = False) -> dict:
    factories = _cycle_factories()
    if cycle_name not in factories:
        raise KeyError(f"unknown cycle: {cycle_name}")
    builders = _body_builders(coupled)
    if body_name not in builders:
        raise KeyError(f"unknown body: {body_name}")

    cycle = factories[cycle_name][0]()
    twin: Powertrain = builders[body_name]()
    if gate1:
        twin = Powertrain(with_gate1(twin.cfg), controller=twin.controller)
    result = run(twin, cycle)

    steps = []
    ev_threshold_kw = twin.cfg.control.ev_demand_threshold_w / 1000.0
    for rec in result.records:
        tier = rec.active_index + 1 if rec.active_index >= 0 else 0
        demand_kw = rec.demand_w / 1000.0
        gen_kw = rec.generation_w / 1000.0
        nominal = _governing_tier_for_demand(rec.demand_w, twin.cfg.atpe)
        combustion_tier = tier if tier > 0 and gen_kw > 0 else (
            nominal if rec.mode == "CS" or demand_kw >= ev_threshold_kw else 0
        )
        steps.append({
            "t": round(rec.time_s, 3),
            "speed_kmh": round(rec.speed_ms * 3.6, 2),
            "demand_kw": round(demand_kw, 2),
            "generation_kw": round(gen_kw, 2),
            "buffer_kw": round(rec.buffer_w / 1000.0, 2),
            "battery_kw": round(rec.battery_w / 1000.0, 2),
            "shortfall_kw": round(rec.shortfall_w / 1000.0, 2),
            "surplus_kw": round(rec.surplus_w / 1000.0, 2),
            "mode": rec.mode,
            "tier": tier,
            "nominal_tier": nominal,
            "combustion_tier": combustion_tier,
            "combustion_live": tier > 0 and gen_kw > 0,
            "tier_label": _step_tier_label(tier, rec.mode),
            "efficiency": round(rec.efficiency, 4),
            "imep_bar": round(rec.imep_bar, 3),
            "knock_index": round(rec.knock_index, 3),
            "peak_pressure_bar": round(rec.peak_pressure_bar, 2),
            "predicted_tdc_mm": round(rec.predicted_tdc_mm, 3),
            "battery_soc": round(rec.battery_soc, 4),
            "buffer_soc": round(rec.buffer_soc, 4),
        })

    summary = {
        "config_name": result.config_name,
        "cycle_name": result.cycle_name,
        "distance_km": round(result.distance_km, 2),
        "duration_s": round(result.duration_s, 1),
        "mean_speed_kmh": round(result.mean_speed_kmh, 1),
        "peak_speed_kmh": round(result.peak_speed_kmh, 1),
        "fuel_l_per_100km": round(result.fuel_l_per_100km, 2),
        "equiv_fuel_l_per_100km": round(result.equiv_fuel_l_per_100km, 2),
        "co2_g_per_km": round(result.co2_g_per_km, 1),
        "mean_efficiency": round(result.mean_efficiency, 4),
        "battery_soc_start": round(result.battery_soc_start, 4),
        "battery_soc_end": round(result.battery_soc_end, 4),
        "net_battery_kwh": round(result.net_battery_kwh, 3),
        "buffer_peak_kw": round(result.buffer_peak_kw, 1),
        "battery_peak_kw": round(result.battery_peak_kw, 1),
        "battery_peak_temp_c": round(result.battery_peak_temp_c, 1),
        "shortfall_events": result.shortfall_events,
        "max_shortfall_kw": round(result.max_shortfall_kw, 1),
        "mean_imep_bar": round(result.mean_imep_bar, 3),
        "mean_knock_index": round(result.mean_knock_index, 3),
        "peak_cylinder_pressure_bar": round(result.peak_cylinder_pressure_bar, 2),
        "min_predicted_tdc_mm": round(result.min_predicted_tdc_mm, 3),
        "mode_share": {k: round(v, 4) for k, v in result.mode_share.items()},
        "dt_s": cycle.dt_s,
    }
    gates = {
        "gate1": _gate1_payload(twin, steps),
        "roadmap": _gate_roadmap(),
    }
    return {"summary": summary, "steps": steps, "gates": gates}


# --- test runner -> JSON ----------------------------------------------------

# The test suite is CPU-heavy (~tens of seconds). This server binds to
# localhost by default, but the ThreadingHTTPServer would still let concurrent
# /api/tests requests pile up overlapping suite runs and exhaust the CPU. This
# non-reentrant lock serialises runs so at most one suite executes at a time;
# callers that arrive while a run is in flight get HTTP 429 instead of spawning
# another run.
_TEST_RUN_LOCK = threading.Lock()


class _JSONResult(unittest.TestResult):
    """Collects per-test outcomes with timing for the dashboard."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[dict] = []
        self._start = 0.0

    def startTest(self, test: unittest.case.TestCase) -> None:  # noqa: N802
        super().startTest(test)
        self._start = time.perf_counter()

    def _push(self, test, status: str, message: str = "") -> None:
        method = test._testMethodName
        cls = type(test)
        self.records.append({
            "id": test.id(),
            "name": method,
            "classname": f"{cls.__module__}.{cls.__qualname__}",
            "short_class": cls.__qualname__,
            "doc": (test._testMethodDoc or "").strip(),
            "status": status,
            "message": message.strip(),
            "duration_ms": round((time.perf_counter() - self._start) * 1000, 1),
        })

    def addSuccess(self, test) -> None:  # noqa: N802
        super().addSuccess(test)
        self._push(test, "passed")

    def addFailure(self, test, err) -> None:  # noqa: N802
        super().addFailure(test, err)
        self._push(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err) -> None:  # noqa: N802
        super().addError(test, err)
        self._push(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason) -> None:  # noqa: N802
        super().addSkip(test, reason)
        self._push(test, "skipped", reason)


def _run_tests() -> dict:
    started = time.perf_counter()
    # Ensure the repo root is importable (mirrors running from the project dir),
    # then discover the suite exactly like ``python -m unittest discover -s tests``.
    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    loader = unittest.TestLoader()
    suite = loader.discover(str(_REPO_ROOT / "tests"))
    result = _JSONResult()
    suite.run(result)
    records = result.records
    total = len(records)
    passed = sum(1 for r in records if r["status"] == "passed")
    failed = sum(1 for r in records if r["status"] in ("failed", "error"))
    skipped = sum(1 for r in records if r["status"] == "skipped")
    return {
        "tests": records,
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        },
    }


# --- HTTP plumbing ----------------------------------------------------------

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class _Handler(BaseHTTPRequestHandler):
    server_version = "PhoenixDashboard/1.0"

    # Quieter logging.
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        return

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self._send_json({"error": "not found"}, status=404)
            return
        body = path.read_bytes()
        ctype = _CONTENT_TYPES.get(path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        try:
            if route in ("/", "/index.html"):
                self._send_file(_STATIC / "index.html")
            elif route.startswith("/static/"):
                # Prevent path traversal: resolve and confirm under _STATIC.
                rel = route[len("/static/"):]
                target = (_STATIC / rel).resolve()
                if _STATIC.resolve() in target.parents or target == _STATIC.resolve():
                    self._send_file(target)
                else:
                    self._send_json({"error": "forbidden"}, status=403)
            elif route == "/api/options":
                self._send_json(self._options())
            elif route == "/api/simulate":
                cycle = (query.get("cycle") or ["Mixed"])[0]
                body = (query.get("body") or ["Default (Phase-1 SUV)"])[0]
                coupled = (query.get("coupled") or ["0"])[0] in ("1", "true", "on")
                gate1 = (query.get("gate1") or ["1"])[0] in ("1", "true", "on")
                self._send_json(_simulate(cycle, body, coupled, gate1=gate1))
            elif route == "/api/tests":
                if not _TEST_RUN_LOCK.acquire(blocking=False):
                    self._send_json(
                        {"error": "a test run is already in progress"},
                        status=429)
                else:
                    try:
                        self._send_json(_run_tests())
                    finally:
                        _TEST_RUN_LOCK.release()
            else:
                self._send_json({"error": "not found"}, status=404)
        except KeyError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:  # pragma: no cover - surfaced to the UI
            self._send_json({"error": f"{type(exc).__name__}: {exc}"}, status=500)

    @staticmethod
    def _options() -> dict:
        cycles = [
            {"name": name, "description": desc}
            for name, (_, desc) in _cycle_factories().items()
        ]
        bodies = list(_body_builders(coupled=False).keys())
        return {"cycles": cycles, "bodies": bodies}


def build_app() -> type:
    """Return the request handler class (exposed for testing/embedding)."""
    return _Handler


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = True,
    *,
    allow_remote: bool = False,
) -> None:
    if host not in _LOOPBACK_HOSTS and not allow_remote:
        raise SystemExit(
            f"Refusing to bind {host!r} without --allow-remote "
            "(exposes unauthenticated /api/simulate and /api/tests)."
        )
    httpd = ThreadingHTTPServer((host, port), _Handler)
    url = f"http://{host}:{port}"
    print(f"Project Phoenix dashboard running at {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard.")
    finally:
        httpd.server_close()
