# Phoenix V3.1 — C ECU firmware skeleton

This directory is the microcontroller flash target for the vehicle Ring ECU.
It mirrors the Python reference runtime in `ecu/` (source of truth for laws).

| File | Role |
|------|------|
| `ecu_bus.h` | Fixed message contract + flash ID + thermal/watchdog limits |
| `ecu_runtime.c` | 10 ms tick: load slew, thermal inhibit, buffer clamp, fail-OFF safe-state |

```text
Flash ID: PHOENIX-V31-ECU-R3
Cycle:    10 ms
Slots:    12 (4/6/2 ring)
Safe:     watchdog / missing brain → EMERGENCY_OFF (fail-OFF)
          operator stop → CONTROLLED_SHUTDOWN ramp (Python reference)
Watchdog: sticky until ecu_reset_watchdog()
```

Compile (host smoke):

```bash
gcc -std=c11 -Wall -c ecu_runtime.c -o ecu_runtime.o
```

Python prove (no MCU required):

```powershell
py -3 scripts/run_vehicle_ecu_smoke.py
```
