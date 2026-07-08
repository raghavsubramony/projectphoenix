"""Actuator plant model — sensor delay, current limits, back-EMF."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from designs.phoenix_v3.config import PhoenixV3Config
from designs.phoenix_v3.constants import clamp


@dataclass
class SensorSample:
    t_s: float
    x_a_m: float
    x_b_m: float
    pressure_pa: float


@dataclass
class SensorDelayBuffer:
    """Ring buffer of plant states for delayed ECU feedback."""

    max_history_s: float = 0.010
    samples: deque[SensorSample] = field(default_factory=deque)

    def push(self, t_s: float, x_a_m: float, x_b_m: float, pressure_pa: float) -> None:
        self.samples.append(SensorSample(t_s, x_a_m, x_b_m, pressure_pa))
        cutoff = t_s - self.max_history_s
        while self.samples and self.samples[0].t_s < cutoff:
            self.samples.popleft()

    def read(self, t_s: float, delay_s: float) -> SensorSample | None:
        if not self.samples:
            return None
        target = t_s - max(delay_s, 0.0)
        chosen = self.samples[0]
        for sample in self.samples:
            if sample.t_s <= target:
                chosen = sample
            else:
                break
        return chosen


@dataclass
class GeneratorActuatorChannel:
    """Current-limited linear generator with back-EMF."""

    current_a: float = 0.0

    def reset(self) -> None:
        self.current_a = 0.0

    def realized_force_n(
        self,
        f_desired_n: float,
        v_outward_ms: float,
        dt: float,
        cfg: PhoenixV3Config,
    ) -> float:
        if not cfg.actuator_model_enabled:
            return max(f_desired_n, 0.0)

        k_f = max(cfg.generator_force_per_amp_n, 1e-6)
        i_target = clamp(f_desired_n / k_f, 0.0, cfg.max_generator_current_a)
        max_di = cfg.current_slew_a_per_s * dt
        self.current_a += clamp(i_target - self.current_a, -max_di, max_di)
        self.current_a = clamp(self.current_a, 0.0, cfg.max_generator_current_a)

        f_em = self.current_a * k_f
        f_back_emf = cfg.back_emf_coeff_n_s_m * max(v_outward_ms, 0.0)
        return max(f_em - f_back_emf, 0.0)


@dataclass
class ActuatorPlant:
    sensor_buffer: SensorDelayBuffer = field(default_factory=SensorDelayBuffer)
    channel_a: GeneratorActuatorChannel = field(default_factory=GeneratorActuatorChannel)
    channel_b: GeneratorActuatorChannel = field(default_factory=GeneratorActuatorChannel)

    def reset(self) -> None:
        self.sensor_buffer = SensorDelayBuffer()
        self.channel_a.reset()
        self.channel_b.reset()

    def observe(self, t_s: float, x_a_m: float, x_b_m: float, pressure_pa: float) -> None:
        self.sensor_buffer.push(t_s, x_a_m, x_b_m, pressure_pa)

    def delayed_state(
        self,
        t_s: float,
        x_a_m: float,
        x_b_m: float,
        pressure_pa: float,
        cfg: PhoenixV3Config,
    ) -> tuple[float, float, float]:
        if not cfg.actuator_model_enabled or cfg.sensor_delay_s <= 0.0:
            return x_a_m, x_b_m, pressure_pa
        sample = self.sensor_buffer.read(t_s, cfg.sensor_delay_s)
        if sample is None:
            return x_a_m, x_b_m, pressure_pa
        return sample.x_a_m, sample.x_b_m, sample.pressure_pa

    def apply_generator_force(
        self,
        side: str,
        f_desired_n: float,
        v_outward_ms: float,
        dt: float,
        cfg: PhoenixV3Config,
    ) -> float:
        channel = self.channel_a if side == "a" else self.channel_b
        return channel.realized_force_n(f_desired_n, v_outward_ms, dt, cfg)
