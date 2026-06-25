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
import sys
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
)

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


# --- simulation -> JSON -----------------------------------------------------

def _simulate(cycle_name: str, body_name: str, coupled: bool) -> dict:
    factories = _cycle_factories()
    if cycle_name not in factories:
        raise KeyError(f"unknown cycle: {cycle_name}")
    builders = _body_builders(coupled)
    if body_name not in builders:
        raise KeyError(f"unknown body: {body_name}")

    cycle = factories[cycle_name][0]()
    twin: Powertrain = builders[body_name]()
    result = run(twin, cycle)

    steps = []
    for rec in result.records:
        tier = rec.active_index + 1 if rec.active_index >= 0 else 0
        steps.append({
            "t": round(rec.time_s, 3),
            "speed_kmh": round(rec.speed_ms * 3.6, 2),
            "demand_kw": round(rec.demand_w / 1000.0, 2),
            "generation_kw": round(rec.generation_w / 1000.0, 2),
            "buffer_kw": round(rec.buffer_w / 1000.0, 2),
            "battery_kw": round(rec.battery_w / 1000.0, 2),
            "shortfall_kw": round(rec.shortfall_w / 1000.0, 2),
            "surplus_kw": round(rec.surplus_w / 1000.0, 2),
            "mode": rec.mode,
            "tier": tier,
            "tier_label": "EV" if tier == 0 else f"Tier {tier}",
            "efficiency": round(rec.efficiency, 4),
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
        "mode_share": {k: round(v, 4) for k, v in result.mode_share.items()},
        "dt_s": cycle.dt_s,
    }
    return {"summary": summary, "steps": steps}


# --- test runner -> JSON ----------------------------------------------------

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
                self._send_json(_simulate(cycle, body, coupled))
            elif route == "/api/tests":
                self._send_json(_run_tests())
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


def serve(host: str = "127.0.0.1", port: int = 8000,
          open_browser: bool = True) -> None:
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
