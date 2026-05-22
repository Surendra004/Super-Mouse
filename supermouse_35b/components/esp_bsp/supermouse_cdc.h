#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void supermouse_cdc_init(void);
void supermouse_cdc_send_tap(int index);
void supermouse_cdc_send_volume(int value);
void supermouse_cdc_send_mouse_move(int dx, int dy);
void supermouse_cdc_send_mouse_click(int button);

#ifdef __cplusplus
}
#endif
