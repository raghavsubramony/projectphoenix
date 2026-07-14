#!/usr/bin/env python3
"""Run the full digital-twin evidence pack and capture numbered outputs.

Writes a master log under docs/evidence-pack/_runlogs/ and regenerates
evidence-pack artifacts per docs/evidence-pack/README.md.
"""

from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LOG_DIR = _REPO / "docs" / "evidence-pack" / "_runlogs"


STEPS: list[tuple[str, list[str]]] = [
    ("export_evidence_pack", ["export_evidence_pack.py"]),
    ("export_gate1_matrix", ["export_gate1_matrix.py"]),
    ("export_gate4_scaling", ["export_gate4_scaling.py"]),
    ("pcmritms_brain_study", ["run_pcmritms_brain_study.py"]),
    ("pcmritms_stress_study", ["run_pcmritms_stress_study.py"]),
    ("atpe_brain_vv_001", ["run_atpe_brain_vv_001.py"]),
    ("gate6_fleet_learning", ["run_gate6_fleet_learning_demo.py"]),
    ("gate6_endurance", ["run_gate6_endurance_study.py", "--hours", "1"]),
    ("verify", None),  # special: verify.py at repo root
]


def _python() -> str:
    """Prefer py -3 launcher (has numpy); fall back to sys.executable."""
    try:
        r = subprocess.run(
            ["py", "-3", "-c", "import numpy"],
            cwd=_REPO,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return "py"
    except OSError:
        pass
    return sys.executable


def main() -> int:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = _LOG_DIR / f"full_pack_{stamp}.log"
    summary: list[tuple[str, int, float]] = []
    py_cmd = _python()

    with log_path.open("w", encoding="utf-8") as log:
        def emit(line: str = "") -> None:
            print(line, flush=True)
            log.write(line + "\n")
            log.flush()

        emit(f"FULL DIGITAL TWIN PACK  utc={stamp}")
        emit(f"python_launcher={py_cmd}")
        emit(f"sys.executable={sys.executable}")
        emit(f"repo={_REPO}")
        emit("=" * 88)

        overall = 0
        for name, script_args in STEPS:
            if name == "verify":
                cmd = (
                    [py_cmd, "-3", str(_REPO / "verify.py")]
                    if py_cmd == "py"
                    else [sys.executable, str(_REPO / "verify.py")]
                )
            else:
                script = _REPO / "scripts" / script_args[0]
                rest = script_args[1:]
                cmd = (
                    [py_cmd, "-3", str(script), *rest]
                    if py_cmd == "py"
                    else [sys.executable, str(script), *rest]
                )
            emit()
            emit(f">>> START {name}")
            emit(f"    cmd: {' '.join(cmd)}")
            t0 = time.perf_counter()
            proc = subprocess.run(
                cmd,
                cwd=_REPO,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
            )
            dt = time.perf_counter() - t0
            out = proc.stdout or ""
            log.write(out)
            if not out.endswith("\n"):
                log.write("\n")
            # Mirror truncated to console (ASCII-safe for Windows cp1252).
            def _safe(s: str) -> str:
                return s.encode("ascii", errors="replace").decode("ascii")

            lines = out.splitlines()
            if len(lines) <= 80:
                print(_safe(out), end="" if out.endswith("\n") else "\n", flush=True)
            else:
                print(_safe("\n".join(lines[:25])), flush=True)
                print(
                    f"    ... ({len(lines) - 50} lines omitted in console; full in log) ...",
                    flush=True,
                )
                print(_safe("\n".join(lines[-25:])), flush=True)
            status = "OK" if proc.returncode == 0 else f"FAIL({proc.returncode})"
            emit(f"<<< END {name}  {status}  elapsed_s={dt:.1f}")
            summary.append((name, proc.returncode, dt))
            if proc.returncode != 0:
                overall = 1

        emit()
        emit("=" * 88)
        emit("SUMMARY")
        for name, code, dt in summary:
            emit(f"  {'OK' if code == 0 else 'FAIL':<4}  {dt:7.1f}s  {name}")
        emit(f"OVERALL={'PASS' if overall == 0 else 'FAIL'}")
        emit(f"LOG={log_path}")

    print(f"\nWrote {log_path}", flush=True)
    return overall


if __name__ == "__main__":
    raise SystemExit(main())
