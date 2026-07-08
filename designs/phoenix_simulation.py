import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, FuncAnimation, PillowWriter
import os
import shutil

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


class RealisticPhoenixAnimator:    
    def __init__(self):
        self.fig = plt.figure(figsize=(14, 10))
        self.ax = self.fig.add_subplot(111, projection='3d')
        self.ax.set_xlim(-1.3, 1.3)
        self.ax.set_ylim(-1.3, 1.3)
        self.ax.set_zlim(-0.5, 0.5)
        self.ax.set_title('PHOENIX-X12 Full 12-Cartridge Engine\nRealistic Free-Piston Dynamics Simulation', fontsize=14)
        
        self.cartridges = []
        angles = np.linspace(0, 2*np.pi, 12, endpoint=False)
        colors = ['green']*6 + ['blue']*4 + ['red']*2
        
        for i, (angle, color) in enumerate(zip(angles, colors)):
            cart = {
                'angle': angle,
                'color': color,
                'phase_offset': i * 0.3,
                'line': None,
                'left_piston': None,
                'right_piston': None,
                'combustion': None
            }
            self.cartridges.append(cart)
        
        self.time_text = self.ax.text2D(0.05, 0.95, '', transform=self.ax.transAxes, fontsize=11, bbox=dict(facecolor='white', alpha=0.7))
    
    def update(self, frame):
        t = frame * 0.012  # time step
        
        for i, cart in enumerate(self.cartridges):
            phase = t * 32 + cart['phase_offset']
            
            # Realistic motion: asymmetric combustion push + spring return
            combustion = 0.5 * (np.sin(phase) ** 3) if np.sin(phase) > 0.3 else 0
            left_pos = 0.5 + 0.4 * np.sin(phase + combustion) * 0.6
            right_pos = -0.5 + 0.4 * np.cos(phase + combustion * 0.7) * 0.6
            
            x = 0.95 * np.cos(cart['angle'])
            y = 0.95 * np.sin(cart['angle'])
            
            # Left piston
            if cart['left_piston'] is None:
                cart['left_piston'] = self.ax.plot([x, x], [y, y], [left_pos-0.08, left_pos+0.08], 
                                                 color=cart['color'], lw=6, solid_capstyle='round')[0]
            else:
                cart['left_piston'].set_3d_properties([left_pos-0.08, left_pos+0.08])
            
            # Right piston
            if cart['right_piston'] is None:
                cart['right_piston'] = self.ax.plot([x, x], [y, y], [right_pos-0.08, right_pos+0.08], 
                                                  color=cart['color'], lw=6, solid_capstyle='round')[0]
            else:
                cart['right_piston'].set_3d_properties([right_pos-0.08, right_pos+0.08])
            
            # Combustion flash
            if cart['combustion'] is None:
                cart['combustion'] = self.ax.scatter([x], [y], [(left_pos + right_pos)/2], 
                                                   color='orange', s=80, alpha=0)
            else:
                alpha = 0.85 if combustion > 0.3 else 0.08
                cart['combustion']._offsets3d = ([x], [y], [(left_pos + right_pos)/2])
                cart['combustion'].set_alpha(alpha)
        
        self.time_text.set_text(f'Time: {t:.3f}s | Cycle: {int(t*15)} | All 12 Cartridges Active')
        return []
 
# ====================== RUN & EXPORT ======================
animator = RealisticPhoenixAnimator()
ani = FuncAnimation(animator.fig, animator.update, frames=450, interval=35, blit=False)

output_dir = os.path.dirname(os.path.abspath(__file__))
mp4_path = os.path.join(output_dir, "PHOENIX_X12_Realistic_12_Cartridge_Simulation.mp4")
gif_path = os.path.join(output_dir, "PHOENIX_X12_Realistic_12_Cartridge_Simulation.gif")

ffmpeg = resolve_ffmpeg()
if ffmpeg:
    plt.rcParams["animation.ffmpeg_path"] = ffmpeg
    writer = FFMpegWriter(fps=25, metadata=dict(artist="PHOENIX-X12 Team"), bitrate=2500)
    ani.save(mp4_path, writer=writer)
    print(f"Video exported: {mp4_path}")
    print(f"ffmpeg: {ffmpeg}")
else:
    ani.save(gif_path, writer=PillowWriter(fps=25))
    print(f"ffmpeg not found — saved GIF instead: {gif_path}")

plt.show()