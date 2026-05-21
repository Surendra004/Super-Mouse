#pragma once
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

void bsp_es8311_recording(uint8_t *data, int size);
void bsp_es8311_playing(uint8_t *data, int size);

#ifdef __cplusplus
}
#endif
