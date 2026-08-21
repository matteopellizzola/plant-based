#include "ads1115_sensor.h"

#include <Adafruit_ADS1X15.h>
#include <Arduino.h>

#include "config.h"
#include "soil_calibration.h"

namespace ads1115_sensor {
namespace {

Adafruit_ADS1115 adc;
Reading reading = {false, {0.0F, 0.0F, 0.0F, 0.0F},
                   {0.0F, 0.0F, 0.0F, 0.0F}, 0};
uint32_t lastReadMs = 0;

// Con GAIN_ONE ogni bit dell'ADS1115 corrisponde a 0,125 mV.
constexpr float VOLTS_PER_COUNT = 0.000125F;

}  // namespace

void begin() {
  reading.available = adc.begin(config::ADS1115_ADDRESS);

  if (reading.available) {
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
  if (!reading.available || millis() - lastReadMs < config::SENSOR_READ_INTERVAL_MS) {
    return;
  }
  lastReadMs = millis();

  Serial.print("[ADS1115]");
  for (uint8_t channel = 0; channel < 4; ++channel) {
    const int16_t rawValue = adc.readADC_SingleEnded(channel);
    reading.voltage[channel] = rawValue * VOLTS_PER_COUNT;
    reading.moisturePercent[channel] = soil_calibration::moisturePercent(
        channel, reading.voltage[channel]);
    Serial.printf(" A%u: %.3f V, %.0f%%", channel, reading.voltage[channel],
                  reading.moisturePercent[channel]);
  }
  reading.measuredAt = millis();
  Serial.println();
}

const Reading& getReading() { return reading; }

}  // namespace ads1115_sensor
