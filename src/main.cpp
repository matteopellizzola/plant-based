#include <Arduino.h>
#include <Wire.h>

#include "ads1115_sensor.h"
#include "bh1750_sensor.h"
#include "config.h"
#include "mqtt_manager.h"
#include "sht31_sensor.h"
#include "soil_calibration.h"
#include "wifi_manager.h"

namespace {

void scanI2cBus() {
  uint8_t devicesFound = 0;

  Serial.println("[I2C] Scansione del bus...");

  for (uint8_t address = 1; address < 127; ++address) {
    Wire.beginTransmission(address);
    const uint8_t error = Wire.endTransmission();

    if (error == 0) {
      Serial.printf("[I2C] Dispositivo trovato all'indirizzo 0x%02X\n", address);
      ++devicesFound;
    }
  }

  if (devicesFound == 0) {
    Serial.println("[I2C] Nessun dispositivo trovato (normale se i sensori non sono collegati). ");
  } else {
    Serial.printf("[I2C] Scansione completata: %u dispositivo/i.\n", devicesFound);
  }
}

}  // namespace

void setup() {
  Serial.begin(config::SERIAL_BAUD_RATE);
  delay(500);

  Serial.println();
  Serial.println("================================");
  Serial.printf(" Avvio nodo: %s\n", config::NODE_ID);
  Serial.println("================================");

  Wire.begin(config::I2C_SDA_PIN, config::I2C_SCL_PIN,
             config::I2C_FREQUENCY_HZ);
  scanI2cBus();

  // Ogni modulo verifica autonomamente la presenza del proprio dispositivo.
  soil_calibration::begin();
  sht31_sensor::begin();
  bh1750_sensor::begin();
  ads1115_sensor::begin();
  wifi_manager::begin();
  mqtt_manager::begin();
}

void loop() {
  // Il loop resta sempre libero: ogni modulo aggiorna il proprio stato senza
  // bloccare gli altri. Sarà essenziale quando avremo sensori e MQTT insieme.
  wifi_manager::update();
  mqtt_manager::update();
  sht31_sensor::update();
  bh1750_sensor::update();
  ads1115_sensor::update();
  soil_calibration::update();
  delay(10);
}
