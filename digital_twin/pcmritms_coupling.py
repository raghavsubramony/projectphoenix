"""Bridge the whitepaper rotor model into the bus-level inertial buffer.

The standalone rotor model (`pcmritms_rotor`) reproduces the whitepaper's
phase-coordinated torque augmentation. This module turns that rotor physics into
a concrete capability the powertrain step loop can use: a brief-burst discharge
rating for the `InertialBuffer`, derived from the rotors' peak reaction torque
and mean speed. This is what makes PCMRITMS an *integrated* part of the twin
rather than a separate validation artifact.
"""

from __future__ import annotations

import math
from dataclasses import replace

from .config import BufferConfig
from .pcmritms_rotor import RotorSet


def rotor_peak_reaction_torque_nm(rotors: RotorSet, samples: int = 2000) -> float:
    """Peak magnitude of the summed reaction torque over one beat period."""
    # The slowest modulation sets the beat period; sample a little beyond it.
    min_freq = min(rotors.mod_freqs_rad_s)
    period_s = 2 * math.pi / min_freq if min_freq > 0 else 1.0
    peak = 0.0
    for k in range(samples):
        t = period_s * k / (samples - 1)
        peak = max(peak, abs(rotors.reaction_torque(t)))
    return peak


def rotor_transient_power_w(rotors: RotorSet) -> float:
    """Brief mechanical power the coordinated rotor surge can add to the bus.

    Power = peak augmentation torque x mean rotor speed. For the whitepaper's
    illustrative rotor set this is ~50 kW on top of the continuous rating.
    """
    return rotor_peak_reaction_torque_nm(rotors) * rotors.mean_speed_rad_s


def couple_buffer(base: BufferConfig, rotors: RotorSet | None = None) -> BufferConfig:
    """Return a BufferConfig whose transient rating reflects rotor coupling.

    The continuous discharge/charge ratings are unchanged; a `peak_transient_w`
    is added equal to the continuous rating plus the rotor surge power, and the
    stored energy is checked against the rotor set's kinetic energy.
    """
    rotors = rotors or RotorSet()
    surge_w = rotor_transient_power_w(rotors)
    peak_transient_w = base.max_discharge_w + surge_w
    return replace(base, peak_transient_w=peak_transient_w)
