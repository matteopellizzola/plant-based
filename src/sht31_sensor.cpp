#include "sht31_sensor.h"

#include <Adafruit_SHT31.h>
#include <Arduino.h>

#include "config.h"

namespace sht31_sensor {
namespace {

Adafruit_SHT31 sensor;
Reading reading = {false, false, NAN, NAN, 0};
uint32_t lastReadMs = 0;

}  // namespace

void begin() {
  reading.available = sensor.begin(config::SHT31_ADDRESS);

  if (reading.available) {
    Serial.printf("[SHT31] Pronto all'indirizzo 0x%02X.\n",
                  config::SHT31_ADDRESS);
  } else {
    Serial.println("[SHT31] Non trovato: controllo indirizzo e cablaggio.");
  }
}

void update() {
  if (!reading.available || millis() - lastReadMs < config::SENSOR_READ_INTERVAL_MS) {
    return;
  }
  lastReadMs = millis();

  reading.temperatureC = sensor.readTemperature();
  reading.humidityPercent = sensor.readHumidity();

  if (isnan(reading.temperatureC) || isnan(reading.humidityPercent)) {
    reading.valid = false;
    Serial.println("[SHT31] Lettura non valida.");
    return;
  }
  reading.valid = true;
  reading.measuredAt = millis();

  Serial.printf("[SHT31] Temperatura: %.2f C | Umidita': %.2f %%\n",
                reading.temperatureC, reading.humidityPercent);
}

const Reading& getReading() { return reading; }

}  // namespace sht31_sensor
