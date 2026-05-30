#ifndef __LVGL_UI_H__
#define __LVGL_UI_H__

#include <stdio.h>
#include "lvgl.h"

#ifdef __cplusplus
extern "C" {
#endif

void lvgl_ui_init(void);

/* Called from supermouse_cdc when PC sends TIME / WEATHER / SONG */
void lvgl_ui_set_time(int hour, int min);
void lvgl_ui_set_weather(const char *temp, const char *condition);
void lvgl_ui_set_song(const char *title);
void lvgl_ui_set_app_slot(int i, const char *name, const char *abbr, uint32_t color);
void lvgl_ui_set_widget(int i, const char *title, const char *line1, const char *line2);
void lvgl_ui_set_background(uint32_t color);
bool lvgl_ui_background_upload_begin(size_t size);
bool lvgl_ui_background_upload_write(const uint8_t *data, size_t len);
bool lvgl_ui_background_upload_end(void);
bool lvgl_ui_background_upload_active(void);
void lvgl_ui_set_app_name(const char *name);
void lvgl_ui_set_button(int i, const char *label, uint32_t color);
void lvgl_ui_clear_buttons(void);
void lvgl_ui_render_dynamic(void);
void lvgl_ui_on_tap(int index);

#ifdef __cplusplus
}
#endif

#endif
