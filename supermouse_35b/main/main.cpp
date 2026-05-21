#include <stdio.h>
#include "bsp_i2c.h"
#include "bsp_display.h"
#include "bsp_touch.h"
#include "lv_port.h"
#include "lvgl_ui.h"
#include "supermouse_cdc.h"
#include "esp_io_expander_tca9554.h"

#define LCD_H_RES       320
#define LCD_V_RES       480
#define LCD_BUFFER_SIZE (LCD_H_RES * LCD_V_RES)

esp_io_expander_handle_t expander_handle = NULL;
esp_lcd_panel_io_handle_t io_handle      = NULL;
esp_lcd_panel_handle_t    panel_handle   = NULL;

static void io_expander_init(i2c_master_bus_handle_t bus)
{
    ESP_ERROR_CHECK(esp_io_expander_new_i2c_tca9554(
        bus, ESP_IO_EXPANDER_I2C_TCA9554_ADDRESS_000, &expander_handle));
    ESP_ERROR_CHECK(esp_io_expander_set_dir(
        expander_handle, IO_EXPANDER_PIN_NUM_1, IO_EXPANDER_OUTPUT));
    ESP_ERROR_CHECK(esp_io_expander_set_level(
        expander_handle, IO_EXPANDER_PIN_NUM_1, 0));
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_ERROR_CHECK(esp_io_expander_set_level(
        expander_handle, IO_EXPANDER_PIN_NUM_1, 1));
    vTaskDelay(pdMS_TO_TICKS(200));
}

static void touchpad_read(lv_indev_drv_t *drv, lv_indev_data_t *data)
{
    static lv_coord_t last_x = 0, last_y = 0;
    touch_data_t td;
    bsp_touch_read();
    if (bsp_touch_get_coordinates(&td)) {
        last_x = td.coords[0].x;
        last_y = td.coords[0].y;
        data->state = LV_INDEV_STATE_PR;
    } else {
        data->state = LV_INDEV_STATE_REL;
    }
    data->point.x = last_x;
    data->point.y = last_y;
}

static void lv_port_setup(void)
{
    lvgl_port_cfg_t port_cfg = {};
    port_cfg.task_priority   = 4;
    port_cfg.task_stack      = 1024 * 5;
    port_cfg.task_affinity   = 1;
    port_cfg.task_max_sleep_ms = 500;
    port_cfg.timer_period_ms = 5;
    lvgl_port_init(&port_cfg);

    lvgl_port_display_cfg_t disp_cfg = {};
    disp_cfg.io_handle    = io_handle;
    disp_cfg.panel_handle = panel_handle;
    disp_cfg.buffer_size  = LCD_BUFFER_SIZE;
    disp_cfg.sw_rotate    = LV_DISP_ROT_NONE;
    disp_cfg.hres         = LCD_H_RES;
    disp_cfg.vres         = LCD_V_RES;
    disp_cfg.trans_size   = LCD_BUFFER_SIZE / 10;
    disp_cfg.draw_wait_cb = NULL;
    disp_cfg.flags.buff_dma    = false;
    disp_cfg.flags.buff_spiram = true;
    lvgl_port_add_disp(&disp_cfg);

    static lv_indev_drv_t indev_drv;
    lv_indev_drv_init(&indev_drv);
    indev_drv.type    = LV_INDEV_TYPE_POINTER;
    indev_drv.read_cb = touchpad_read;
    lv_indev_drv_register(&indev_drv);
}

extern "C" void app_main(void)
{
    i2c_master_bus_handle_t i2c_bus = bsp_i2c_init();
    io_expander_init(i2c_bus);
    bsp_display_init(&io_handle, &panel_handle, LCD_BUFFER_SIZE);
    bsp_touch_init(i2c_bus, LCD_H_RES, LCD_V_RES, 0);
    bsp_display_brightness_init();
    bsp_display_set_brightness(100);

    supermouse_cdc_init();
    lv_port_setup();

    if (lvgl_port_lock(0)) {
        lvgl_ui_init();
        lvgl_port_unlock();
    }
}
