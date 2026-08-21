#pragma once

#include <stdint.h>

namespace sht31_sensor {

struct Reading {
	bool available;
	bool valid;
	float temperatureC;
	float humidityPercent;
	uint32_t measuredAt;
};

// Inizializza il sensore e stampa il risultato sul monitor seriale.
void begin();

// Legge temperatura e umidita' a intervalli regolari, senza bloccare il loop.
void update();
const Reading& getReading();

}  // namespace sht31_sensor
