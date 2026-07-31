/**
 * Phoenix V3.1 — Vehicle ECU bus contract (C firmware skeleton).
 * Must stay in sync with ecu/bus.py ModeCode and BrainSetpointFrame fields.
 *
 * Build note: compile with -std=c11. No heap on the hot path.
 */
#ifndef PHOENIX_ECU_BUS_H
#define PHOENIX_ECU_BUS_H

#include <stdint.h>
#include <stdbool.h>

#define ECU_FLASH_ID            "PHOENIX-V31-ECU-R3"
#define ECU_INTERFACE_VERSION   "1.0.0"
#define ECU_CYCLE_MS            10
#define ECU_SLOT_COUNT          12
#define ECU_LATENCY_BUDGET_MS   50
#define ECU_BRAIN_TIMEOUT_MS    250
#define ECU_WALL_DERATE_C       195.0f
#define ECU_WALL_INHIBIT_C      205.0f

typedef enum {
    MODE_OFF = 0,
    MODE_IDLE = 1,
    MODE_CITY = 2,
    MODE_HIGHWAY = 3,
    MODE_OVERTAKE = 4,
    MODE_TRACK = 5
} ecu_mode_t;

typedef struct {
    uint8_t  slot_index;
    uint8_t  enabled;
    uint16_t load_scale_milli;      /* 0..1200  => 0.000 .. 1.200 */
    uint16_t ignition_scale_milli;
    uint16_t gen_force_scale_milli;
} ecu_cartridge_setpoint_t;

typedef struct {
    uint32_t sequence;
    ecu_mode_t mode;
    uint32_t demand_w;
    uint32_t target_power_w;
    ecu_cartridge_setpoint_t cartridges[ECU_SLOT_COUNT];
    uint32_t buffer_assist_w;
    uint32_t buffer_precharge_w;
    uint32_t buffer_burst_w;
    uint8_t  brain_alive;
} brain_setpoint_frame_t;

typedef struct {
    uint8_t  slot_index;
    uint8_t  enable;
    uint16_t load_fraction_milli;
    uint16_t ignition_scale_milli;
    uint16_t gen_force_scale_milli;
    uint16_t valve_authority_milli;
} slot_actuator_out_t;

typedef struct {
    uint32_t sequence_ack;
    ecu_mode_t mode;
    slot_actuator_out_t slots[ECU_SLOT_COUNT];
    uint32_t buffer_assist_w;
    uint32_t buffer_burst_w;
    uint32_t buffer_precharge_w;
    uint8_t  safe_state;
    uint8_t  watchdog_ok;
    uint8_t  latency_budget_ok;
} actuator_command_t;

/** Deterministic 10 ms ECU tick — implemented in ecu_runtime.c */
void ecu_accept_brain(const brain_setpoint_frame_t *frame);
void ecu_reset_watchdog(void);
void ecu_tick(const float *wall_temp_c /*[12]*/, float buffer_soc, actuator_command_t *out);
const char *ecu_flash_id(void);

#endif /* PHOENIX_ECU_BUS_H */
