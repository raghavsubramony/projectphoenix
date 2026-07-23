/**
 * Phoenix V3.1 — Minimal C ECU runtime skeleton for vehicle flash.
 * Mirrors ecu/runtime.py laws: slew load, clamp buffer, safe-state hold.
 */
#include "ecu_bus.h"
#include <string.h>

static brain_setpoint_frame_t g_setpoints;
static uint8_t g_have_setpoints;
static uint8_t g_watchdog_trip;
static actuator_command_t g_latched;
static uint16_t g_load_milli[ECU_SLOT_COUNT];

const char *ecu_flash_id(void)
{
    return ECU_FLASH_ID;
}

void ecu_accept_brain(const brain_setpoint_frame_t *frame)
{
    if (frame == 0) {
        return;
    }
    g_setpoints = *frame;
    g_have_setpoints = 1;
    g_watchdog_trip = 0;
}

static uint16_t clamp_u16(int v, int lo, int hi)
{
    if (v < lo) return (uint16_t)lo;
    if (v > hi) return (uint16_t)hi;
    return (uint16_t)v;
}

void ecu_tick(const float *wall_temp_c, float buffer_soc, actuator_command_t *out)
{
    int i;
    if (out == 0) {
        return;
    }
    memset(out, 0, sizeof(*out));

    if (!g_have_setpoints || g_watchdog_trip || !g_setpoints.brain_alive) {
        if (g_latched.watchdog_ok || g_latched.safe_state) {
            *out = g_latched;
            out->safe_state = 1;
            out->watchdog_ok = 0;
            out->buffer_assist_w = 0;
            out->buffer_burst_w = 0;
        } else {
            out->mode = MODE_OFF;
            out->safe_state = 1;
            out->watchdog_ok = 0;
        }
        return;
    }

    out->sequence_ack = g_setpoints.sequence;
    out->mode = g_setpoints.mode;
    out->watchdog_ok = 1;
    out->latency_budget_ok = 1;
    out->safe_state = 0;

    for (i = 0; i < ECU_SLOT_COUNT; ++i) {
        const ecu_cartridge_setpoint_t *sp = &g_setpoints.cartridges[i];
        uint16_t target = sp->enabled ? sp->load_scale_milli : 0;
        int delta = (int)target - (int)g_load_milli[i];
        /* ~2.0 /s slew at 10 ms => 20 milli per tick */
        if (delta > 20) delta = 20;
        if (delta < -20) delta = -20;
        g_load_milli[i] = clamp_u16((int)g_load_milli[i] + delta, 0, 1200);

        out->slots[i].slot_index = (uint8_t)i;
        out->slots[i].enable = (sp->enabled && g_load_milli[i] > 10) ? 1 : 0;
        if (wall_temp_c && wall_temp_c[i] > 205.0f) {
            out->slots[i].enable = 0;
            g_load_milli[i] = 0;
        }
        out->slots[i].load_fraction_milli = g_load_milli[i];
        out->slots[i].ignition_scale_milli = out->slots[i].enable ? sp->ignition_scale_milli : 0;
        out->slots[i].gen_force_scale_milli = out->slots[i].enable ? sp->gen_force_scale_milli : 0;
        out->slots[i].valve_authority_milli = out->slots[i].enable ? 1000 : 0;
    }

    if (buffer_soc < 0.15f) {
        out->buffer_assist_w = 0;
        out->buffer_burst_w = 0;
        out->buffer_precharge_w = g_setpoints.buffer_precharge_w;
    } else {
        out->buffer_assist_w = g_setpoints.buffer_assist_w;
        out->buffer_burst_w = g_setpoints.buffer_burst_w;
        out->buffer_precharge_w = g_setpoints.buffer_precharge_w;
        if (out->buffer_assist_w > 40000u) out->buffer_assist_w = 40000u;
        if (out->buffer_burst_w > 120000u) out->buffer_burst_w = 120000u;
    }

    g_latched = *out;
}
