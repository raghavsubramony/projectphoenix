"""Cross-section diagrams: free-piston vs opposed-piston (Phoenix ATPE).

Run:
    python designs/opposed_piston_diagram.py

Outputs:
    designs/opposed_piston_cross_section.png  — side-by-side comparison
    designs/phoenix_opposed_cartridge.png     — Phoenix cartridge only (large)
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, Circle, Rectangle, FancyArrowPatch, Wedge

OUT_DIR = Path(__file__).resolve().parent


def _draw_toyota_style_free_piston(ax) -> None:
    """Single free piston + gas spring (Toyota FPEG style) — NOT opposed."""
    ax.set_xlim(-1.2, 1.2)
    ax.set_ylim(-0.3, 1.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title(
        "A) Free-piston (single)\nToyota FPEG style — NOT opposed",
        fontsize=11,
        fontweight="bold",
        color="#c53030",
    )

    # Cylinder liner
    ax.add_patch(Rectangle((-0.35, 0.1), 0.7, 1.15, fill=False, lw=2.5, ec="#2d3748"))
    # Combustion side (left / small diameter)
    ax.add_patch(Rectangle((-0.28, 0.55), 0.56, 0.55, fc="#fed7d7", ec="#e53e3e", lw=1.5))
    ax.text(0, 0.82, "COMBUSTION\n(chamber)", ha="center", va="center", fontsize=8, color="#742a2a")

    # W-shaped / step piston — one physical piston
    ax.add_patch(Rectangle((-0.22, 0.48), 0.44, 0.12, fc="#2d3748", ec="#1a202c", lw=1.2))
    ax.add_patch(Rectangle((-0.30, 0.35), 0.60, 0.14, fc="#4a5568", ec="#1a202c", lw=1.2))
    ax.text(0, 0.42, "ONE piston\n(W-shape)", ha="center", va="center", fontsize=7.5, color="white")

    # Gas spring return (NOT a second firing piston)
    ax.add_patch(Rectangle((-0.28, 0.12), 0.56, 0.28, fc="#bee3f8", ec="#3182ce", lw=1.5))
    ax.text(0, 0.26, "GAS SPRING\n(bounce — no fuel)", ha="center", va="center", fontsize=8, color="#2c5282")

    # Linear generator coil
    ax.add_patch(Rectangle((-0.38, 0.32), 0.08, 0.35, fc="#9f7aea", ec="#6b46c1", lw=1))
    ax.add_patch(Rectangle((0.30, 0.32), 0.08, 0.35, fc="#9f7aea", ec="#6b46c1", lw=1))
    ax.text(-0.55, 0.5, "Linear\ngenerator", ha="center", fontsize=7, color="#553c9a")
    ax.annotate("", xy=(-0.34, 0.5), xytext=(-0.48, 0.5), arrowprops=dict(arrowstyle="->", color="#553c9a"))

    # Motion arrows
    ax.annotate("", xy=(0, 0.62), xytext=(0, 0.95), arrowprops=dict(arrowstyle="<->", color="#e53e3e", lw=2))
    ax.text(0.52, 0.78, "Oscillates\n← →", fontsize=8, color="#e53e3e")

    ax.text(0, -0.08, "No crankshaft  ·  Return force = gas spring, not 2nd piston", ha="center", fontsize=8, style="italic", color="#718096")


def _draw_opposed_free_piston(ax, *, title_suffix: str = "") -> None:
    """Two pistons, one combustion chamber — Phoenix ATPE style."""
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-0.35, 1.4)
    ax.set_aspect("equal")
    ax.axis("off")
    title = "B) Opposed-piston + free-piston\nPhoenix ATPE cartridge"
    if title_suffix:
        title = title_suffix
    ax.set_title(title, fontsize=11, fontweight="bold", color="#276749")

    # Outer cylinder / liner
    ax.add_patch(Rectangle((-1.05, 0.15), 2.1, 0.95, fill=False, lw=2.5, ec="#2d3748"))

    # Shared combustion chamber in the middle
    ax.add_patch(Rectangle((-0.35, 0.45), 0.70, 0.45, fc="#fed7d7", ec="#e53e3e", lw=2))
    ax.text(0, 0.67, "ONE shared\nCOMBUSTION\nchamber", ha="center", va="center", fontsize=9, fontweight="bold", color="#742a2a")

    # Left piston — fires toward center
    ax.add_patch(Rectangle((-0.95, 0.42), 0.55, 0.22, fc="#2d3748", ec="#1a202c", lw=1.5))
    ax.text(-0.67, 0.53, "Piston\nA", ha="center", va="center", fontsize=8, color="white", fontweight="bold")

    # Right piston — fires toward center
    ax.add_patch(Rectangle((0.40, 0.42), 0.55, 0.22, fc="#2d3748", ec="#1a202c", lw=1.5))
    ax.text(0.67, 0.53, "Piston\nB", ha="center", va="center", fontsize=8, color="white", fontweight="bold")

    # Bounce chambers (outside, both ends)
    ax.add_patch(Rectangle((-1.02, 0.18), 0.38, 0.28, fc="#bee3f8", ec="#3182ce", lw=1.2))
    ax.add_patch(Rectangle((0.64, 0.18), 0.38, 0.28, fc="#bee3f8", ec="#3182ce", lw=1.2))
    ax.text(-0.83, 0.32, "Bounce", ha="center", fontsize=7, color="#2c5282")
    ax.text(0.83, 0.32, "Bounce", ha="center", fontsize=7, color="#2c5282")

    # Linear generators outside each piston
    ax.add_patch(Rectangle((-1.18, 0.38), 0.12, 0.30, fc="#9f7aea", ec="#6b46c1", lw=1))
    ax.add_patch(Rectangle((1.06, 0.38), 0.12, 0.30, fc="#9f7aea", ec="#6b46c1", lw=1))
    ax.text(-1.35, 0.53, "Gen", ha="center", fontsize=7, color="#553c9a")
    ax.text(1.35, 0.53, "Gen", ha="center", fontsize=7, color="#553c9a")

    # Opposed motion arrows — key visual
    ax.annotate("", xy=(-0.38, 0.53), xytext=(-0.78, 0.53),
                arrowprops=dict(arrowstyle="->", color="#38a169", lw=2.5))
    ax.annotate("", xy=(0.38, 0.53), xytext=(0.78, 0.53),
                arrowprops=dict(arrowstyle="->", color="#38a169", lw=2.5))
    ax.text(0, 0.88, "←  push  |  push  →", ha="center", fontsize=10, color="#276749", fontweight="bold")
    ax.text(0, 1.02, "TWO pistons move toward each other at ignition", ha="center", fontsize=8, color="#276749")

    ax.text(0, -0.12, "No crankshaft  ·  Both pistons are real movers (not a gas spring pretending to be piston #2)", ha="center", fontsize=8, style="italic", color="#718096")


def _draw_crank_opposed_for_context(ax) -> None:
    """Achates-style opposed WITH crank — for contrast only."""
    ax.set_xlim(-1.5, 1.5)
    ax.set_ylim(-0.5, 1.3)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("C) Opposed-piston WITH crank\n(Achates — NOT free-piston)", fontsize=11, fontweight="bold", color="#744210")

    ax.add_patch(Rectangle((-1.0, 0.35), 2.0, 0.55, fill=False, lw=2, ec="#2d3748"))
    ax.add_patch(Rectangle((-0.3, 0.48), 0.6, 0.3, fc="#fed7d7", ec="#e53e3e", lw=1.5))
    ax.text(0, 0.63, "Combustion", ha="center", fontsize=8, color="#742a2a")

    ax.add_patch(Rectangle((-0.95, 0.50), 0.55, 0.18, fc="#2d3748"))
    ax.add_patch(Rectangle((0.40, 0.50), 0.55, 0.18, fc="#2d3748"))
    ax.text(-0.67, 0.59, "Piston", ha="center", fontsize=7, color="white")
    ax.text(0.67, 0.59, "Piston", ha="center", fontsize=7, color="white")

    # Crankshaft below
    ax.add_patch(Circle((0, 0.05), 0.12, fc="#cbd5e0", ec="#4a5568", lw=2))
    ax.text(0, 0.05, "CRANK", ha="center", va="center", fontsize=6, fontweight="bold")
    ax.plot([-0.67, -0.67, 0], [0.59, 0.05, 0.05], "k-", lw=2)
    ax.plot([0.67, 0.67, 0], [0.59, 0.05, 0.05], "k-", lw=2)
    ax.text(0, -0.28, "Opposed geometry, but crank locks stroke & timing", ha="center", fontsize=8, style="italic", color="#744210")


def make_comparison_png() -> Path:
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5))
    fig.suptitle(
        "Free-piston vs opposed-piston — why your mental picture is probably panel A",
        fontsize=13,
        fontweight="bold",
        y=1.02,
    )
    _draw_toyota_style_free_piston(axes[0])
    _draw_opposed_free_piston(axes[1])
    _draw_crank_opposed_for_context(axes[2])

    legend_items = [
        mpatches.Patch(fc="#fed7d7", ec="#e53e3e", label="Combustion (fuel burns here)"),
        mpatches.Patch(fc="#bee3f8", ec="#3182ce", label="Bounce / gas spring (no fuel)"),
        mpatches.Patch(fc="#2d3748", label="Piston (solid mover)"),
        mpatches.Patch(fc="#9f7aea", label="Linear generator"),
    ]
    fig.legend(handles=legend_items, loc="lower center", ncol=4, fontsize=9, frameon=True, bbox_to_anchor=(0.5, -0.02))

    out = OUT_DIR / "opposed_piston_cross_section.png"
    fig.savefig(out, dpi=160, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def make_phoenix_only_png() -> Path:
    fig, ax = plt.subplots(figsize=(8, 6))
    _draw_opposed_free_piston(ax, title_suffix="Phoenix ATPE — one cartridge (cross-section)")
    ax.text(
        0,
        -0.28,
        "Gate 1 rig tests ONE of these: 300 cc, 50 mm stroke, opposed pair",
        ha="center",
        fontsize=10,
        fontweight="bold",
        color="#2d3748",
    )
    out = OUT_DIR / "phoenix_opposed_cartridge.png"
    fig.savefig(out, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out


def main() -> None:
    if "--headless" in sys.argv or not sys.stdout.isatty():
        import matplotlib
        matplotlib.use("Agg")
    p1 = make_comparison_png()
    p2 = make_phoenix_only_png()
    print(f"Wrote: {p1}")
    print(f"Wrote: {p2}")


if __name__ == "__main__":
    main()
