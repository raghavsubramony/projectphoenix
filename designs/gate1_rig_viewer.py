"""Interactive Gate 1 lab-rig CAD viewer with digital-twin physics.

Parametric 3D model of the single-cartridge ATPE bench (Rig α/β/γ) driven by
``gate1_bench_at_load()`` free-piston integration. View on any PC with Python +
matplotlib; optionally export PNG/GIF/OBJ.

Run from repo root:

    .venv\\Scripts\\python.exe designs\\gate1_rig_viewer.py
    .venv\\Scripts\\python.exe designs\\gate1_rig_viewer.py --animate --cycles 8
    .venv\\Scripts\\python.exe designs\\gate1_rig_viewer.py --headless --export-png designs/gate1_rig.png
    .venv\\Scripts\\python.exe designs\\gate1_rig_viewer.py --rpm 2600 --load 0.75 --tier 1 --stage gamma

FreeCAD solid model (optional): run ``designs/gate1_rig_freecad.py`` inside FreeCAD.
"""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from matplotlib.animation import FuncAnimation
    from matplotlib.figure import Figure

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_DESIGNS = Path(__file__).resolve().parent
if str(_DESIGNS) not in sys.path:
    sys.path.insert(0, str(_DESIGNS))

from digital_twin.single_cylinder import (  # noqa: E402
    SingleCylinderInputs,
    gate1_bench_at_load,
    simulate_1d_combustion,
    simulate_free_piston,
    tier_physics_profile,
)
from gate1_rig_geometry import (  # noqa: E402
    Gate1RigGeometry,
    build_rig_geometry,
    piston_centers_mm,
    rig_axis_extent_mm,
)

FFMPEG_PATH = Path(__file__).resolve().parent / "@ffmpeg-installer" / "win32-x64" / "ffmpeg.exe"

# Populated by _import_matplotlib() after backend selection.
plt = None
FuncAnimation = None
FFMpegWriter = None
PillowWriter = None
GridSpec = None
Poly3DCollection = None


def _want_interactive_display(args: argparse.Namespace) -> bool:
    if args.headless:
        return False
    if args.animate:
        return True
    if args.export_png or args.export_gif or args.export_mp4:
        return False
    return True


def _gui_dependencies_available(backend: str) -> bool:
    """Return True if the optional packages for a matplotlib GUI backend exist."""
    if backend == "TkAgg":
        try:
            import tkinter  # noqa: F401
        except ImportError:
            return False
        return True
    if backend == "QtAgg":
        for module in ("PyQt6.QtWidgets", "PySide6.QtWidgets", "PyQt5.QtWidgets"):
            try:
                __import__(module)
                return True
            except ImportError:
                continue
        return False
    if backend == "WXAgg":
        try:
            import wx  # noqa: F401
        except ImportError:
            return False
        return True
    return False


def _configure_matplotlib_backend(*, interactive: bool) -> bool:
    """Select matplotlib backend. Returns True if a GUI backend is active."""
    import matplotlib

    if not interactive:
        matplotlib.use("Agg")
        return False

    # Qt before Tk — uv-managed Python on Windows often ships without tkinter.
    for candidate in ("QtAgg", "TkAgg", "WXAgg"):
        if not _gui_dependencies_available(candidate):
            continue
        try:
            matplotlib.use(candidate, force=True)
            matplotlib.backends.backend_registry.load_backend_module(candidate)
            return True
        except (ImportError, ValueError, RuntimeError):
            continue

    matplotlib.use("Agg", force=True)
    return False


def _open_path(path: Path) -> None:
    """Open a file with the OS default app (best-effort)."""
    try:
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run(["open", str(path)], check=False)
        else:
            import subprocess
            subprocess.run(["xdg-open", str(path)], check=False)
    except OSError:
        pass


def _save_live_animation(ani: FuncAnimation, path: Path) -> None:
    _import_matplotlib()
    path.parent.mkdir(parents=True, exist_ok=True)
    ani.save(str(path), writer=PillowWriter(fps=20))
    print(f"Saved animation: {path}")


def _print_gui_backend_help() -> None:
    print(
        "No GUI backend available (tkinter missing; Qt not installed).\n"
        "  For a live window:  uv pip install PyQt6\n"
        "  Or use headless:    python designs\\gate1_rig_viewer.py --animate "
        "--headless --export-gif designs\\gate1_rig_demo.gif"
    )


def _import_matplotlib() -> None:
    global plt, FuncAnimation, FFMpegWriter, PillowWriter, GridSpec, Poly3DCollection
    if plt is not None:
        return
    import matplotlib.pyplot as _plt
    from matplotlib.animation import FFMpegWriter as _FFMpegWriter
    from matplotlib.animation import FuncAnimation as _FuncAnimation
    from matplotlib.animation import PillowWriter as _PillowWriter
    from matplotlib.gridspec import GridSpec as _GridSpec
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection as _Poly3DCollection

    plt = _plt
    FuncAnimation = _FuncAnimation
    FFMpegWriter = _FFMpegWriter
    PillowWriter = _PillowWriter
    GridSpec = _GridSpec
    Poly3DCollection = _Poly3DCollection


@dataclass(frozen=True)
class RigPhysicsTrace:
    """Time series from one or more repeated free-piston cycles."""

    t_s: np.ndarray
    left_travel_mm: np.ndarray
    pressure_bar: np.ndarray
    bounce_bar: np.ndarray
    speed_ms: np.ndarray
    bench_passed: bool
    check_lines: tuple[str, ...]
    electric_efficiency: float
    peak_power_kw: float
    imep_bar: float
    bearing_runout_mm: float
    speed_rpm: float
    load_fraction: float


def resolve_ffmpeg() -> str | None:
    if FFMPEG_PATH.is_file():
        return str(FFMPEG_PATH)
    return shutil.which("ffmpeg")


def load_physics_trace(
    *,
    tier_index: int,
    speed_rpm: float,
    load_fraction: float,
    cycles: int,
    prefer_cantera: bool,
) -> RigPhysicsTrace:
    bench = gate1_bench_at_load(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        prefer_cantera=prefer_cantera,
        tier_index=tier_index,
    )
    compression_ratio, fp_cfg = tier_physics_profile(tier_index)
    disp_cc = (100.0, 300.0, 750.0)[max(0, min(tier_index, 2))]
    inp = SingleCylinderInputs(
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
        displacement_m3=disp_cc * 1e-6,
        compression_ratio=compression_ratio,
    )
    comb = simulate_1d_combustion(inp, prefer_cantera=prefer_cantera)
    fp = simulate_free_piston(comb, fp_cfg, speed_rpm=speed_rpm)

    stroke_mm = fp_cfg.stroke_m * 1000.0
    t = np.asarray(fp.t_s, dtype=float)
    x_m = np.asarray(fp.x_m, dtype=float)
    p_c = np.asarray(fp.cylinder_pressure_pa, dtype=float) / 1e5
    p_b = np.asarray(fp.bounce_pressure_pa, dtype=float) / 1e5
    v = np.asarray(fp.v_ms, dtype=float)
    travel_mm = x_m * 1000.0

    if cycles > 1 and len(t) > 1:
        dt = t[-1] - t[0]
        chunks_t, chunks_tr, chunks_p, chunks_b, chunks_v = [], [], [], [], []
        for c in range(cycles):
            offset = c * dt
            chunks_t.append(t + offset)
            chunks_tr.append(travel_mm)
            chunks_p.append(p_c)
            chunks_b.append(p_b)
            chunks_v.append(v)
        t = np.concatenate(chunks_t)
        travel_mm = np.concatenate(chunks_tr)
        p_c = np.concatenate(chunks_p)
        p_b = np.concatenate(chunks_b)
        v = np.concatenate(chunks_v)

    m = bench.measurement
    checks = tuple(
        f"{'PASS' if c.passed else 'FAIL'} {c.name}: {c.measured:.3g} {c.unit}"
        for c in bench.checks
    )
    return RigPhysicsTrace(
        t_s=t,
        left_travel_mm=travel_mm,
        pressure_bar=p_c,
        bounce_bar=p_b,
        speed_ms=v,
        bench_passed=bench.passed,
        check_lines=checks,
        electric_efficiency=m.electric_efficiency,
        peak_power_kw=m.peak_power_kw,
        imep_bar=m.imep_bar,
        bearing_runout_mm=m.bearing_runout_mm,
        speed_rpm=speed_rpm,
        load_fraction=load_fraction,
    )


def _cylinder_mesh(
    center: tuple[float, float, float],
    radius: float,
    length: float,
    axis: str = "x",
    segments: int = 24,
) -> tuple[np.ndarray, list[list[tuple[float, float, float]]]]:
    """Return (center_array, list of quad faces) for a solid cylinder."""
    cx, cy, cz = center
    theta = np.linspace(0.0, 2.0 * math.pi, segments, endpoint=False)
    if axis == "x":
        y0 = cy + radius * np.cos(theta)
        z0 = cz + radius * np.sin(theta)
        x0 = np.full_like(y0, cx - length * 0.5)
        x1 = np.full_like(y0, cx + length * 0.5)
        faces: list[list[tuple[float, float, float]]] = []
        for i in range(segments):
            j = (i + 1) % segments
            faces.append([
                (x0[i], y0[i], z0[i]),
                (x0[j], y0[j], z0[j]),
                (x1[j], y0[j], z0[j]),
                (x1[i], y0[i], z0[i]),
            ])
        return np.array([cx, cy, cz]), faces
    raise ValueError(f"unsupported axis {axis}")


def _box_faces(
    cx: float,
    cy: float,
    cz: float,
    lx: float,
    ly: float,
    lz: float,
) -> list[list[tuple[float, float, float]]]:
    hx, hy, hz = lx * 0.5, ly * 0.5, lz * 0.5
    corners = [
        (cx - hx, cy - hy, cz - hz),
        (cx + hx, cy - hy, cz - hz),
        (cx + hx, cy + hy, cz - hz),
        (cx - hx, cy + hy, cz - hz),
        (cx - hx, cy - hy, cz + hz),
        (cx + hx, cy - hy, cz + hz),
        (cx + hx, cy + hy, cz + hz),
        (cx - hx, cy + hy, cz + hz),
    ]
    idx = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (2, 3, 7, 6), (1, 2, 6, 5), (0, 3, 7, 4)]
    return [[corners[i] for i in face] for face in idx]


class Gate1RigViewer:
    """Matplotlib 3D rig model with instrument traces."""

    def __init__(
        self,
        geom: Gate1RigGeometry,
        trace: RigPhysicsTrace,
        stage: str = "gamma",
    ) -> None:
        _import_matplotlib()
        self.geom = geom
        self.trace = trace
        self.stage = stage
        self.fig = plt.figure(figsize=(14, 9))
        self.gs = GridSpec(2, 2, height_ratios=[1.35, 1.0], hspace=0.32, wspace=0.28)
        self.ax3d = self.fig.add_subplot(self.gs[0, :], projection="3d")
        self.ax_p = self.fig.add_subplot(self.gs[1, 0])
        self.ax_x = self.fig.add_subplot(self.gs[1, 1])
        self._artists: dict[str, object] = {}
        self._animation: FuncAnimation | None = None
        self._vlines: list = []
        self._setup_axes()
        self._draw_static_rig()
        self._draw_instrument_baselines()
        self._update_frame(0)

    def _setup_axes(self) -> None:
        g = self.geom
        x0, x1 = rig_axis_extent_mm(g)
        pad = 60.0
        self.ax3d.set_xlim(x0 - pad, g.load_bank_offset_mm + 120.0)
        self.ax3d.set_ylim(-g.bedplate_width_mm * 0.55, g.bedplate_width_mm * 0.55)
        self.ax3d.set_zlim(-g.bedplate_height_mm, g.bore_mm * 1.8)
        self.ax3d.set_xlabel("X (mm)")
        self.ax3d.set_ylabel("Y (mm)")
        self.ax3d.set_zlabel("Z (mm)")
        stage_tag = {"alpha": "α motion", "beta": "β combustion", "gamma": "γ electrical"}[self.stage]
        title = (
            f"Gate 1 Lab Rig — {g.tier_label} | {stage_tag}\n"
            f"{g.stroke_mm:.0f} mm stroke · {g.bore_mm:.0f} mm bore · "
            f"{g.displacement_cc:.0f} cc · CR {g.compression_ratio:.1f}"
        )
        self.ax3d.set_title(title, fontsize=11)
        self.fig.suptitle(
            "PHOENIX Gate 1 virtual bench — geometry from digital twin",
            fontsize=13,
            fontweight="bold",
        )

    def _add_faces(
        self,
        faces: list[list[tuple[float, float, float]]],
        *,
        facecolor: str,
        edgecolor: str,
        alpha: float,
        key: str,
    ) -> None:
        coll = Poly3DCollection(
            faces,
            facecolors=facecolor,
            edgecolors=edgecolor,
            linewidths=0.4,
            alpha=alpha,
        )
        self.ax3d.add_collection3d(coll)
        self._artists[key] = coll

    def _draw_static_rig(self) -> None:
        g = self.geom
        r = g.piston_radius_mm
        z_c = g.bore_mm * 0.45

        bed_faces = _box_faces(
            0.0,
            0.0,
            g.bedplate_height_mm * 0.5,
            g.bedplate_length_mm,
            g.bedplate_width_mm,
            g.bedplate_height_mm,
        )
        self._add_faces(bed_faces, facecolor="#4a5568", edgecolor="#2d3748", alpha=0.9, key="bed")

        shield_faces = _box_faces(
            0.0,
            0.0,
            g.bore_mm * 0.9,
            g.bedplate_length_mm * 0.55,
            g.bedplate_width_mm * 0.7,
            g.bore_mm * 1.6,
        )
        self._add_faces(
            shield_faces,
            facecolor="#90cdf4",
            edgecolor="#4299e1",
            alpha=0.08,
            key="shield",
        )

        for side, cx in ((-1.0, -g.bounce_length_mm * 0.5), (1.0, g.bounce_length_mm * 0.5 + g.chamber_gap_mm + 2.0 * g.piston_length_mm)):
            _, bounce_faces = _cylinder_mesh(
                (cx, 0.0, z_c),
                r * 1.05,
                g.bounce_length_mm,
            )
            self._add_faces(
                bounce_faces,
                facecolor="#a0aec0",
                edgecolor="#718096",
                alpha=0.55 if self.stage != "alpha" else 0.75,
                key=f"bounce_{side}",
            )

        if self.stage in ("beta", "gamma"):
            _, liner_faces = _cylinder_mesh(
                (0.0, 0.0, z_c),
                r * 0.98,
                g.chamber_gap_mm + g.piston_length_mm * 0.4,
            )
            self._add_faces(
                liner_faces,
                facecolor="#fed7d7",
                edgecolor="#e53e3e",
                alpha=0.35,
                key="liner",
            )

        if self.stage == "gamma":
            lb_faces = _box_faces(g.load_bank_offset_mm, 0.0, z_c, 140.0, 90.0, 70.0)
            self._add_faces(
                lb_faces,
                facecolor="#48bb78",
                edgecolor="#276749",
                alpha=0.65,
                key="load_bank",
            )
            bus_faces = _box_faces(g.load_bank_offset_mm - 90.0, -g.bedplate_width_mm * 0.35, z_c * 0.5, 80.0, 40.0, 25.0)
            self._add_faces(
                bus_faces,
                facecolor="#ecc94b",
                edgecolor="#b7791f",
                alpha=0.8,
                key="dc_bus",
            )

        sensor_x = [rig_axis_extent_mm(g)[0] - 20.0, 0.0, rig_axis_extent_mm(g)[1] + 20.0]
        self._artists["sensors"] = self.ax3d.scatter(
            sensor_x,
            [0.0, 0.0, 0.0],
            [z_c + r + 8.0, z_c + r + 12.0, z_c + r + 8.0],
            c="#f6ad55",
            s=40,
            depthshade=False,
            label="Instruments",
        )

    def _draw_instrument_baselines(self) -> None:
        t = self.trace.t_s
        self.ax_p.plot(t, self.trace.pressure_bar, color="#e53e3e", lw=1.5, label="Cylinder")
        self.ax_p.plot(t, self.trace.bounce_bar, color="#4299e1", lw=1.0, ls="--", label="Bounce")
        self.ax_p.set_ylabel("Pressure (bar)")
        self.ax_p.set_xlabel("Time (s)")
        self.ax_p.legend(loc="upper right", fontsize=8)
        self.ax_p.grid(True, alpha=0.3)
        self.ax_p.set_title("Combustion / bounce pressure (twin)")

        self.ax_x.plot(t, self.trace.left_travel_mm, color="#2b6cb0", lw=1.5, label="Left travel")
        self.ax_x.axhline(self.geom.stroke_mm, color="#718096", ls=":", lw=1.0, label="Stroke max")
        self.ax_x.set_ylabel("Travel (mm)")
        self.ax_x.set_xlabel("Time (s)")
        self.ax_x.legend(loc="upper right", fontsize=8)
        self.ax_x.grid(True, alpha=0.3)
        self.ax_x.set_title("Piston position (twin free-piston integrator)")

        status = "PASS" if self.trace.bench_passed else "FAIL"
        hud = (
            f"Virtual bench @ {self.trace.speed_rpm:.0f} rpm, "
            f"{self.trace.load_fraction * 100:.0f}% load — {status}\n"
            f"η_elec={self.trace.electric_efficiency:.3f} · "
            f"P_peak={self.trace.peak_power_kw:.1f} kW · "
            f"IMEP={self.trace.imep_bar:.2f} bar · "
            f"runout={self.trace.bearing_runout_mm:.3f} mm"
        )
        self._artists["hud"] = self.fig.text(
            0.02,
            0.02,
            hud + "\n" + "\n".join(self.trace.check_lines[:3]),
            fontsize=8,
            family="monospace",
            va="bottom",
            bbox=dict(facecolor="white", alpha=0.85, edgecolor="#cbd5e0"),
        )

    def _update_frame(self, frame_idx: int) -> list:
        g = self.geom
        r = g.piston_radius_mm
        z_c = g.bore_mm * 0.45
        travel = float(self.trace.left_travel_mm[frame_idx % len(self.trace.left_travel_mm)])
        left_x, right_x, _ = piston_centers_mm(g, travel)

        for key in ("piston_left", "piston_right", "coil_left", "coil_right", "comb_flash"):
            if key in self._artists:
                self._artists[key].remove()

        _, left_faces = _cylinder_mesh((left_x, 0.0, z_c), r, g.piston_length_mm)
        left_coll = Poly3DCollection(
            left_faces,
            facecolors="#2d3748",
            edgecolors="#1a202c",
            linewidths=0.3,
            alpha=0.95,
        )
        self.ax3d.add_collection3d(left_coll)
        self._artists["piston_left"] = left_coll

        _, right_faces = _cylinder_mesh((right_x, 0.0, z_c), r, g.piston_length_mm)
        right_coll = Poly3DCollection(
            right_faces,
            facecolors="#2d3748",
            edgecolors="#1a202c",
            linewidths=0.3,
            alpha=0.95,
        )
        self.ax3d.add_collection3d(right_coll)
        self._artists["piston_right"] = right_coll

        if self.stage == "gamma":
            for tag, px, sign in (("coil_left", left_x, -1.0), ("coil_right", right_x, 1.0)):
                _, coil_faces = _cylinder_mesh(
                    (px + sign * (g.piston_length_mm * 0.5 + g.coil_length_mm * 0.45), 0.0, z_c),
                    r * 1.15,
                    g.coil_length_mm,
                )
                coll = Poly3DCollection(
                    coil_faces,
                    facecolors="#9f7aea",
                    edgecolors="#6b46c1",
                    linewidths=0.3,
                    alpha=0.7,
                )
                self.ax3d.add_collection3d(coll)
                self._artists[tag] = coll

        if self.stage in ("beta", "gamma"):
            p_now = float(self.trace.pressure_bar[frame_idx % len(self.trace.pressure_bar)])
            flash_alpha = min(0.85, max(0.05, (p_now - 5.0) / 20.0))
            self._artists["comb_flash"] = self.ax3d.scatter(
                [0.0],
                [0.0],
                [z_c],
                c="#ed8936",
                s=80.0 + 40.0 * flash_alpha,
                alpha=flash_alpha,
                depthshade=False,
            )

        t_now = float(self.trace.t_s[frame_idx % len(self.trace.t_s)])
        for vl in self._vlines:
            vl.remove()
        self._vlines = [
            self.ax_p.axvline(t_now, color="#4a5568", lw=0.8, alpha=0.6),
            self.ax_x.axvline(t_now, color="#4a5568", lw=0.8, alpha=0.6),
        ]

        cursor = self.fig.text(
            0.98,
            0.98,
            f"t={t_now * 1000:.1f} ms",
            ha="right",
            va="top",
            fontsize=9,
            bbox=dict(facecolor="white", alpha=0.7),
        )
        if "cursor" in self._artists:
            self._artists["cursor"].remove()
        self._artists["cursor"] = cursor
        return []

    def show(self) -> None:
        _import_matplotlib()
        plt.show()

    def animate(self, interval_ms: int = 40, max_frames: int = 120) -> FuncAnimation:
        _import_matplotlib()
        n = len(self.trace.t_s)
        if n > max_frames:
            idx = np.linspace(0, n - 1, max_frames, dtype=int)
        else:
            idx = np.arange(n)
        self._animation = FuncAnimation(
            self.fig,
            lambda i: self._update_frame(int(idx[i])),
            frames=len(idx),
            interval=interval_ms,
            blit=False,
            repeat=True,
        )
        return self._animation


def export_obj(path: Path, geom: Gate1RigGeometry, travel_mm: float) -> None:
    """Write a simple OBJ of the static rig at one piston position."""
    g = geom
    r = g.piston_radius_mm
    z_c = g.bore_mm * 0.45
    left_x, right_x, _ = piston_centers_mm(g, travel_mm)
    vertices: list[str] = []
    faces: list[str] = []
    vi = 1

    def add_box(cx: float, cy: float, cz: float, lx: float, ly: float, lz: float) -> None:
        nonlocal vi
        hx, hy, hz = lx * 0.5, ly * 0.5, lz * 0.5
        corners = [
            (cx - hx, cy - hy, cz - hz),
            (cx + hx, cy - hy, cz - hz),
            (cx + hx, cy + hy, cz - hz),
            (cx - hx, cy + hy, cz - hz),
            (cx - hx, cy - hy, cz + hz),
            (cx + hx, cy - hy, cz + hz),
            (cx + hx, cy + hy, cz + hz),
            (cx - hx, cy + hy, cz + hz),
        ]
        start = vi
        for x, y, z in corners:
            vertices.append(f"v {x:.4f} {y:.4f} {z:.4f}")
            vi += 1
        quads = [(1, 2, 3, 4), (5, 6, 7, 8), (1, 2, 6, 5), (3, 4, 8, 7), (2, 3, 7, 6), (1, 4, 8, 5)]
        for q in quads:
            faces.append(f"f {' '.join(str(start + i - 1) for i in q)}")

    add_box(0.0, 0.0, g.bedplate_height_mm * 0.5, g.bedplate_length_mm, g.bedplate_width_mm, g.bedplate_height_mm)
    add_box(left_x, 0.0, z_c, g.piston_length_mm, r * 2.0, r * 2.0)
    add_box(right_x, 0.0, z_c, g.piston_length_mm, r * 2.0, r * 2.0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(vertices + faces) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gate 1 lab rig CAD viewer + twin physics")
    p.add_argument("--tier", type=int, default=1, choices=(0, 1, 2), help="0=micro 1=medium 2=large")
    p.add_argument("--rpm", type=float, default=2600.0)
    p.add_argument("--load", type=float, default=0.75, help="Load fraction 0–1")
    p.add_argument("--stage", choices=("alpha", "beta", "gamma"), default="gamma")
    p.add_argument("--cycles", type=int, default=6, help="Repeat physics trace for animation")
    p.add_argument("--animate", action="store_true")
    p.add_argument("--headless", action="store_true", help="No interactive window")
    p.add_argument("--export-png", type=Path, default=None)
    p.add_argument("--export-gif", type=Path, default=None)
    p.add_argument("--export-mp4", type=Path, default=None)
    p.add_argument("--export-obj", type=Path, default=None)
    p.add_argument("--cantera", action="store_true", help="Use Cantera chemistry if installed")
    return p.parse_args()


def main(args: argparse.Namespace | None = None) -> None:
    if args is None:
        args = parse_args()
    interactive = _want_interactive_display(args)
    gui_ok = _configure_matplotlib_backend(interactive=interactive)
    _import_matplotlib()

    geom = build_rig_geometry(args.tier)
    trace = load_physics_trace(
        tier_index=args.tier,
        speed_rpm=args.rpm,
        load_fraction=args.load,
        cycles=max(1, args.cycles),
        prefer_cantera=args.cantera,
    )
    viewer = Gate1RigViewer(geom, trace, stage=args.stage)

    if args.export_obj:
        travel = float(trace.left_travel_mm[len(trace.left_travel_mm) // 2])
        export_obj(args.export_obj, geom, travel)
        print(f"Wrote OBJ: {args.export_obj}")

    if args.export_png:
        args.export_png.parent.mkdir(parents=True, exist_ok=True)
        viewer.fig.savefig(args.export_png, dpi=160, bbox_inches="tight")
        print(f"Wrote PNG: {args.export_png}")

    if args.animate or args.export_gif or args.export_mp4:
        ani = viewer.animate()
        if args.export_mp4:
            ffmpeg = resolve_ffmpeg()
            if ffmpeg:
                plt.rcParams["animation.ffmpeg_path"] = ffmpeg
                writer = FFMpegWriter(fps=25, bitrate=2000)
                args.export_mp4.parent.mkdir(parents=True, exist_ok=True)
                ani.save(str(args.export_mp4), writer=writer)
                print(f"Wrote MP4: {args.export_mp4}")
            else:
                print("ffmpeg not found — use --export-gif instead")
        if args.export_gif:
            args.export_gif.parent.mkdir(parents=True, exist_ok=True)
            ani.save(str(args.export_gif), writer=PillowWriter(fps=20))
            print(f"Wrote GIF: {args.export_gif}")
        if args.animate and not args.headless:
            if gui_ok:
                viewer.show()
            else:
                fallback = Path(__file__).resolve().parent / "gate1_rig_live.gif"
                _save_live_animation(ani, fallback)
                _print_gui_backend_help()
                _open_path(fallback)
    elif not args.headless and not args.export_png:
        viewer._update_frame(0)
        if gui_ok:
            viewer.show()
        else:
            out = Path(__file__).resolve().parent / "gate1_rig_preview.png"
            viewer.fig.savefig(out, dpi=160, bbox_inches="tight")
            print(f"No GUI backend — saved still: {out}")
            _print_gui_backend_help()
            _open_path(out)
    elif not args.export_png and not args.export_obj:
        viewer._update_frame(0)
        out = Path(__file__).resolve().parent / "gate1_rig_preview.png"
        viewer.fig.savefig(out, dpi=140, bbox_inches="tight")
        print(f"Wrote preview: {out}")


if __name__ == "__main__":
    main(parse_args())
