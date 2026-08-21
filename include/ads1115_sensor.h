#pragma once

#include <stdint.h>

namespace ads1115_sensor {

struct Reading {
	bool available;
	float voltage[4];
	float moisturePercent[4];
	uint32_t measuredAt;
};

// Inizializza l'ADC. E' normale che non sia disponibile finche' non e' cablato.
void begin();

// Stampa periodicamente i quattro ingressi analogici in Volt e percentuale di
// umidita', usando la calibrazione condivisa definita in config.h.
void update();
const Reading& getReading();

}  // namespace ads1115_sensor
