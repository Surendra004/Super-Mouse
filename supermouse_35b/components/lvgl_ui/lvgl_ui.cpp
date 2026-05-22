/*
 * lvgl_ui.cpp — Super Mouse Full UI
 * Layout:
 *   [ Status bar                  ]   28 px
 *   [ Clock + Date + Weather card ]  115 px
 *   [ Music controls card         ]  110 px
 *   [ App icon grid  2x3          ]  rest
 */

#include "lvgl_ui.h"
#include "supermouse_cdc.h"
#include "bsp_pcf85063.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_heap_caps.h"
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <time.h>

static const char *TAG = "supermouse_ui";
LV_IMG_DECLARE(supermouse_bg);

/* ── Screen dimensions ──────────────────────────────────────────── */
#define SCR_W   320
#define SCR_H   480
#define BG_RAW_SIZE (SCR_W * SCR_H * 2)
#define BG_FLASH_LABEL "storage"
#define BG_FLASH_MAGIC 0x534D4247u
#define BG_HEADER_SIZE 16

/* ── Colours ─────────────────────────────────────────────────────── */
#define CLR_BG       lv_color_make(15,  15,  20 )
#define CLR_CARD     lv_color_make(28,  28,  38 )
#define CLR_ACCENT   lv_color_make(255, 59,  48 )
#define CLR_TEXT     lv_color_white()
#define CLR_SUB      lv_color_make(150, 150, 160)
#define CLR_TRACK_BG lv_color_make(50,  50,  65 )

/* ── App definitions — abbr is what shows inside the icon ───────── */
typedef struct {
    char     name[18];
    char     abbr[5];   /* short text shown inside coloured tile */
    uint32_t color;
} app_def_t;

static app_def_t s_apps[] = {
    { "Chrome",   "G",  0x4285F4 },
    { "Word",     "W",  0x2B579A },
    { "Excel",    "X",  0x217346 },
    { "PowerPnt", "P",  0xD04423 },
    { "VS Code",  "VS", 0x007ACC },
    { "Settings", "S",  0x636366 },
};
#define APP_COUNT 6

/* ── LVGL handles ────────────────────────────────────────────────── */
static lv_obj_t *s_time_label    = NULL;
static lv_obj_t *s_date_label    = NULL;
static lv_obj_t *s_weather_label = NULL;
static lv_obj_t *s_song_label    = NULL;
static lv_obj_t *s_progress_bar  = NULL;
static lv_obj_t *s_play_label    = NULL;
static lv_obj_t *s_volume_panel  = NULL;
static lv_obj_t *s_volume_arc    = NULL;
static lv_obj_t *s_volume_label  = NULL;
static lv_obj_t *s_touch_panel   = NULL;
static lv_obj_t *s_touch_app     = NULL;
static lv_obj_t *s_touch_speed_label = NULL;
static lv_obj_t *s_bg_img        = NULL;
static lv_img_dsc_t s_bg_dsc     = {};
static uint8_t   *s_bg_buf       = NULL;
static const esp_partition_t *s_bg_partition = NULL;
static size_t     s_bg_upload_remaining = 0;
static size_t     s_bg_upload_written = 0;
static bool       s_bg_upload_active = false;
static lv_obj_t *s_app_tiles[APP_COUNT] = {};
static lv_obj_t *s_app_abbrs[APP_COUNT] = {};
static lv_obj_t *s_app_names[APP_COUNT] = {};
static lv_obj_t *s_widget2_title = NULL;
static lv_obj_t *s_widget2_line1 = NULL;
static lv_obj_t *s_widget2_line2 = NULL;
static bool      s_playing        = false;
static bool      s_volume_open    = false;
static bool      s_edge_tracking  = false;
static bool      s_touch_open     = false;
static bool      s_touch_tracking = false;
static bool      s_touchpad_active = false;
static bool      s_touchpad_moved = false;
static bool      s_touchpad_long_press = false;
static int       s_volume         = 70;
static int       s_touch_speed    = 4;
static lv_coord_t s_swipe_start_x = 0;
static lv_coord_t s_swipe_start_panel_x = 0;
static lv_coord_t s_touch_last_x  = 0;
static lv_coord_t s_touch_last_y  = 0;
static int        s_mouse_smooth_dx = 0;
static int        s_mouse_smooth_dy = 0;
static uint32_t  s_last_pc_time_tick = 0;
static uint32_t  s_last_volume_send_tick = 0;
static uint32_t  s_last_mouse_send_tick = 0;

#define VOLUME_PANEL_W 118
#define VOLUME_PANEL_HIDDEN_X (-VOLUME_PANEL_W - 20)
#define TOUCH_PANEL_W 180
#define TOUCH_PANEL_HIDDEN_X (SCR_W + 20)
#define TOUCHPAD_APP_INDEX 5

/* ── PC-driven update functions (called from supermouse_cdc) ─────── */
void lvgl_ui_set_time(int hour, int min)
{
    if (!s_time_label) return;
    if (hour < 0 || hour > 23 || min < 0 || min > 59) return;

    char buf[8];
    snprintf(buf, sizeof(buf), "%02d:%02d", hour, min);
    lv_label_set_text(s_time_label, buf);
    s_last_pc_time_tick = lv_tick_get();
}

void lvgl_ui_set_weather(const char *temp, const char *condition)
{
    if (!s_weather_label) return;
    char buf[32];
    snprintf(buf, sizeof(buf), "%s  %s", temp, condition);
    lv_label_set_text(s_weather_label, buf);
}

void lvgl_ui_set_song(const char *title)
{
    if (!s_song_label) return;
    lv_label_set_text(s_song_label, title);
}

static lv_color_t color_from_u32(uint32_t color)
{
    return lv_color_make((color >> 16) & 0xFF, (color >> 8) & 0xFF, color & 0xFF);
}

static const esp_partition_t *background_partition_get(void)
{
    if (s_bg_partition) return s_bg_partition;
    s_bg_partition = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, BG_FLASH_LABEL);
    if (!s_bg_partition) {
        ESP_LOGW(TAG, "Background partition not found");
    }
    return s_bg_partition;
}

static bool background_apply_from_file(void)
{
    return false;

    const esp_partition_t *part = background_partition_get();
    if (!part || !s_bg_img) return false;

    uint32_t header[4] = {};
    if (esp_partition_read(part, 0, header, sizeof(header)) != ESP_OK) {
        return false;
    }
    if (header[0] != BG_FLASH_MAGIC || header[1] != SCR_W ||
        header[2] != SCR_H || header[3] != BG_RAW_SIZE) {
        return false;
    }

    if (!s_bg_buf) {
        s_bg_buf = (uint8_t *)heap_caps_malloc(BG_RAW_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
        if (!s_bg_buf) {
            s_bg_buf = (uint8_t *)malloc(BG_RAW_SIZE);
        }
    }
    if (!s_bg_buf) {
        ESP_LOGW(TAG, "No memory for background image");
        return false;
    }

    if (esp_partition_read(part, BG_HEADER_SIZE, s_bg_buf, BG_RAW_SIZE) != ESP_OK) {
        ESP_LOGW(TAG, "Background read failed");
        return false;
    }

    s_bg_dsc.header.always_zero = 0;
    s_bg_dsc.header.w = SCR_W;
    s_bg_dsc.header.h = SCR_H;
    s_bg_dsc.header.cf = LV_IMG_CF_TRUE_COLOR;
    s_bg_dsc.data_size = BG_RAW_SIZE;
    s_bg_dsc.data = s_bg_buf;

    lv_img_set_src(s_bg_img, &s_bg_dsc);
    lv_obj_clear_flag(s_bg_img, LV_OBJ_FLAG_HIDDEN);
    lv_obj_move_background(s_bg_img);
    return true;
}

static void background_apply_async(void *user_data)
{
    (void)user_data;
    background_apply_from_file();
}

bool lvgl_ui_background_upload_begin(size_t size)
{
    const esp_partition_t *part = background_partition_get();
    if (size != BG_RAW_SIZE || !part || part->size < BG_HEADER_SIZE + BG_RAW_SIZE) {
        return false;
    }

    size_t erase_size = BG_HEADER_SIZE + BG_RAW_SIZE;
    erase_size = (erase_size + 4095) & ~((size_t)4095);
    if (esp_partition_erase_range(part, 0, erase_size) != ESP_OK) {
        ESP_LOGW(TAG, "Background erase failed");
        return false;
    }

    s_bg_upload_remaining = size;
    s_bg_upload_written = 0;
    s_bg_upload_active = true;
    return true;
}

bool lvgl_ui_background_upload_write(const uint8_t *data, size_t len)
{
    const esp_partition_t *part = background_partition_get();
    if (!part || !data || len > s_bg_upload_remaining) {
        return false;
    }

    if (esp_partition_write(part, BG_HEADER_SIZE + s_bg_upload_written, data, len) != ESP_OK) {
        ESP_LOGW(TAG, "Background write failed");
        return false;
    }

    s_bg_upload_written += len;
    s_bg_upload_remaining -= len;
    return true;
}

bool lvgl_ui_background_upload_end(void)
{
    if (s_bg_upload_remaining != 0) {
        s_bg_upload_active = false;
        return false;
    }

    const esp_partition_t *part = background_partition_get();
    if (!part) return false;

    uint32_t header[4] = { BG_FLASH_MAGIC, SCR_W, SCR_H, BG_RAW_SIZE };
    if (esp_partition_write(part, 0, header, sizeof(header)) != ESP_OK) {
        ESP_LOGW(TAG, "Background header write failed");
        s_bg_upload_active = false;
        return false;
    }

    s_bg_upload_active = false;
    lv_async_call(background_apply_async, NULL);
    return true;
}

bool lvgl_ui_background_upload_active(void)
{
    return s_bg_upload_active;
}

static void refresh_app_slot(int i)
{
    if (i < 0 || i >= APP_COUNT || !s_app_tiles[i]) return;

    lv_color_t c = color_from_u32(s_apps[i].color);
    lv_obj_set_style_bg_color(s_app_tiles[i], c, 0);
    lv_obj_set_style_bg_color(s_app_tiles[i], lv_color_darken(c, 40), LV_STATE_PRESSED);
    lv_obj_set_style_shadow_color(s_app_tiles[i], c, 0);
    lv_label_set_text(s_app_abbrs[i], s_apps[i].abbr);
    lv_label_set_text(s_app_names[i], s_apps[i].name);
}

void lvgl_ui_set_app_slot(int i, const char *name, const char *abbr, uint32_t color)
{
    if (i < 0 || i >= APP_COUNT) return;
    if (name && *name) {
        snprintf(s_apps[i].name, sizeof(s_apps[i].name), "%s", name);
    }
    if (abbr && *abbr) {
        snprintf(s_apps[i].abbr, sizeof(s_apps[i].abbr), "%s", abbr);
    }
    s_apps[i].color = color;
    refresh_app_slot(i);
}

void lvgl_ui_set_widget(int i, const char *title, const char *line1, const char *line2)
{
    if (i != 1) return;
    if (s_widget2_title && title) lv_label_set_text(s_widget2_title, title);
    if (s_widget2_line1 && line1) lv_label_set_text(s_widget2_line1, line1);
    if (s_widget2_line2 && line2) lv_label_set_text(s_widget2_line2, line2);
}

void lvgl_ui_set_background(uint32_t color)
{
    lv_obj_set_style_bg_color(lv_scr_act(), color_from_u32(color), 0);
}

/* Legacy stubs required by CDC */
void lvgl_ui_set_app_name(const char *name)            { (void)name; }
void lvgl_ui_set_button(int i, const char *l, uint32_t c) { lvgl_ui_set_app_slot(i, l, l, c); }
void lvgl_ui_clear_buttons(void)                       {}
void lvgl_ui_render_dynamic(void)                      {}
void lvgl_ui_on_tap(int index)                         { supermouse_cdc_send_tap(index); }

/* ── RTC clock refresh (every 30 s) ─────────────────────────────── */
static const char *WDAYS[]  = {"Sun","Mon","Tue","Wed","Thu","Fri","Sat"};
static const char *MONTHS[] = {"Jan","Feb","Mar","Apr","May","Jun",
                                "Jul","Aug","Sep","Oct","Nov","Dec"};

static void clock_timer_cb(lv_timer_t *t)
{
    (void)t;
    struct tm now = {};
    time_t raw = time(NULL);
    localtime_r(&raw, &now);
    char buf[16];
    if (s_last_pc_time_tick == 0 || lv_tick_elaps(s_last_pc_time_tick) > 70000) {
        snprintf(buf, sizeof(buf), "%02d:%02d", now.tm_hour, now.tm_min);
        lv_label_set_text(s_time_label, buf);
    }

    snprintf(buf, sizeof(buf), "%s, %s %d",
             WDAYS[now.tm_wday], MONTHS[now.tm_mon], now.tm_mday);
    lv_label_set_text(s_date_label, buf);
}

/* ── App tap ─────────────────────────────────────────────────────── */
static void app_btn_cb(lv_event_t *e)
{
    if (lvgl_ui_background_upload_active()) return;
    int idx = (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
    ESP_LOGI(TAG, "TAP %s (%d)", s_apps[idx].name, idx);
    if (idx == TOUCHPAD_APP_INDEX && s_touch_app) {
        lv_obj_clear_flag(s_touch_app, LV_OBJ_FLAG_HIDDEN);
        lv_obj_move_foreground(s_touch_app);
        return;
    }
    supermouse_cdc_send_tap(idx);
}

/* ── Music callbacks ─────────────────────────────────────────────── */
static void play_cb(lv_event_t *e)
{
    (void)e;
    s_playing = !s_playing;
    lv_label_set_text(s_play_label, s_playing ? LV_SYMBOL_PAUSE : LV_SYMBOL_PLAY);
    supermouse_cdc_send_tap(20);
}
static void prev_cb(lv_event_t *e) { (void)e; supermouse_cdc_send_tap(21); }
static void next_cb(lv_event_t *e) { (void)e; supermouse_cdc_send_tap(22); }

static void volume_panel_anim_x(void *obj, int32_t x)
{
    lv_obj_set_x((lv_obj_t *)obj, x);
}

static void volume_panel_set_open(bool open)
{
    if (!s_volume_panel) return;
    int target_x = open ? 0 : VOLUME_PANEL_HIDDEN_X;
    if (s_volume_open == open && lv_obj_get_x(s_volume_panel) == target_x) return;
    s_volume_open = open;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_volume_panel);
    lv_anim_set_exec_cb(&a, volume_panel_anim_x);
    lv_anim_set_values(&a, lv_obj_get_x(s_volume_panel), target_x);
    lv_anim_set_time(&a, 180);
    lv_anim_set_path_cb(&a, lv_anim_path_ease_out);
    lv_anim_start(&a);
}

static void volume_set_value(int value, bool send)
{
    if (value < 0) value = 0;
    if (value > 100) value = 100;
    if (value == s_volume && send) return;

    s_volume = value;
    if (s_volume_arc) {
        lv_arc_set_value(s_volume_arc, s_volume);
    }

    char buf[8];
    snprintf(buf, sizeof(buf), "%d", s_volume);
    if (s_volume_label) {
        lv_label_set_text(s_volume_label, buf);
    }

    if (!send) return;

    uint32_t now = lv_tick_get();
    if (s_last_volume_send_tick == 0 || lv_tick_elaps(s_last_volume_send_tick) > 120) {
        supermouse_cdc_send_volume(s_volume);
        s_last_volume_send_tick = now;
    }
}

static void volume_touch_cb(lv_event_t *e)
{
    if (lvgl_ui_background_upload_active()) return;
    lv_event_code_t code = lv_event_get_code(e);
    if (code != LV_EVENT_PRESSED && code != LV_EVENT_PRESSING && code != LV_EVENT_RELEASED) {
        return;
    }

    lv_obj_t *zone = lv_event_get_target(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    lv_area_t a;
    lv_obj_get_coords(zone, &a);
    int h = a.y2 - a.y1 + 1;
    int y = p.y - a.y1;
    if (y < 0) y = 0;
    if (y > h) y = h;

    int value = 100 - ((y * 100) / h);
    value = ((value + 1) / 2) * 2;
    volume_set_value(value, true);
}

static void mute_cb(lv_event_t *e)
{
    (void)e;
    supermouse_cdc_send_tap(32);
}

static void volume_edge_cb(lv_event_t *e)
{
    if (lvgl_ui_background_upload_active()) return;
    lv_event_code_t code = lv_event_get_code(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    if (code == LV_EVENT_PRESSED) {
        s_swipe_start_x = p.x;
        s_edge_tracking = (p.x < 22);
    } else if (code == LV_EVENT_PRESSING && s_edge_tracking) {
        if (p.x - s_swipe_start_x > 42) {
            volume_panel_set_open(true);
            s_edge_tracking = false;
        }
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        s_edge_tracking = false;
    }
}

static void volume_panel_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    if (code == LV_EVENT_PRESSED) {
        s_swipe_start_x = p.x;
        s_swipe_start_panel_x = lv_obj_get_x(s_volume_panel);
    } else if (code == LV_EVENT_PRESSING) {
        int delta = p.x - s_swipe_start_x;
        if (delta < 0) {
            int next_x = s_swipe_start_panel_x + delta;
            if (next_x < VOLUME_PANEL_HIDDEN_X) next_x = VOLUME_PANEL_HIDDEN_X;
            if (next_x > 0) next_x = 0;
            lv_obj_set_x(s_volume_panel, next_x);
        }
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        int x = lv_obj_get_x(s_volume_panel);
        if (x < -(VOLUME_PANEL_W / 3)) {
            volume_panel_set_open(false);
        } else {
            volume_panel_set_open(true);
        }
    }
}

/* ── Helpers ─────────────────────────────────────────────────────── */
static void touch_panel_anim_x(void *obj, int32_t x)
{
    lv_obj_set_x((lv_obj_t *)obj, x);
}

static void touch_panel_set_open(bool open)
{
    if (!s_touch_panel || s_touch_open == open) return;
    s_touch_open = open;

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, s_touch_panel);
    lv_anim_set_exec_cb(&a, touch_panel_anim_x);
    lv_anim_set_values(&a, lv_obj_get_x(s_touch_panel), open ? (SCR_W - TOUCH_PANEL_W) : TOUCH_PANEL_HIDDEN_X);
    lv_anim_set_time(&a, 160);
    lv_anim_start(&a);
}

static void touch_edge_cb(lv_event_t *e)
{
    if (lvgl_ui_background_upload_active()) return;
    lv_event_code_t code = lv_event_get_code(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    if (code == LV_EVENT_PRESSED) {
        s_swipe_start_x = p.x;
        s_touch_tracking = (p.x > SCR_W - 22);
    } else if (code == LV_EVENT_PRESSING && s_touch_tracking) {
        if (s_swipe_start_x - p.x > 42) {
            touch_panel_set_open(true);
            s_touch_tracking = false;
        }
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        s_touch_tracking = false;
    }
}

static void touch_panel_cb(lv_event_t *e)
{
    lv_event_code_t code = lv_event_get_code(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    if (code == LV_EVENT_PRESSED) {
        s_swipe_start_x = p.x;
    } else if (code == LV_EVENT_PRESSING) {
        if (p.x - s_swipe_start_x > 44) {
            touch_panel_set_open(false);
        }
    }
}

static void mouse_touchpad_cb(lv_event_t *e)
{
    if (lvgl_ui_background_upload_active()) return;
    lv_event_code_t code = lv_event_get_code(e);
    lv_point_t p;
    lv_indev_get_point(lv_indev_get_act(), &p);

    if (code == LV_EVENT_PRESSED) {
        s_touchpad_active = true;
        s_touchpad_moved = false;
        s_touchpad_long_press = false;
        s_touch_last_x = p.x;
        s_touch_last_y = p.y;
        s_mouse_smooth_dx = 0;
        s_mouse_smooth_dy = 0;
    } else if (code == LV_EVENT_PRESSING && s_touchpad_active) {
        int dx = (int)(p.x - s_touch_last_x);
        int dy = (int)(p.y - s_touch_last_y);
        uint32_t now = lv_tick_get();
        if ((dx <= -1 || dx >= 1 || dy <= -1 || dy >= 1) &&
            (s_last_mouse_send_tick == 0 || lv_tick_elaps(s_last_mouse_send_tick) > 6)) {
            if (dx <= -3 || dx >= 3 || dy <= -3 || dy >= 3) {
                s_touchpad_moved = true;
            }

            int ax = dx < 0 ? -dx : dx;
            int ay = dy < 0 ? -dy : dy;
            int gain_x = ax >= 10 ? s_touch_speed + 2 : (ax >= 4 ? s_touch_speed + 1 : s_touch_speed);
            int gain_y = ay >= 10 ? s_touch_speed + 2 : (ay >= 4 ? s_touch_speed + 1 : s_touch_speed);
            int target_dx = dx * gain_x;
            int target_dy = dy * gain_y;

            s_mouse_smooth_dx = (s_mouse_smooth_dx + target_dx * 4) / 5;
            s_mouse_smooth_dy = (s_mouse_smooth_dy + target_dy * 4) / 5;

            supermouse_cdc_send_mouse_move(s_mouse_smooth_dx, s_mouse_smooth_dy);
            s_touch_last_x = p.x;
            s_touch_last_y = p.y;
            s_last_mouse_send_tick = now;
        }
    } else if (code == LV_EVENT_LONG_PRESSED && s_touchpad_active && !s_touchpad_moved) {
        s_touchpad_long_press = true;
        supermouse_cdc_send_mouse_click(1);
    } else if (code == LV_EVENT_RELEASED || code == LV_EVENT_PRESS_LOST) {
        s_touchpad_active = false;
    } else if (code == LV_EVENT_CLICKED) {
        if (!s_touchpad_moved && !s_touchpad_long_press) {
            supermouse_cdc_send_mouse_click(0);
        }
    }
}

static void touch_speed_refresh(void)
{
    if (!s_touch_speed_label) return;
    char buf[16];
    snprintf(buf, sizeof(buf), "Speed %d", s_touch_speed);
    lv_label_set_text(s_touch_speed_label, buf);
}

static void touch_speed_down_cb(lv_event_t *e)
{
    (void)e;
    if (s_touch_speed > 2) s_touch_speed--;
    touch_speed_refresh();
}

static void touch_speed_up_cb(lv_event_t *e)
{
    (void)e;
    if (s_touch_speed < 7) s_touch_speed++;
    touch_speed_refresh();
}

static void touch_app_close_cb(lv_event_t *e)
{
    (void)e;
    if (s_touch_app) {
        lv_obj_add_flag(s_touch_app, LV_OBJ_FLAG_HIDDEN);
    }
}

static void mouse_left_click_cb(lv_event_t *e)
{
    (void)e;
    supermouse_cdc_send_mouse_click(0);
}

static void mouse_right_click_cb(lv_event_t *e)
{
    (void)e;
    supermouse_cdc_send_mouse_click(1);
}

static lv_obj_t *make_card(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *c = lv_obj_create(parent);
    lv_obj_set_pos(c, x, y);
    lv_obj_set_size(c, w, h);
    lv_obj_set_style_bg_color(c, CLR_CARD, 0);
    lv_obj_set_style_bg_opa(c, 220, 0);
    lv_obj_set_style_radius(c, 20, 0);
    lv_obj_set_style_border_width(c, 0, 0);
    lv_obj_set_style_pad_all(c, 0, 0);
    lv_obj_clear_flag(c, LV_OBJ_FLAG_SCROLLABLE);
    return c;
}

static lv_obj_t *make_ctrl_btn(lv_obj_t *parent, const char *sym,
                                lv_color_t col, lv_event_cb_t cb)
{
    lv_obj_t *btn = lv_btn_create(parent);
    lv_obj_set_style_bg_color(btn, col, 0);
    lv_obj_set_style_bg_color(btn, lv_color_darken(col, 50), LV_STATE_PRESSED);
    lv_obj_set_style_radius(btn, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_border_width(btn, 0, 0);
    lv_obj_set_style_shadow_width(btn, 10, 0);
    lv_obj_set_style_shadow_color(btn, col, 0);
    lv_obj_set_style_shadow_opa(btn, LV_OPA_30, 0);
    if (cb) lv_obj_add_event_cb(btn, cb, LV_EVENT_CLICKED, NULL);
    lv_obj_t *lbl = lv_label_create(btn);
    lv_label_set_text(lbl, sym);
    lv_obj_set_style_text_color(lbl, lv_color_white(), 0);
    lv_obj_set_style_text_font(lbl, &lv_font_montserrat_16, 0);
    lv_obj_center(lbl);
    return btn;
}

/* ================================================================
 * BUILD UI
 * ================================================================ */
void lvgl_ui_init(void)
{
    lv_obj_t *scr = lv_scr_act();
    lv_obj_set_style_bg_color(scr, CLR_BG, 0);
    lv_obj_set_style_bg_opa(scr, LV_OPA_COVER, 0);
    lv_obj_clear_flag(scr, LV_OBJ_FLAG_SCROLLABLE);

    s_bg_img = lv_img_create(scr);
    lv_obj_set_pos(s_bg_img, 0, 0);
    lv_img_set_src(s_bg_img, &supermouse_bg);
    lv_obj_move_background(s_bg_img);

    /* ── Status bar ──────────────────────────────────────────── */
    lv_obj_t *sb = lv_obj_create(scr);
    lv_obj_set_pos(sb, 0, 0);
    lv_obj_set_size(sb, SCR_W, 28);
    lv_obj_set_style_bg_color(sb, CLR_BG, 0);
    lv_obj_set_style_border_width(sb, 0, 0);
    lv_obj_set_style_pad_all(sb, 0, 0);
    lv_obj_clear_flag(sb, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *sb_lbl = lv_label_create(sb);
    lv_label_set_text(sb_lbl, "Super Mouse  " LV_SYMBOL_WIFI);
    lv_obj_set_style_text_color(sb_lbl, CLR_SUB, 0);
    lv_obj_set_style_text_font(sb_lbl, &lv_font_montserrat_12, 0);
    lv_obj_align(sb_lbl, LV_ALIGN_CENTER, 0, 0);

    /* ── Clock card (32..147) ────────────────────────────────── */
    lv_obj_t *clock_card = make_card(scr, 10, 32, 145, 132);

    s_time_label = lv_label_create(clock_card);
    lv_label_set_text(s_time_label, "00:00");
    lv_obj_set_style_text_color(s_time_label, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_time_label, &lv_font_montserrat_48, 0);
    lv_obj_align(s_time_label, LV_ALIGN_TOP_MID, 0, 10);

    s_date_label = lv_label_create(clock_card);
    lv_label_set_text(s_date_label, "---");
    lv_obj_set_style_text_color(s_date_label, CLR_SUB, 0);
    lv_obj_set_style_text_font(s_date_label, &lv_font_montserrat_12, 0);
    lv_obj_align(s_date_label, LV_ALIGN_BOTTOM_LEFT, 12, -8);

    s_weather_label = lv_label_create(clock_card);
    lv_label_set_text(s_weather_label, LV_SYMBOL_DOWNLOAD " --");
    lv_obj_set_style_text_color(s_weather_label, CLR_SUB, 0);
    lv_obj_set_style_text_font(s_weather_label, &lv_font_montserrat_12, 0);
    lv_obj_align(s_weather_label, LV_ALIGN_BOTTOM_RIGHT, -12, -8);

    /* ── Music card (155..265) ───────────────────────────────── */
    lv_obj_t *music_card = make_card(scr, 165, 32, 145, 132);

    s_widget2_title = lv_label_create(music_card);
    lv_label_set_text(s_widget2_title, "PC");
    lv_obj_set_style_text_color(s_widget2_title, CLR_SUB, 0);
    lv_obj_set_style_text_font(s_widget2_title, &lv_font_montserrat_12, 0);
    lv_obj_align(s_widget2_title, LV_ALIGN_TOP_LEFT, 12, 10);

    s_widget2_line1 = lv_label_create(music_card);
    lv_label_set_text(s_widget2_line1, "Photos");
    lv_obj_set_style_text_color(s_widget2_line1, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_widget2_line1, &lv_font_montserrat_16, 0);
    lv_obj_set_width(s_widget2_line1, 118);
    lv_label_set_long_mode(s_widget2_line1, LV_LABEL_LONG_DOT);
    lv_obj_align(s_widget2_line1, LV_ALIGN_TOP_LEFT, 12, 34);

    s_widget2_line2 = lv_label_create(music_card);
    lv_label_set_text(s_widget2_line2, "Files ready");
    lv_obj_set_style_text_color(s_widget2_line2, CLR_SUB, 0);
    lv_obj_set_style_text_font(s_widget2_line2, &lv_font_montserrat_12, 0);
    lv_obj_set_width(s_widget2_line2, 118);
    lv_label_set_long_mode(s_widget2_line2, LV_LABEL_LONG_DOT);
    lv_obj_align(s_widget2_line2, LV_ALIGN_TOP_LEFT, 12, 58);

    s_song_label = lv_label_create(music_card);
    lv_label_set_text(s_song_label, "No media");
    lv_obj_set_style_text_color(s_song_label, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_song_label, &lv_font_montserrat_16, 0);
    lv_label_set_long_mode(s_song_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(s_song_label, 118);
    lv_obj_align(s_song_label, LV_ALIGN_BOTTOM_MID, 0, -10);
    lv_obj_add_flag(s_song_label, LV_OBJ_FLAG_HIDDEN);

    s_progress_bar = lv_bar_create(music_card);
    lv_obj_set_size(s_progress_bar, 112, 4);
    lv_obj_align(s_progress_bar, LV_ALIGN_BOTTOM_MID, 0, -34);
    lv_obj_set_style_bg_color(s_progress_bar, CLR_TRACK_BG, 0);
    lv_obj_set_style_bg_color(s_progress_bar, CLR_ACCENT, LV_PART_INDICATOR);
    lv_obj_set_style_radius(s_progress_bar, 2, 0);
    lv_obj_set_style_radius(s_progress_bar, 2, LV_PART_INDICATOR);
    lv_bar_set_value(s_progress_bar, 0, LV_ANIM_OFF);
    lv_obj_add_flag(s_progress_bar, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *prev_btn = make_ctrl_btn(music_card, LV_SYMBOL_PREV, CLR_ACCENT, prev_cb);
    lv_obj_set_size(prev_btn, 28, 28);
    lv_obj_align(prev_btn, LV_ALIGN_BOTTOM_MID, -44, -46);
    lv_obj_add_flag(prev_btn, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *play_btn = make_ctrl_btn(music_card, LV_SYMBOL_PLAY, CLR_ACCENT, play_cb);
    lv_obj_set_size(play_btn, 32, 32);
    lv_obj_align(play_btn, LV_ALIGN_BOTTOM_MID, 0, -44);
    s_play_label = lv_obj_get_child(play_btn, 0);
    lv_obj_add_flag(play_btn, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *next_btn = make_ctrl_btn(music_card, LV_SYMBOL_NEXT, CLR_ACCENT, next_cb);
    lv_obj_set_size(next_btn, 28, 28);
    lv_obj_align(next_btn, LV_ALIGN_BOTTOM_MID, 44, -46);
    lv_obj_add_flag(next_btn, LV_OBJ_FLAG_HIDDEN);

    /* ── App grid (273 downward) ─────────────────────────────── */
    const int grid_y  = 176;
    const int icon_w  = 82;
    const int icon_h  = 74;   /* tile height without label */
    const int gap_x   = 16;
    const int gap_y   = 12;
    const int cols    = 3;
    const int start_x = (SCR_W - (cols * icon_w + (cols - 1) * gap_x)) / 2;

    for (int i = 0; i < APP_COUNT; i++) {
        int col = i % cols;
        int row = i / cols;
        int x   = start_x + col * (icon_w + gap_x);
        int y   = grid_y  + row * (icon_h + 18 + gap_y);

        lv_color_t c = lv_color_make(
            (s_apps[i].color >> 16) & 0xFF,
            (s_apps[i].color >>  8) & 0xFF,
            (s_apps[i].color      ) & 0xFF);

        /* Coloured tile */
        lv_obj_t *tile = lv_obj_create(scr);
        lv_obj_set_pos(tile, x, y);
        lv_obj_set_size(tile, icon_w, icon_h);
        lv_obj_set_style_bg_color(tile, c, 0);
        lv_obj_set_style_radius(tile, 14, 0);
        lv_obj_set_style_border_width(tile, 0, 0);
        lv_obj_set_style_shadow_width(tile, 8, 0);
        lv_obj_set_style_shadow_color(tile, c, 0);
        lv_obj_set_style_shadow_opa(tile, LV_OPA_40, 0);
        lv_obj_set_style_bg_color(tile, lv_color_darken(c, 40), LV_STATE_PRESSED);
        lv_obj_set_style_pad_all(tile, 0, 0);
        lv_obj_clear_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_add_flag(tile, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_user_data(tile, (void *)(intptr_t)i);
        lv_obj_add_event_cb(tile, app_btn_cb, LV_EVENT_CLICKED, NULL);
        s_app_tiles[i] = tile;

        /* Abbreviation label inside tile */
        lv_obj_t *abbr = lv_label_create(tile);
        lv_label_set_text(abbr, s_apps[i].abbr);
        lv_obj_set_style_text_color(abbr, lv_color_white(), 0);
        lv_obj_set_style_text_font(abbr, &lv_font_montserrat_16, 0);
        lv_obj_center(abbr);
        s_app_abbrs[i] = abbr;

        /* App name below tile */
        lv_obj_t *name = lv_label_create(scr);
        lv_label_set_text(name, s_apps[i].name);
        lv_obj_set_style_text_color(name, CLR_TEXT, 0);
        lv_obj_set_style_text_font(name, &lv_font_montserrat_12, 0);
        lv_obj_set_pos(name, x, y + icon_h + 2);
        lv_obj_set_width(name, icon_w);
        lv_obj_set_style_text_align(name, LV_TEXT_ALIGN_CENTER, 0);
        s_app_names[i] = name;
        refresh_app_slot(i);
    }

    lv_obj_t *edge = lv_obj_create(scr);
    lv_obj_set_pos(edge, 0, 0);
    lv_obj_set_size(edge, 14, SCR_H);
    lv_obj_set_style_bg_opa(edge, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(edge, 0, 0);
    lv_obj_set_style_pad_all(edge, 0, 0);
    lv_obj_clear_flag(edge, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(edge, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(edge, volume_edge_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *touch_edge = lv_obj_create(scr);
    lv_obj_set_pos(touch_edge, SCR_W - 14, 0);
    lv_obj_set_size(touch_edge, 14, SCR_H);
    lv_obj_set_style_bg_opa(touch_edge, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(touch_edge, 0, 0);
    lv_obj_set_style_pad_all(touch_edge, 0, 0);
    lv_obj_clear_flag(touch_edge, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(touch_edge, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(touch_edge, touch_edge_cb, LV_EVENT_ALL, NULL);

    s_volume_panel = make_card(scr, VOLUME_PANEL_HIDDEN_X, 118, VOLUME_PANEL_W, 290);
    lv_obj_set_style_radius(s_volume_panel, 18, 0);
    lv_obj_add_flag(s_volume_panel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_volume_panel, volume_panel_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *vol_title = lv_label_create(s_volume_panel);
    lv_label_set_text(vol_title, "VOL");
    lv_obj_set_style_text_color(vol_title, CLR_TEXT, 0);
    lv_obj_set_style_text_font(vol_title, &lv_font_montserrat_16, 0);
    lv_obj_align(vol_title, LV_ALIGN_TOP_MID, 0, 12);

    s_volume_arc = lv_arc_create(s_volume_panel);
    lv_obj_set_size(s_volume_arc, 108, 108);
    lv_obj_align(s_volume_arc, LV_ALIGN_CENTER, 0, -8);
    lv_arc_set_rotation(s_volume_arc, 0);
    lv_arc_set_bg_angles(s_volume_arc, 270, 90);
    lv_arc_set_mode(s_volume_arc, LV_ARC_MODE_REVERSE);
    lv_arc_set_range(s_volume_arc, 0, 100);
    lv_arc_set_value(s_volume_arc, s_volume);
    lv_obj_set_style_arc_width(s_volume_arc, 10, LV_PART_MAIN);
    lv_obj_set_style_arc_width(s_volume_arc, 12, LV_PART_INDICATOR);
    lv_obj_set_style_arc_color(s_volume_arc, CLR_TRACK_BG, LV_PART_MAIN);
    lv_obj_set_style_arc_color(s_volume_arc, CLR_ACCENT, LV_PART_INDICATOR);
    lv_obj_set_style_bg_color(s_volume_arc, CLR_ACCENT, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(s_volume_arc, LV_OPA_COVER, LV_PART_KNOB);
    lv_obj_set_style_pad_all(s_volume_arc, 3, LV_PART_KNOB);
    lv_obj_clear_flag(s_volume_arc, LV_OBJ_FLAG_CLICKABLE);

    lv_obj_t *volume_touch = lv_obj_create(s_volume_panel);
    lv_obj_set_size(volume_touch, 90, 150);
    lv_obj_align(volume_touch, LV_ALIGN_CENTER, 0, -8);
    lv_obj_set_style_bg_opa(volume_touch, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(volume_touch, 0, 0);
    lv_obj_set_style_pad_all(volume_touch, 0, 0);
    lv_obj_clear_flag(volume_touch, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(volume_touch, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(volume_touch, volume_touch_cb, LV_EVENT_ALL, NULL);

    s_volume_label = lv_label_create(s_volume_panel);
    lv_label_set_text(s_volume_label, "70");
    lv_obj_set_style_text_color(s_volume_label, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_volume_label, &lv_font_montserrat_20, 0);
    lv_obj_align(s_volume_label, LV_ALIGN_CENTER, 0, -8);

    lv_obj_t *mute_btn = make_ctrl_btn(s_volume_panel, LV_SYMBOL_MUTE, CLR_ACCENT, mute_cb);
    lv_obj_set_size(mute_btn, 42, 34);
    lv_obj_align(mute_btn, LV_ALIGN_BOTTOM_MID, 0, -12);

    lv_obj_t *volume_close_grip = lv_obj_create(s_volume_panel);
    lv_obj_set_pos(volume_close_grip, VOLUME_PANEL_W - 28, 0);
    lv_obj_set_size(volume_close_grip, 28, 290);
    lv_obj_set_style_bg_opa(volume_close_grip, LV_OPA_TRANSP, 0);
    lv_obj_set_style_border_width(volume_close_grip, 0, 0);
    lv_obj_set_style_pad_all(volume_close_grip, 0, 0);
    lv_obj_clear_flag(volume_close_grip, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(volume_close_grip, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(volume_close_grip, volume_panel_cb, LV_EVENT_ALL, NULL);

    s_touch_panel = make_card(scr, TOUCH_PANEL_HIDDEN_X, 118, TOUCH_PANEL_W, 290);
    lv_obj_set_style_radius(s_touch_panel, 18, 0);
    lv_obj_add_flag(s_touch_panel, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(s_touch_panel, touch_panel_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *touch_title = lv_label_create(s_touch_panel);
    lv_label_set_text(touch_title, "TOUCH");
    lv_obj_set_style_text_color(touch_title, CLR_TEXT, 0);
    lv_obj_set_style_text_font(touch_title, &lv_font_montserrat_16, 0);
    lv_obj_align(touch_title, LV_ALIGN_TOP_MID, 0, 12);

    lv_obj_t *touch_hint = lv_label_create(s_touch_panel);
    lv_label_set_text(touch_hint, "Swipe right to hide");
    lv_obj_set_style_text_color(touch_hint, CLR_SUB, 0);
    lv_obj_set_style_text_font(touch_hint, &lv_font_montserrat_12, 0);
    lv_obj_align(touch_hint, LV_ALIGN_TOP_MID, 0, 34);

    lv_obj_t *pad = lv_obj_create(s_touch_panel);
    lv_obj_set_size(pad, 148, 152);
    lv_obj_align(pad, LV_ALIGN_TOP_MID, 0, 60);
    lv_obj_set_style_bg_color(pad, lv_color_make(18, 18, 28), 0);
    lv_obj_set_style_bg_color(pad, lv_color_make(30, 30, 44), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(pad, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(pad, 14, 0);
    lv_obj_set_style_border_width(pad, 1, 0);
    lv_obj_set_style_border_color(pad, CLR_TRACK_BG, 0);
    lv_obj_set_style_pad_all(pad, 0, 0);
    lv_obj_clear_flag(pad, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(pad, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(pad, mouse_touchpad_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *pad_lbl = lv_label_create(pad);
    lv_label_set_text(pad_lbl, "Move");
    lv_obj_set_style_text_color(pad_lbl, CLR_SUB, 0);
    lv_obj_set_style_text_font(pad_lbl, &lv_font_montserrat_12, 0);
    lv_obj_center(pad_lbl);

    lv_obj_t *left_btn = make_ctrl_btn(s_touch_panel, "L", CLR_ACCENT, mouse_left_click_cb);
    lv_obj_set_size(left_btn, 62, 38);
    lv_obj_align(left_btn, LV_ALIGN_BOTTOM_LEFT, 22, -16);

    lv_obj_t *right_btn = make_ctrl_btn(s_touch_panel, "R", CLR_ACCENT, mouse_right_click_cb);
    lv_obj_set_size(right_btn, 62, 38);
    lv_obj_align(right_btn, LV_ALIGN_BOTTOM_RIGHT, -22, -16);

    s_touch_app = lv_obj_create(scr);
    lv_obj_set_pos(s_touch_app, 0, 0);
    lv_obj_set_size(s_touch_app, SCR_W, SCR_H);
    lv_obj_set_style_bg_color(s_touch_app, CLR_BG, 0);
    lv_obj_set_style_bg_opa(s_touch_app, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(s_touch_app, 0, 0);
    lv_obj_set_style_pad_all(s_touch_app, 0, 0);
    lv_obj_clear_flag(s_touch_app, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(s_touch_app, LV_OBJ_FLAG_HIDDEN);

    lv_obj_t *touch_header = lv_obj_create(s_touch_app);
    lv_obj_set_pos(touch_header, 0, 0);
    lv_obj_set_size(touch_header, SCR_W, 54);
    lv_obj_set_style_bg_color(touch_header, CLR_BG, 0);
    lv_obj_set_style_border_width(touch_header, 0, 0);
    lv_obj_set_style_pad_all(touch_header, 0, 0);
    lv_obj_clear_flag(touch_header, LV_OBJ_FLAG_SCROLLABLE);

    lv_obj_t *back_btn = make_ctrl_btn(touch_header, LV_SYMBOL_LEFT, CLR_ACCENT, touch_app_close_cb);
    lv_obj_set_size(back_btn, 42, 34);
    lv_obj_align(back_btn, LV_ALIGN_LEFT_MID, 10, 0);

    lv_obj_t *touch_app_title = lv_label_create(touch_header);
    lv_label_set_text(touch_app_title, "Touchpad");
    lv_obj_set_style_text_color(touch_app_title, CLR_TEXT, 0);
    lv_obj_set_style_text_font(touch_app_title, &lv_font_montserrat_20, 0);
    lv_obj_align(touch_app_title, LV_ALIGN_CENTER, 0, 0);

    lv_obj_t *speed_down_btn = make_ctrl_btn(touch_header, "-", CLR_ACCENT, touch_speed_down_cb);
    lv_obj_set_size(speed_down_btn, 34, 30);
    lv_obj_align(speed_down_btn, LV_ALIGN_RIGHT_MID, -98, 0);

    s_touch_speed_label = lv_label_create(touch_header);
    lv_obj_set_style_text_color(s_touch_speed_label, CLR_SUB, 0);
    lv_obj_set_style_text_font(s_touch_speed_label, &lv_font_montserrat_12, 0);
    lv_obj_align(s_touch_speed_label, LV_ALIGN_RIGHT_MID, -42, 0);
    touch_speed_refresh();

    lv_obj_t *speed_up_btn = make_ctrl_btn(touch_header, "+", CLR_ACCENT, touch_speed_up_cb);
    lv_obj_set_size(speed_up_btn, 34, 30);
    lv_obj_align(speed_up_btn, LV_ALIGN_RIGHT_MID, -4, 0);

    lv_obj_t *touch_pad = lv_obj_create(s_touch_app);
    lv_obj_set_pos(touch_pad, 0, 54);
    lv_obj_set_size(touch_pad, SCR_W, SCR_H - 54);
    lv_obj_set_style_bg_color(touch_pad, lv_color_make(16, 16, 26), 0);
    lv_obj_set_style_bg_color(touch_pad, lv_color_make(28, 28, 44), LV_STATE_PRESSED);
    lv_obj_set_style_radius(touch_pad, 0, 0);
    lv_obj_set_style_border_width(touch_pad, 0, 0);
    lv_obj_set_style_pad_all(touch_pad, 0, 0);
    lv_obj_clear_flag(touch_pad, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_flag(touch_pad, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(touch_pad, mouse_touchpad_cb, LV_EVENT_ALL, NULL);

    lv_obj_t *touch_pad_lbl = lv_label_create(touch_pad);
    lv_label_set_text(touch_pad_lbl, "Drag anywhere\nTap to click\nHold for right click");
    lv_obj_set_style_text_color(touch_pad_lbl, CLR_SUB, 0);
    lv_obj_set_style_text_font(touch_pad_lbl, &lv_font_montserrat_16, 0);
    lv_obj_set_style_text_align(touch_pad_lbl, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(touch_pad_lbl);

    lv_timer_create(clock_timer_cb, 30000, NULL);
    clock_timer_cb(NULL);

    ESP_LOGI(TAG, "UI ready");
}
