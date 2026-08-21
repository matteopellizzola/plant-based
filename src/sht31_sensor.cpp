#include "sht31_sensor.h"

#include <Adafruit_SHT31.h>
#include <Arduino.h>

#include "config.h"

namespace sht31_sensor {
namespace {

Adafruit_SHT31 sensor;
bool available = false;
uint32_t lastReadMs = 0;

}  // namespace

void begin() {
  available = sensor.begin(config::SHT31_ADDRESS);

  if (available) {
    Serial.printf("[SHT31] Pronto all'indirizzo 0x%02X.\n",
                  config::SHT31_ADDRESS);
  } else {
    Serial.println("[SHT31] Non trovato: controllo indirizzo e cablaggio.");
  }
}

void update() {
  if (!available || millis() - lastReadMs < config::SENSOR_READ_INTERVAL_MS) {
    return;
  }
  lastReadMs = millis();

  const float temperatureC = sensor.readTemperature();
  const float humidityPercent = sensor.readHumidity();

  if (isnan(temperatureC) || isnan(humidityPercent)) {
    Serial.println("[SHT31] Lettura non valida.");
    return;
  }

  Serial.printf("[SHT31] Temperatura: %.2f C | Umidita': %.2f %%\n",
                temperatureC, humidityPercent);
}

}  // namespace sht31_sensor
