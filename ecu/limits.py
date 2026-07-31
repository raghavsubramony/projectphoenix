"""Shared ECU thermal / watchdog limits — single source for Python and docs."""

from __future__ import annotations

# Generator force derate begins above this wall temperature.
ECU_WALL_DERATE_C = 195.0
# Slot enable / ignition hard inhibit above this wall temperature.
ECU_WALL_INHIBIT_C = 205.0
# Max age of last brain accept before watchdog trips.
ECU_BRAIN_TIMEOUT_S = 0.250
# Soft real-time cycle budget flag (does not trip safe-state by itself).
ECU_LATENCY_BUDGET_S = 0.050
# Sensor sample older than this vs ECU time → stale (inhibit combustion).
ECU_SENSOR_STALE_S = 0.050
# Controlled shutdown: ramp load to zero over this window, then cut ignition.
ECU_CONTROLLED_SHUTDOWN_S = 0.100
# HV bus must be above this before buffer assist/burst is allowed.
ECU_DC_PRECHARGE_READY_V = 360.0
# Continuous DC-bus assist authority (inverter / contactor rating).
ECU_DC_BUS_CONTINUOUS_W = 160_000.0
