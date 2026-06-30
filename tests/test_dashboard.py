"""Tests for the stdlib dashboard HTTP server.

Pure-stdlib unittest (no third-party deps). Run with:

    .venv\\Scripts\\python.exe -m unittest discover -s tests -v

These tests deliberately never hit the success path of ``/api/tests``: that
endpoint runs the whole unittest suite, which (when invoked from within a test)
would re-discover this module and recurse. The DoS guard is exercised instead by
holding the run lock and asserting the endpoint refuses to start a second run.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from dashboard import server
from dashboard.server import _Handler, _simulate


class _LiveServer:
    """Context manager running the dashboard handler on an ephemeral port."""

    def __enter__(self) -> str:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self.httpd.server_address
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{port}"

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _get(url: str):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


class SimulateTest(unittest.TestCase):
    def test_simulate_returns_summary_and_steps(self) -> None:
        payload = _simulate("Highway cruise", "Default (Phase-1 SUV)", False)
        self.assertIn("summary", payload)
        self.assertIn("steps", payload)
        self.assertIn("gates", payload)
        self.assertGreater(len(payload["steps"]), 0)
        summary = payload["summary"]
        for key in ("distance_km", "duration_s", "fuel_l_per_100km"):
            self.assertIn(key, summary)
        gates = payload["gates"]
        self.assertIn("gate1", gates)
        self.assertIn("roadmap", gates)
        self.assertGreaterEqual(len(gates["roadmap"]), 1)
        self.assertIn("has_data", gates["gate1"])
        if gates["gate1"]["has_data"]:
            self.assertIn("trace", gates["gate1"])
            self.assertIn("pressure_bar", gates["gate1"]["trace"])
        # Duration must match the integration convention (one step per sample).
        self.assertAlmostEqual(summary["duration_s"],
                               len(payload["steps"]) * summary["dt_s"],
                               places=1)

    def test_simulate_rejects_unknown_cycle(self) -> None:
        with self.assertRaises(KeyError):
            _simulate("No Such Cycle", "Default (Phase-1 SUV)", False)

    def test_gate1_returns_all_tiers(self) -> None:
        payload = _simulate("Highway cruise", "Default (Phase-1 SUV)", False,
                             gate1=True)
        g1 = payload["gates"]["gate1"]
        self.assertTrue(g1["has_data"])
        self.assertEqual(len(g1["tiers"]), 3)
        for t in g1["tiers"]:
            self.assertIn("trace", t)
            self.assertIn("displacement_cc", t)
            self.assertIn("animation", t)
            anim = t["animation"]
            for key in ("stroke_mm", "bore_mm", "crank_deg", "piston_mm",
                        "pressure_bar", "heat_norm", "speed_rpm"):
                self.assertIn(key, anim)
            self.assertEqual(len(anim["crank_deg"]), len(anim["piston_mm"]))
            self.assertGreater(t["metrics"]["imep_bar"], 0.0)
        # Per-step combustion telemetry when Gate 1 drives the twin.
        engine_on = [s for s in payload["steps"] if s["tier"] > 0]
        self.assertTrue(any(s["imep_bar"] > 0 for s in engine_on))
        s = engine_on[0]
        self.assertIn("combustion_tier", s)
        self.assertIn("nominal_tier", s)
        self.assertTrue(s["combustion_live"])

    def test_gate1_tiers_differ_by_displacement(self) -> None:
        payload = _simulate("Mixed", "Default (Phase-1 SUV)", False, gate1=True)
        tiers = payload["gates"]["gate1"]["tiers"]
        cc = [t["displacement_cc"] for t in tiers]
        self.assertEqual(cc, [100.0, 300.0, 750.0])
        peaks = [t["metrics"]["peak_pressure_bar"] for t in tiers]
        self.assertNotEqual(peaks[0], peaks[2])


class HttpApiTest(unittest.TestCase):
    def test_options_endpoint(self) -> None:
        with _LiveServer() as base:
            status, body = _get(f"{base}/api/options")
        self.assertEqual(status, 200)
        self.assertIn("cycles", body)
        self.assertIn("bodies", body)
        self.assertGreater(len(body["bodies"]), 0)

    def test_simulate_endpoint(self) -> None:
        with _LiveServer() as base:
            status, body = _get(
                f"{base}/api/simulate?cycle=Highway+cruise"
                "&body=Default+(Phase-1+SUV)&coupled=0")
        self.assertEqual(status, 200)
        self.assertIn("summary", body)

    def test_unknown_route_is_404(self) -> None:
        with _LiveServer() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                _get(f"{base}/api/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_static_path_traversal_is_blocked(self) -> None:
        with _LiveServer() as base:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                _get(f"{base}/static/../server.py")
        self.assertIn(ctx.exception.code, (403, 404))

    def test_tests_endpoint_refuses_concurrent_runs(self) -> None:
        # Hold the run lock to simulate an in-flight suite run; the endpoint
        # must refuse with 429 instead of launching an overlapping run. This
        # also avoids recursively executing the suite from within a test.
        acquired = server._TEST_RUN_LOCK.acquire(blocking=False)
        self.assertTrue(acquired, "run lock should be free at test start")
        try:
            with _LiveServer() as base:
                with self.assertRaises(urllib.error.HTTPError) as ctx:
                    _get(f"{base}/api/tests")
            self.assertEqual(ctx.exception.code, 429)
        finally:
            server._TEST_RUN_LOCK.release()


if __name__ == "__main__":
    unittest.main()
