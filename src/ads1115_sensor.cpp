#include "ads1115_sensor.h"

#include <Adafruit_ADS1X15.h>
#include <Arduino.h>

#include "config.h"
#include "soil_calibration.h"

namespace ads1115_sensor {
namespace {

Adafruit_ADS1115 adc;
bool available = false;
uint32_t lastReadMs = 0;

// Con GAIN_ONE ogni bit dell'ADS1115 corrisponde a 0,125 mV.
constexpr float VOLTS_PER_COUNT = 0.000125F;

}  // namespace

void begin() {
  available = adc.begin(config::ADS1115_ADDRESS);

  if (available) {
    // Intervallo del convertitore: +/- 4,096 V. Con alimentazione a 3,3 V,
    // non applicare mai agli ingressi una tensione superiore a 3,3 V.
    adc.setGain(GAIN_ONE);
    Serial.printf("[ADS1115] Pronto all'indirizzo 0x%02X.\n",
                  config::ADS1115_ADDRESS);
  } else {
    Serial.println("[ADS1115] Non trovato: verra' riprovato al prossimo riavvio.");
  }
}

void update() {
  if (!available || millis() - lastReadMs < config::SENSOR_READ_INTERVAL_MS) {
    return;
  }
  lastReadMs = millis();

  Serial.print("[ADS1115]");
  for (uint8_t channel = 0; channel < 4; ++channel) {
    const int16_t rawValue = adc.readADC_SingleEnded(channel);
    const float voltage = rawValue * VOLTS_PER_COUNT;
    const float moisturePercent = soil_calibration::moisturePercent(channel, voltage);
    Serial.printf(" A%u: %.3f V, %.0f%%", channel, voltage,
                  moisturePercent);
  }
  Serial.println();
}

}  // namespace ads1115_sensor
