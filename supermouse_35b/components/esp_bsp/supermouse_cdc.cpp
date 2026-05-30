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
void lvgl_ui_set_app_slot(int i, const char *name, const char *abbr, uint32_t color);
void lvgl_ui_set_widget(int i, const char *title, const char *line1, const char *line2);
void lvgl_ui_set_background(uint32_t color);
void lvgl_ui_add_keyboard_word(const char *word);
void lvgl_ui_add_keyboard_pair(const char *previous, const char *next);
bool lvgl_ui_background_upload_begin(size_t size);
bool lvgl_ui_background_upload_write(const uint8_t *data, size_t len);
bool lvgl_ui_background_upload_end(void);
}

static bool s_bg_rx_active = false;
static size_t s_bg_rx_remaining = 0;

static char *next_field(char **cursor)
{
    if (!cursor || !*cursor) return NULL;
    char *field = *cursor;
    char *sep = strchr(field, '|');
    if (sep) {
        *sep = '\0';
        *cursor = sep + 1;
    } else {
        *cursor = NULL;
    }
    return field;
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

    /* VOICE <line1>|<line2> */
    if (strncmp(line, "VOICE ", 6) == 0) {
        char *cursor = line + 6;
        char *line1 = next_field(&cursor);
        char *line2 = next_field(&cursor);
        lvgl_ui_set_widget(1, "Voice", line1 ? line1 : "", line2 ? line2 : "");
        return;
    }

    /* KWORD <word> - learned keyboard word from PC launcher */
    if (strncmp(line, "KWORD ", 6) == 0) {
        lvgl_ui_add_keyboard_word(line + 6);
        return;
    }

    /* KPAIR <previous>|<next> - learned next-word pair */
    if (strncmp(line, "KPAIR ", 6) == 0) {
        char *cursor = line + 6;
        char *previous = next_field(&cursor);
        char *next = next_field(&cursor);
        if (previous && next) {
            lvgl_ui_add_keyboard_pair(previous, next);
        }
        return;
    }

    /* BG <color_hex> */
    if (strncmp(line, "BG ", 3) == 0) {
        lvgl_ui_set_background((uint32_t)strtoul(line + 3, NULL, 16));
        return;
    }

    /* BGIMG <raw_rgb565_size> followed by raw bytes */
    if (strncmp(line, "BGIMG ", 6) == 0) {
        size_t size = (size_t)strtoul(line + 6, NULL, 10);
        if (lvgl_ui_background_upload_begin(size)) {
            s_bg_rx_active = true;
            s_bg_rx_remaining = size;
            const char *ok = "BGREADY\n";
            usb_serial_jtag_write_bytes((const uint8_t *)ok, strlen(ok), pdMS_TO_TICKS(50));
        } else {
            const char *err = "BGERR\n";
            usb_serial_jtag_write_bytes((const uint8_t *)err, strlen(err), pdMS_TO_TICKS(50));
        }
        return;
    }

    /* APP <index>|<name>|<abbr>|<color_hex> */
    if (strncmp(line, "APP ", 4) == 0) {
        char *cursor = line + 4;
        char *idx_s = next_field(&cursor);
        char *name = next_field(&cursor);
        char *abbr = next_field(&cursor);
        char *color_s = next_field(&cursor);
        if (idx_s && name && abbr && color_s) {
            lvgl_ui_set_app_slot(atoi(idx_s), name, abbr, (uint32_t)strtoul(color_s, NULL, 16));
        }
        return;
    }

    /* WIDGET <index>|<title>|<line1>|<line2> */
    if (strncmp(line, "WIDGET ", 7) == 0) {
        char *cursor = line + 7;
        char *idx_s = next_field(&cursor);
        char *title = next_field(&cursor);
        char *line1 = next_field(&cursor);
        char *line2 = next_field(&cursor);
        if (idx_s && title && line1 && line2) {
            lvgl_ui_set_widget(atoi(idx_s), title, line1, line2);
        }
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
            if (s_bg_rx_active) {
                size_t chunk = n - i;
                if (chunk > s_bg_rx_remaining) {
                    chunk = s_bg_rx_remaining;
                }
                if (!lvgl_ui_background_upload_write(buf + i, chunk)) {
                    s_bg_rx_active = false;
                    s_bg_rx_remaining = 0;
                    const char *err = "BGERR\n";
                    usb_serial_jtag_write_bytes((const uint8_t *)err, strlen(err), pdMS_TO_TICKS(50));
                    break;
                }
                s_bg_rx_remaining -= chunk;
                i += (int)chunk - 1;
                if (s_bg_rx_remaining == 0) {
                    s_bg_rx_active = false;
                    bool ok = lvgl_ui_background_upload_end();
                    const char *msg = ok ? "BGDONE\n" : "BGERR\n";
                    usb_serial_jtag_write_bytes((const uint8_t *)msg, strlen(msg), pdMS_TO_TICKS(50));
                }
                vTaskDelay(pdMS_TO_TICKS(1));
                continue;
            }

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
        vTaskDelay(pdMS_TO_TICKS(10));
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

    xTaskCreatePinnedToCore(cdc_rx_task, "cdc_rx", 4096, NULL, 0, NULL, 0);
    ESP_LOGI(TAG, "CDC ready");
}

void supermouse_cdc_send_tap(int index)
{
    char msg[24];
    int  len = snprintf(msg, sizeof(msg), "TAP %d\n", index);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(50));
}

void supermouse_cdc_send_volume(int value)
{
    if (value < 0) value = 0;
    if (value > 100) value = 100;

    char msg[24];
    int  len = snprintf(msg, sizeof(msg), "VOL %d\n", value);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(50));
}

void supermouse_cdc_send_mouse_move(int dx, int dy)
{
    if (dx < -127) dx = -127;
    if (dx > 127) dx = 127;
    if (dy < -127) dy = -127;
    if (dy > 127) dy = 127;

    char msg[32];
    int  len = snprintf(msg, sizeof(msg), "MOVE %d %d\n", dx, dy);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(20));
}

void supermouse_cdc_send_mouse_click(int button)
{
    if (button < 0) button = 0;
    if (button > 1) button = 1;

    char msg[24];
    int  len = snprintf(msg, sizeof(msg), "CLICK %d\n", button);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(20));
}

void supermouse_cdc_send_text(const char *text)
{
    if (!text || !text[0]) return;

    char msg[96];
    int len = snprintf(msg, sizeof(msg), "TEXT %.80s\n", text);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(50));
}

void supermouse_cdc_send_word(const char *word)
{
    if (!word || !word[0]) return;

    char msg[96];
    int len = snprintf(msg, sizeof(msg), "WORD %.80s\n", word);
    usb_serial_jtag_write_bytes((const uint8_t *)msg, len, pdMS_TO_TICKS(50));
}
