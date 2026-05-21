#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void supermouse_cdc_init(void);
void supermouse_cdc_send_tap(int index);

#ifdef __cplusplus
}
#endif
