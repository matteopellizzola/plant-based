#pragma once

namespace sht31_sensor {

// Inizializza il sensore e stampa il risultato sul monitor seriale.
void begin();

// Legge temperatura e umidita' a intervalli regolari, senza bloccare il loop.
void update();

}  // namespace sht31_sensor
