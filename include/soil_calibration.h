#pragma once

#include <Arduino.h>

namespace soil_calibration {

struct Values {
  float dryVoltage;
  float wetVoltage;
  float thresholdPercent;
};

// Carica le calibrazioni dei quattro vasi dalla NVS; in assenza di valori
// salvati usa i valori iniziali definiti in config.h.
void begin();

const Values& get(uint8_t channel);
float moisturePercent(uint8_t channel, float voltage);

// Gestisce i comandi ricevuti dalla porta seriale, senza bloccare il loop.
void update();

}  // namespace soil_calibration
