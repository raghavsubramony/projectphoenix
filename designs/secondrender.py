import os
import shutil

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, FuncAnimation, PillowWriter

FFMPEG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "@ffmpeg-installer",
    "win32-x64",
    "ffmpeg.exe",
)


def resolve_ffmpeg() -> str | None:
    if os.path.isfile(FFMPEG_PATH):
        return FFMPEG_PATH
    return shutil.which("ffmpeg")


# Run this code for a more engine-like visualization
fig = plt.figure(figsize=(14, 10))
ax = fig.add_subplot(111, projection="3d")
ax.set_title(
    "PHOENIX-X12 Radial Free-Piston Engine\nRealistic Synchronized Animation",
    fontsize=14,
)
ax.set_xlim(-1.2, 1.2)
ax.set_ylim(-1.2, 1.2)
ax.set_zlim(-0.5, 0.5)

# Create 12 cartridge positions in hexagonal pattern
angles = np.linspace(0, 2 * np.pi, 12, endpoint=False)
colors = ["green"] * 6 + ["blue"] * 4 + ["red"] * 2
cartridges = []

for i in range(12):
    x = 0.9 * np.cos(angles[i])
    y = 0.9 * np.sin(angles[i])
    color = colors[i]
    line, = ax.plot([x, x], [y, y], [-0.4, 0.4], color=color, lw=8, alpha=0.85)
    cartridges.append({"line": line, "x": x, "y": y, "color": color, "phase": i * 0.4})


def update(frame):
    t = frame * 0.018
    for cart in cartridges:
        pos = 0.28 * np.sin(t * 25 + cart["phase"])
        cart["line"].set_3d_properties([-0.35 + pos, 0.35 + pos])
    return []


ani = FuncAnimation(fig, update, frames=500, interval=30)

output_dir = os.path.dirname(os.path.abspath(__file__))
mp4_path = os.path.join(output_dir, "PHOENIX_X12_Radial_Engine_Animation.mp4")
gif_path = os.path.join(output_dir, "PHOENIX_X12_Radial_Engine_Animation.gif")

ffmpeg = resolve_ffmpeg()
if ffmpeg:
    plt.rcParams["animation.ffmpeg_path"] = ffmpeg
    writer = FFMpegWriter(fps=25, bitrate=2200)
    ani.save(mp4_path, writer=writer)
    print(f"Video exported: {mp4_path}")
    print(f"ffmpeg: {ffmpeg}")
else:
    ani.save(gif_path, writer=PillowWriter(fps=25))
    print(f"ffmpeg not found — saved GIF instead: {gif_path}")

plt.show()
