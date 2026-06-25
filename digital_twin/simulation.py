"""Simulation runner: drive a Powertrain over a DriveCycle and aggregate metrics."""

from __future__ import annotations

from dataclasses import dataclass, field

from .drive_cycles import DriveCycle
from .powertrain import Powertrain, StepResult
from .config import GASOLINE_LHV_MJ_PER_KG, GASOLINE_DENSITY_KG_PER_L

# Re-export for the package public API.
StepRecord = StepResult

_KW = 1_000.0


@dataclass
class Result:
    """Aggregated outcome of a simulation run plus the raw telemetry."""

    cycle_name: str
    config_name: str
    records: list[StepResult] = field(default_factory=list)

    # --- derived metrics (filled by `_finalize`) ---
    distance_km: float = 0.0
    duration_s: float = 0.0
    mean_speed_kmh: float = 0.0
    peak_speed_kmh: float = 0.0
    fuel_l: float = 0.0
    fuel_l_per_100km: float = 0.0
    equiv_fuel_l_per_100km: float = 0.0  # SoC-corrected (charge-sustaining)
    co2_g_per_km: float = 0.0
    mean_efficiency: float = 0.0
    mode_share: dict[str, float] = field(default_factory=dict)
    battery_soc_start: float = 0.0
    battery_soc_end: float = 0.0
    net_battery_kwh: float = 0.0
    battery_peak_kw: float = 0.0
    battery_throughput_kj: float = 0.0
    battery_peak_temp_c: float = 0.0
    battery_heat_loss_kj: float = 0.0
    battery_efc: float = 0.0               # equivalent full cycles this run
    battery_efc_per_100km: float = 0.0
    projected_pack_life_km: float = 0.0    # 0 if no cycle-life rating configured
    buffer_throughput_kj: float = 0.0
    buffer_peak_kw: float = 0.0
    buffer_min_soc: float = 1.0    # lowest reservoir level seen (reserve health)
    shortfall_events: int = 0
    max_shortfall_kw: float = 0.0

    def report(self) -> str:
        lines = [
            f"=== {self.config_name} | {self.cycle_name} ===",
            f"  Distance            : {self.distance_km:8.2f} km "
            f"over {self.duration_s/60:.1f} min",
            f"  Speed (mean / peak) : {self.mean_speed_kmh:6.1f} / "
            f"{self.peak_speed_kmh:.1f} km/h",
            f"  Fuel used           : {self.fuel_l:8.3f} L "
            f"({self.fuel_l_per_100km:.2f} L/100km)",
            f"  Fuel (SoC-corrected): {self.equiv_fuel_l_per_100km:8.2f} "
            "L/100km (charge-sustaining equivalent)",
            f"  CO2                 : {self.co2_g_per_km:8.1f} g/km",
            f"  Mean ATPE efficiency: {self.mean_efficiency*100:7.1f} % "
            "(while generating)",
            f"  Battery SoC         : {self.battery_soc_start*100:5.1f}% -> "
            f"{self.battery_soc_end*100:5.1f}%  "
            f"(net {self.net_battery_kwh:+.2f} kWh)",
            f"  Buffer throughput   : {self.buffer_throughput_kj:8.1f} kJ, "
            f"peak {self.buffer_peak_kw:.1f} kW"
            f" (min reservoir {self.buffer_min_soc*100:.0f}%)",
            f"  Battery durability  : {self.battery_efc:8.4f} EFC "
            f"({self.battery_efc_per_100km:.3f}/100km)"
            + (f", ~{self.projected_pack_life_km/1000:.0f}k km life"
               if self.projected_pack_life_km > 0 else ""),
            f"  Battery thermal     : peak {self.battery_peak_temp_c:6.1f} C, "
            f"{self.battery_heat_loss_kj:.1f} kJ I2R heat",
            "  Mode share          : " + ", ".join(
                f"{k} {v*100:.0f}%" for k, v in self.mode_share.items()),
            f"  Capability shortfall: {self.shortfall_events} steps, "
            f"max {self.max_shortfall_kw:.1f} kW",
        ]
        return "\n".join(lines)


def run(twin: Powertrain, cycle: DriveCycle) -> Result:
    """Run `twin` over `cycle` and return aggregated metrics + telemetry."""
    accels = cycle.accelerations()
    dt = cycle.dt_s
    result = Result(cycle_name=cycle.name, config_name=twin.cfg.name)
    result.battery_soc_start = twin.battery.soc

    distance_m = 0.0
    fuel_l = 0.0
    co2_kg = 0.0
    eff_weighted = 0.0
    eff_weight = 0.0
    speed_sum = 0.0
    peak_speed = 0.0
    mode_counts: dict[str, int] = {}
    buffer_throughput_j = 0.0
    buffer_peak_w = 0.0
    buffer_min_soc = 1.0
    battery_discharge_j = 0.0
    battery_peak_w = 0.0
    shortfall_events = 0
    max_shortfall_w = 0.0

    for i, speed in enumerate(cycle.speeds_ms):
        rec = twin.step(speed, accels[i], cycle.grades_rad[i], dt)
        result.records.append(rec)

        distance_m += speed * dt
        fuel_l += rec.fuel_l
        co2_kg += rec.co2_kg
        speed_sum += speed
        peak_speed = max(peak_speed, speed)

        if rec.generation_w > 0.0:
            eff_weighted += rec.efficiency * rec.generation_w * dt
            eff_weight += rec.generation_w * dt

        label = rec.mode if rec.active_index < 0 else f"Tier {rec.active_index + 1}"
        mode_counts[label] = mode_counts.get(label, 0) + 1

        buffer_throughput_j += abs(rec.buffer_w) * dt
        buffer_peak_w = max(buffer_peak_w, abs(rec.buffer_w))
        buffer_min_soc = min(buffer_min_soc, rec.buffer_soc)

        # Battery discharge stress (peak C-rate proxy + total energy drawn).
        if rec.battery_w > 0.0:
            battery_discharge_j += rec.battery_w * dt
            battery_peak_w = max(battery_peak_w, rec.battery_w)

        if rec.shortfall_w > 1.0:
            shortfall_events += 1
            max_shortfall_w = max(max_shortfall_w, rec.shortfall_w)

    n = len(cycle.speeds_ms)
    result.distance_km = distance_m / 1000.0
    result.duration_s = cycle.duration_s
    result.mean_speed_kmh = (speed_sum / n) * 3.6 if n else 0.0
    result.peak_speed_kmh = peak_speed * 3.6
    result.fuel_l = fuel_l
    result.fuel_l_per_100km = (fuel_l / result.distance_km * 100.0
                               if result.distance_km > 0 else 0.0)
    result.co2_g_per_km = (co2_kg * 1000.0 / result.distance_km
                           if result.distance_km > 0 else 0.0)
    result.mean_efficiency = eff_weighted / eff_weight if eff_weight > 0 else 0.0
    result.mode_share = {k: v / n for k, v in mode_counts.items()}
    result.battery_soc_end = twin.battery.soc
    result.net_battery_kwh = (
        (result.battery_soc_end - result.battery_soc_start)
        * twin.cfg.battery.usable_capacity_j / 3.6e6
    )
    # SoC-corrected fuel: charge any net battery depletion back as the fuel that
    # would have been needed to generate it (and credit any net charging). This
    # is the standard charge-sustaining correction (cf. SAE J1711) that makes
    # fuel numbers comparable across policies with different SoC drift.
    best_eff = max(t.thermal_efficiency for t in twin.cfg.atpe.tiers)
    drained_j = -result.net_battery_kwh * 3.6e6  # +ve when battery was depleted
    equiv_fuel_j = drained_j / best_eff
    equiv_fuel_l = equiv_fuel_j / (
        GASOLINE_LHV_MJ_PER_KG * 1e6 * GASOLINE_DENSITY_KG_PER_L)
    result.equiv_fuel_l_per_100km = (
        (fuel_l + equiv_fuel_l) / result.distance_km * 100.0
        if result.distance_km > 0 else 0.0
    )
    result.buffer_throughput_kj = buffer_throughput_j / 1000.0
    result.buffer_peak_kw = buffer_peak_w / _KW
    result.buffer_min_soc = buffer_min_soc
    result.battery_peak_kw = battery_peak_w / _KW
    result.battery_throughput_kj = battery_discharge_j / 1000.0
    # Thermal + durability rollup (thermal fields stay at ambient when the
    # lumped-thermal model is disabled).
    result.battery_peak_temp_c = twin.battery.peak_temperature_c
    result.battery_heat_loss_kj = twin.battery.heat_loss_j / 1000.0
    cap_j = twin.cfg.battery.usable_capacity_j
    # Equivalent full cycles: one EFC = one full charge + full discharge, i.e.
    # 2x usable capacity of bidirectional throughput.
    result.battery_efc = (twin.battery.throughput_j / (2.0 * cap_j)
                          if cap_j > 0 else 0.0)
    result.battery_efc_per_100km = (
        result.battery_efc / result.distance_km * 100.0
        if result.distance_km > 0 else 0.0)
    rating = twin.cfg.battery.cycle_life_efc
    result.projected_pack_life_km = (
        rating / result.battery_efc_per_100km * 100.0
        if rating and result.battery_efc_per_100km > 0 else 0.0)
    result.shortfall_events = shortfall_events
    result.max_shortfall_kw = max_shortfall_w / _KW
    return result
