#include "bh1750_sensor.h"

#include <Arduino.h>
#include <BH1750.h>

#include "config.h"

namespace bh1750_sensor {
namespace {

BH1750 sensor;
Reading reading = {false, false, NAN, 0};
uint32_t lastReadMs = 0;

}  // namespace

void begin() {
  reading.available = sensor.begin(BH1750::CONTINUOUS_HIGH_RES_MODE,
                                   config::BH1750_ADDRESS, &Wire);

  if (reading.available) {
    Serial.printf("[BH1750] Pronto all'indirizzo 0x%02X.\n",
                  config::BH1750_ADDRESS);
  } else {
    Serial.println("[BH1750] Non trovato: controllo indirizzo e cablaggio.");
  }
}

void update() {
  if (!reading.available ||
      millis() - lastReadMs < config::SENSOR_READ_INTERVAL_MS) {
    return;
  }
  lastReadMs = millis();

  reading.lux = sensor.readLightLevel();
  if (reading.lux < 0.0F || isnan(reading.lux)) {
    reading.valid = false;
    Serial.println("[BH1750] Lettura non valida.");
    return;
  }

  reading.valid = true;
  reading.measuredAt = millis();
  Serial.printf("[BH1750] Luminosita': %.2f lux\n", reading.lux);
}

const Reading& getReading() { return reading; }

}  // namespace bh1750_sensor