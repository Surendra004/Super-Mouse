/**
 * lv_conf.h — LVGL config for Super Mouse Full UI
 */
#ifndef LV_CONF_H
#define LV_CONF_H

#include <stdint.h>

#define LV_COLOR_DEPTH          16
#define LV_MEM_CUSTOM           0
#define LV_MEM_SIZE             (64 * 1024U)
#define LV_TICK_CUSTOM          0

/* Fonts — all sizes used in the UI */
#define LV_FONT_MONTSERRAT_12   1
#define LV_FONT_MONTSERRAT_14   1
#define LV_FONT_MONTSERRAT_16   1
#define LV_FONT_MONTSERRAT_20   1
#define LV_FONT_MONTSERRAT_48   1
#define LV_FONT_DEFAULT         &lv_font_montserrat_16

/* Logging */
#define LV_USE_LOG              1
#define LV_LOG_LEVEL            LV_LOG_LEVEL_WARN
#define LV_LOG_PRINTF           1

/* Widgets */
#define LV_USE_BUTTON           1
#define LV_USE_LABEL            1
#define LV_USE_BAR              1
#define LV_USE_IMAGE            0
#define LV_USE_ARC              0
#define LV_USE_SLIDER           0
#define LV_USE_SPINNER          0
#define LV_USE_ROLLER           0
#define LV_USE_DROPDOWN         0
#define LV_USE_TABLE            0
#define LV_USE_CHART            0

/* OS */
#define LV_USE_OS               LV_OS_FREERTOS
#define LV_DRAW_BUF_ALIGN       4

#endif /* LV_CONF_H */
