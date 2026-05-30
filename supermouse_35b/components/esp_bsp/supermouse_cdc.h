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
void supermouse_cdc_send_text(const char *text);
void supermouse_cdc_send_word(const char *word);

#ifdef __cplusplus
}
#endif
