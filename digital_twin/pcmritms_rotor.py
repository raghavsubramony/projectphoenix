"""Rotor-level PCMRITMS model — faithful reproduction of the whitepaper.

This module mirrors the lumped-parameter model from Appendix A of
"Phase-Controlled Multi-Ring Inertial Torque Modulation System" (Whitepaper v1.0),
using only the standard library (the whitepaper used NumPy).

It exists to validate that the digital twin's buffer parameters are physically
consistent with the source document, and to reproduce the headline result:
    baseline 180 N.m -> peak 242.8 N.m  (+34.9%), stored energy 0.118 MJ.

See docs/10-pcmritms-whitepaper-spec.md section 6.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class RotorSet:
    """Independent coaxial rotors, per the whitepaper's illustrative sizing."""

    inertias_kg_m2: tuple[float, ...] = (0.12, 0.15, 0.10)
    mean_speed_rad_s: float = 800.0
    # Sinusoidal speed-modulation programme (whitepaper Appendix A defaults).
    amplitudes: tuple[float, ...] = (80.0, 80.0, 80.0)
    mod_freqs_rad_s: tuple[float, ...] = (18.0, 18.0, 18.0)
    phases_rad: tuple[float, ...] = field(
        default_factory=lambda: (0.0, 2 * math.pi / 3, 4 * math.pi / 3)
    )

    @property
    def n_rotors(self) -> int:
        return len(self.inertias_kg_m2)

    def stored_energy_j(self) -> float:
        """Total kinetic energy at mean speed: sum 1/2 I omega^2."""
        w = self.mean_speed_rad_s
        return 0.5 * sum(i * w * w for i in self.inertias_kg_m2)

    def reaction_torque(self, t: float) -> float:
        """Summed reaction torque from all rotors at time t (N.m).

        Reaction torque on the summing stage is -I * alpha, where the rotor
        angular acceleration alpha = d/dt[A * sin(Omega t + phi)] * <unit speed>
        is modelled as alpha_i = A_i * Omega_i * cos(Omega_i t + phi_i).
        """
        total = 0.0
        for i in range(self.n_rotors):
            alpha = (self.amplitudes[i] * self.mod_freqs_rad_s[i]
                     * math.cos(self.mod_freqs_rad_s[i] * t + self.phases_rad[i]))
            total += -self.inertias_kg_m2[i] * alpha
        return total


@dataclass
class TorqueModulationResult:
    baseline_nm: float
    peak_nm: float
    boost_percent: float
    stored_energy_mj: float


def simulate_torque_augmentation(
    rotors: RotorSet | None = None,
    primary_torque_nm: float = 180.0,
    duration_s: float = 1.5,
    samples: int = 3000,
) -> TorqueModulationResult:
    """Reproduce the whitepaper Appendix A headline metrics."""
    rotors = rotors or RotorSet()
    peak = float("-inf")
    for k in range(samples):
        t = duration_s * k / (samples - 1)
        total = primary_torque_nm + rotors.reaction_torque(t)
        if total > peak:
            peak = total
    boost = 100.0 * (peak - primary_torque_nm) / primary_torque_nm
    return TorqueModulationResult(
        baseline_nm=primary_torque_nm,
        peak_nm=peak,
        boost_percent=boost,
        stored_energy_mj=rotors.stored_energy_j() / 1e6,
    )
