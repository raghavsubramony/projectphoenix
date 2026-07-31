"""Sensor freshness and channel validity for the vehicle ECU."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .bus import SensorFrame
from .dtc import DtcCode, DtcStore
from .limits import ECU_SENSOR_STALE_S, ECU_WALL_INHIBIT_C


@dataclass(frozen=True)
class SensorAssessment:
    """Result of validating one SensorFrame against ECU time."""

    fresh: bool
    age_s: float
    sensors_ok: bool
    inhibit_slots: frozenset[int]
    dtcs: tuple[DtcCode, ...]


def assess_sensors(
    sensors: SensorFrame,
    *,
    ecu_t_s: float,
    slot_count: int,
    stale_s: float = ECU_SENSOR_STALE_S,
) -> SensorAssessment:
    """Validate freshness, finiteness, and thermal / position inhibit channels.

    ``stamped_t_s is None`` means "sampled on this tick" (age 0). HIL delay
    cases set an explicit older stamp to exercise the stale path.
    """
    if sensors.stamped_t_s is None:
        age_s = 0.0
        fresh = bool(sensors.valid)
    else:
        age_s = max(0.0, ecu_t_s - sensors.stamped_t_s)
        fresh = age_s <= stale_s and bool(sensors.valid)

    dtcs: list[DtcCode] = []
    inhibit: set[int] = set()

    if not fresh:
        dtcs.append(DtcCode.SENSOR_STALE)
        inhibit.update(range(slot_count))

    if not math.isfinite(sensors.buffer_soc) or not sensors.buffer_soc_valid:
        dtcs.append(DtcCode.SENSOR_INVALID_SOC)

    if not math.isfinite(sensors.bus_demand_w):
        dtcs.append(DtcCode.SENSOR_INVALID_SOC)

    walls = sensors.slot_wall_temp_c
    wall_valid = sensors.wall_temp_valid
    positions = sensors.slot_position_mm
    pos_valid = sensors.position_valid

    for i in range(slot_count):
        if i >= len(walls) or i >= len(wall_valid) or not wall_valid[i]:
            inhibit.add(i)
            if DtcCode.SENSOR_INVALID_WALL not in dtcs:
                dtcs.append(DtcCode.SENSOR_INVALID_WALL)
        else:
            temp = walls[i]
            if not math.isfinite(temp):
                inhibit.add(i)
                if DtcCode.SENSOR_INVALID_WALL not in dtcs:
                    dtcs.append(DtcCode.SENSOR_INVALID_WALL)
            elif temp > ECU_WALL_INHIBIT_C:
                inhibit.add(i)
                if DtcCode.WALL_OVERTEMP not in dtcs:
                    dtcs.append(DtcCode.WALL_OVERTEMP)

        if i < len(positions) and i < len(pos_valid):
            if not pos_valid[i] or not math.isfinite(positions[i]):
                inhibit.add(i)
                if DtcCode.SENSOR_INVALID_POSITION not in dtcs:
                    dtcs.append(DtcCode.SENSOR_INVALID_POSITION)

    sensors_ok = (
        fresh
        and math.isfinite(sensors.buffer_soc)
        and sensors.buffer_soc_valid
        and math.isfinite(sensors.bus_demand_w)
    )
    return SensorAssessment(
        fresh=fresh,
        age_s=age_s,
        sensors_ok=sensors_ok,
        inhibit_slots=frozenset(inhibit),
        dtcs=tuple(dtcs),
    )


def apply_assessment_dtcs(store: DtcStore, assessment: SensorAssessment) -> None:
    for code in assessment.dtcs:
        store.raise_(code)
