#pragma once

namespace ads1115_sensor {

// Inizializza l'ADC. E' normale che non sia disponibile finche' non e' cablato.
void begin();

// Stampa periodicamente i quattro ingressi analogici in Volt e percentuale di
// umidita', usando la calibrazione condivisa definita in config.h.
void update();

}  // namespace ads1115_sensor
