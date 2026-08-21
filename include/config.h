#pragma once

#include <Arduino.h>

namespace config {

// Identifica il nodo nei log e, più avanti, nei topic MQTT.
constexpr char NODE_ID[] = "plant-node-01";

// Pin I2C standard delle comuni ESP32 DevKit/WROOM-32.
constexpr uint8_t I2C_SDA_PIN = 21;
constexpr uint8_t I2C_SCL_PIN = 22;

// Frequenze e intervalli iniziali.
constexpr uint32_t SERIAL_BAUD_RATE = 115200;
constexpr uint32_t I2C_FREQUENCY_HZ = 100000;
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 10000;
constexpr uint32_t SENSOR_READ_INTERVAL_MS = 2000;
constexpr uint32_t MQTT_RETRY_INTERVAL_MS = 10000;
constexpr uint32_t MQTT_PUBLISH_INTERVAL_MS = 10000;

// Indirizzi I2C scelti tramite i pin AD/ADDR dei moduli.
constexpr uint8_t SHT31_ADDRESS = 0x44;
constexpr uint8_t ADS1115_ADDRESS = 0x48;

// Calibrazione provvisoria dei sensori capacitivi alimentati a 3,3 V.
// Nei sensori usati qui la tensione aumenta quando il terriccio si asciuga.
// Misurare i valori reali di terreno completamente asciutto e ben bagnato e
// sostituire questi due estremi prima di usare le percentuali per decisioni
// automatiche. La stessa curva e' applicata inizialmente a A0, A1, A2 e A3.
constexpr float SOIL_DRY_VOLTAGE = 2.70F;
constexpr float SOIL_WET_VOLTAGE = 1.25F;

// Soglia iniziale per gli avvisi futuri. Ogni vaso puo' sovrascriverla e
// conservarla nella memoria NVS tramite i comandi seriali di calibrazione.
constexpr float SOIL_MOISTURE_THRESHOLD_PERCENT = 35.0F;

}  // namespace config
