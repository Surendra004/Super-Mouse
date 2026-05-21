#include "supermouse_cdc.h"
#include "driver/usb_serial_jtag.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include <string.h>
#include <stdio.h>
#include <stdlib.h>

#define TAG "CDC"
#define CDC_LINE_MAX 256
#define RX_BUF   1024
#define TX_BUF   1024

/* Forward-declared UI callbacks — implemented in lvgl_ui.cpp */
extern "C" {
void lvgl_ui_set_app_name(const char *name);
void lvgl_ui_set_button(int i, const char *label, uint32_t color);
void lvgl_ui_clear_buttons(void);
void lvgl_ui_render_dynamic(void);
void lvgl_ui_on_tap(int index);
void lvgl_ui_set_time(int hour, int min);
void lvgl_ui_set_weather(const char *temp, const char *condition);
void lvgl_ui_set_song(const char *title);
}

static void handle_line(char *line)
{
    if (strcmp(line, "PING") == 0) {
        const char *pong = "PONG\n";
        usb_serial_jtag_write_bytes((const uint8_t *)pong, strlen(pong),
                                    pdMS_TO_TICKS(50));
        return;
    }
    if (strcmp(line, "CLEAR") == 0) {
        lvgl_ui_clear_buttons();
        return;
    }
    if (strcmp(line, "SHOW") == 0) {
        lvgl_ui_render_dynamic();
        return;
    }

    /* LAYOUT <app_name> */
    if (strncmp(line, "LAYOUT ", 7) == 0) {
        lvgl_ui_set_app_name(line + 7);
        return;
    }

    /* SONG <title> */
    if (strncmp(line, "SONG ", 5) == 0) {
        lvgl_ui_set_song(line + 5);
        return;
    }

    /* TIME HH:MM */
    if (strncmp(line, "TIME ", 5) == 0) {
        int h = 0, m = 0;
        if (sscanf(line + 5, "%d:%d", &h, &m) == 2)
            lvgl_ui_set_time(h, m);
        return;
    }

    /* WEATHER <temp> <condition> */
    if (strncmp(line, "WEATHER ", 8) == 0) {
        char temp[16] = {}, cond[32] = {};
        sscanf(line + 8, "%15s %31[^\n]", temp, cond);
        lvgl_ui_set_weather(temp, cond);
        return;
    }

    /* BTN <index> <label> <color_hex> */
    if (strncmp(line, "BTN ", 4) == 0) {
        int idx;
        char label[64];
        char color_str[16];
        if (sscanf(line + 4, "%d %63s %15s", &idx, label, color_str) == 3) {
            uint32_t color = (uint32_t)strtoul(color_str, NULL, 16);
            lvgl_ui_set_button(idx, label, color);
        }
        return;
    }
}

static void cdc_rx_task(void *arg)
{
    uint8_t buf[128];
    char    line[CDC_LINE_MAX];
    int     pos = 0;

    while (1) {
        int n = usb_serial_jtag_read_bytes(buf, sizeof(buf), pdMS_TO_TICKS(20));
        for (int i = 0; i < n; i++) {
            char c = (char)buf[i];
            if (c == '\n' || c == '\r') {
                if (pos > 0) {
                    line[pos] = '\0';
                    handle_line(line);
                    pos = 0;
                }
            } else if (pos < CDC_LINE_MAX - 1) {
                line[pos++] = c;
            }
        }
    }
}

void supermouse_cdc_init(void)
{
    usb_serial_jtag_driver_config_t cfg = {
        .tx_buffer_size = TX_BUF,
        .rx_buffer_size = RX_BUF,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));

    const char *ready = "READY\n";
    usb_serial_jtag_write_bytes((const uint8_t *)ready, strlen(ready),
                                pdMS_TO_TICKS(100));

    xTaskCreate(cdc_rx_task, "cdc_rx", 4096, NULL, 5, NULL);
    ESP_LOGI(TAG, "CDC ready");
}

void supermouse_cdc_send_tap(int index)
{
    char msg[24];
    int  len = snprintf(msg, sizeof(msg), "TAP %d\n", index);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(50));
}
