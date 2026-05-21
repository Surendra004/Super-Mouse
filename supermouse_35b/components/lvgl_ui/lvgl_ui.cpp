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
#include <string.h>
#include <stdio.h>
#include <stdint.h>
#include <time.h>

static const char *TAG = "supermouse_ui";

/* ── Screen dimensions ──────────────────────────────────────────── */
#define SCR_W   320
#define SCR_H   480

/* ── Colours ─────────────────────────────────────────────────────── */
#define CLR_BG       lv_color_make(15,  15,  20 )
#define CLR_CARD     lv_color_make(28,  28,  38 )
#define CLR_ACCENT   lv_color_make(255, 59,  48 )
#define CLR_TEXT     lv_color_white()
#define CLR_SUB      lv_color_make(150, 150, 160)
#define CLR_TRACK_BG lv_color_make(50,  50,  65 )

/* ── App definitions — abbr is what shows inside the icon ───────── */
typedef struct {
    const char *name;
    const char *abbr;   /* short text shown inside coloured tile */
    uint32_t    color;
} app_def_t;

static const app_def_t APPS[] = {
    { "Chrome",   "G",   0x4285F4 },   /* Google blue   */
    { "Word",     "W",   0x2B579A },   /* Word blue     */
    { "Excel",    "X",   0x217346 },   /* Excel green   */
    { "PowerPnt", "P",   0xD04423 },   /* PPT orange    */
    { "VS Code",  "VS",  0x007ACC },   /* VSCode blue   */
    { "Settings", "S",   0x636366 },
};
#define APP_COUNT 6

/* ── LVGL handles ────────────────────────────────────────────────── */
static lv_obj_t *s_time_label    = NULL;
static lv_obj_t *s_date_label    = NULL;
static lv_obj_t *s_weather_label = NULL;
static lv_obj_t *s_song_label    = NULL;
static lv_obj_t *s_progress_bar  = NULL;
static lv_obj_t *s_play_label    = NULL;
static bool      s_playing        = false;
static uint32_t  s_last_pc_time_tick = 0;

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

/* Unused stubs required by CDC */
void lvgl_ui_set_app_name(const char *name)            { (void)name; }
void lvgl_ui_set_button(int i, const char *l, uint32_t c) { (void)i;(void)l;(void)c; }
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
    if (!bsp_pcf85063_get_time(&now)) {
        time_t raw = time(NULL);
        localtime_r(&raw, &now);
    }
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
    int idx = (int)(intptr_t)lv_obj_get_user_data(lv_event_get_target(e));
    ESP_LOGI(TAG, "TAP %s (%d)", APPS[idx].name, idx);
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

/* ── Helpers ─────────────────────────────────────────────────────── */
static lv_obj_t *make_card(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *c = lv_obj_create(parent);
    lv_obj_set_pos(c, x, y);
    lv_obj_set_size(c, w, h);
    lv_obj_set_style_bg_color(c, CLR_CARD, 0);
    lv_obj_set_style_bg_opa(c, LV_OPA_COVER, 0);
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
    lv_obj_t *clock_card = make_card(scr, 10, 32, SCR_W - 20, 115);

    s_time_label = lv_label_create(clock_card);
    lv_label_set_text(s_time_label, "00:00");
    lv_obj_set_style_text_color(s_time_label, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_time_label, &lv_font_montserrat_48, 0);
    lv_obj_align(s_time_label, LV_ALIGN_TOP_MID, 0, 8);

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
    lv_obj_t *music_card = make_card(scr, 10, 155, SCR_W - 20, 110);

    s_song_label = lv_label_create(music_card);
    lv_label_set_text(s_song_label, "No media");
    lv_obj_set_style_text_color(s_song_label, CLR_TEXT, 0);
    lv_obj_set_style_text_font(s_song_label, &lv_font_montserrat_16, 0);
    lv_label_set_long_mode(s_song_label, LV_LABEL_LONG_SCROLL_CIRCULAR);
    lv_obj_set_width(s_song_label, SCR_W - 60);
    lv_obj_align(s_song_label, LV_ALIGN_TOP_MID, 0, 10);

    s_progress_bar = lv_bar_create(music_card);
    lv_obj_set_size(s_progress_bar, SCR_W - 60, 4);
    lv_obj_align(s_progress_bar, LV_ALIGN_BOTTOM_MID, 0, -30);
    lv_obj_set_style_bg_color(s_progress_bar, CLR_TRACK_BG, 0);
    lv_obj_set_style_bg_color(s_progress_bar, CLR_ACCENT, LV_PART_INDICATOR);
    lv_obj_set_style_radius(s_progress_bar, 2, 0);
    lv_obj_set_style_radius(s_progress_bar, 2, LV_PART_INDICATOR);
    lv_bar_set_value(s_progress_bar, 0, LV_ANIM_OFF);

    lv_obj_t *prev_btn = make_ctrl_btn(music_card, LV_SYMBOL_PREV, CLR_ACCENT, prev_cb);
    lv_obj_set_size(prev_btn, 36, 36);
    lv_obj_align(prev_btn, LV_ALIGN_BOTTOM_MID, -60, -10);

    lv_obj_t *play_btn = make_ctrl_btn(music_card, LV_SYMBOL_PLAY, CLR_ACCENT, play_cb);
    lv_obj_set_size(play_btn, 44, 44);
    lv_obj_align(play_btn, LV_ALIGN_BOTTOM_MID, 0, -8);
    s_play_label = lv_obj_get_child(play_btn, 0);

    lv_obj_t *next_btn = make_ctrl_btn(music_card, LV_SYMBOL_NEXT, CLR_ACCENT, next_cb);
    lv_obj_set_size(next_btn, 36, 36);
    lv_obj_align(next_btn, LV_ALIGN_BOTTOM_MID, 60, -10);

    /* ── App grid (273 downward) ─────────────────────────────── */
    const int grid_y  = 273;
    const int icon_w  = 82;
    const int icon_h  = 66;   /* tile height without label */
    const int gap_x   = 16;
    const int gap_y   = 10;
    const int cols    = 3;
    const int start_x = (SCR_W - (cols * icon_w + (cols - 1) * gap_x)) / 2;

    for (int i = 0; i < APP_COUNT; i++) {
        int col = i % cols;
        int row = i / cols;
        int x   = start_x + col * (icon_w + gap_x);
        int y   = grid_y  + row * (icon_h + 20 + gap_y);

        lv_color_t c = lv_color_make(
            (APPS[i].color >> 16) & 0xFF,
            (APPS[i].color >>  8) & 0xFF,
            (APPS[i].color      ) & 0xFF);

        /* Coloured tile */
        lv_obj_t *tile = lv_obj_create(scr);
        lv_obj_set_pos(tile, x, y);
        lv_obj_set_size(tile, icon_w, icon_h);
        lv_obj_set_style_bg_color(tile, c, 0);
        lv_obj_set_style_radius(tile, 16, 0);
        lv_obj_set_style_border_width(tile, 0, 0);
        lv_obj_set_style_shadow_width(tile, 8, 0);
        lv_obj_set_style_shadow_color(tile, c, 0);
        lv_obj_set_style_shadow_opa(tile, LV_OPA_40, 0);
        lv_obj_set_style_pad_all(tile, 0, 0);
        lv_obj_clear_flag(tile, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_user_data(tile, (void *)(intptr_t)i);
        lv_obj_add_event_cb(tile, app_btn_cb, LV_EVENT_CLICKED, NULL);

        /* Abbreviation label inside tile */
        lv_obj_t *abbr = lv_label_create(tile);
        lv_label_set_text(abbr, APPS[i].abbr);
        lv_obj_set_style_text_color(abbr, lv_color_white(), 0);
        lv_obj_set_style_text_font(abbr, &lv_font_montserrat_20, 0);
        lv_obj_center(abbr);

        /* App name below tile */
        lv_obj_t *name = lv_label_create(scr);
        lv_label_set_text(name, APPS[i].name);
        lv_obj_set_style_text_color(name, CLR_TEXT, 0);
        lv_obj_set_style_text_font(name, &lv_font_montserrat_12, 0);
        lv_obj_set_pos(name, x, y + icon_h + 2);
        lv_obj_set_width(name, icon_w);
        lv_obj_set_style_text_align(name, LV_TEXT_ALIGN_CENTER, 0);
    }

    lv_timer_create(clock_timer_cb, 30000, NULL);
    clock_timer_cb(NULL);

    ESP_LOGI(TAG, "UI ready");
}
