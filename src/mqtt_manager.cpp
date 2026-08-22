#include "mqtt_manager.h"

#include <Arduino.h>
#include <ArduinoJson.h>
#include <PubSubClient.h>
#include <WiFiClient.h>
#include <stdio.h>
#include <string.h>

#include "ads1115_sensor.h"
#include "bh1750_sensor.h"
#include "config.h"
#include "sht31_sensor.h"
#include "soil_calibration.h"
#include "wifi_manager.h"

#if __has_include("secrets.h")
#include "secrets.h"
#endif

#ifndef MQTT_BROKER_HOST
#define MQTT_BROKER_HOST ""
#endif
#ifndef MQTT_BROKER_PORT
#define MQTT_BROKER_PORT 1883
#endif
#ifndef MQTT_USERNAME
#define MQTT_USERNAME ""
#endif
#ifndef MQTT_PASSWORD
#define MQTT_PASSWORD ""
#endif

namespace mqtt_manager {
namespace {

WiFiClient wifiClient;
PubSubClient client(wifiClient);
uint32_t lastAttemptAt = 0;
uint32_t lastPublishAt = 0;
char stateTopic[80];
char measurementsTopic[80];
char configTopic[80];

bool configured() {
  return MQTT_BROKER_HOST[0] != '\0';
}

void makeTopics() {
  snprintf(stateTopic, sizeof(stateTopic), "plants/%s/state", config::NODE_ID);
  snprintf(measurementsTopic, sizeof(measurementsTopic),
           "plants/%s/measurements", config::NODE_ID);
  snprintf(configTopic, sizeof(configTopic), "plants/%s/config", config::NODE_ID);
}

void publishState(const char* state) {
  StaticJsonDocument<256> document;
  document["node"] = config::NODE_ID;
  document["state"] = state;
  document["uptime_s"] = millis() / 1000UL;
  document["wifi"] = wifi_manager::isConnected();
  document["sht31"] = sht31_sensor::getReading().available;
  document["bh1750"] = bh1750_sensor::getReading().available;
  document["ads1115"] = ads1115_sensor::getReading().available;
  char payload[256];
  const size_t length = serializeJson(document, payload, sizeof(payload));
  client.publish(stateTopic, reinterpret_cast<const uint8_t*>(payload), length,
                 true);
}

void publishMeasurements() {
  const ads1115_sensor::Reading& soil = ads1115_sensor::getReading();
  const sht31_sensor::Reading& air = sht31_sensor::getReading();
  const bh1750_sensor::Reading& light = bh1750_sensor::getReading();
  StaticJsonDocument<896> document;
  document["node"] = config::NODE_ID;
  document["uptime_s"] = millis() / 1000UL;
  JsonArray soilItems = document.createNestedArray("soil");
  for (uint8_t channel = 0; channel < 4; ++channel) {
    JsonObject item = soilItems.createNestedObject();
    item["channel"] = channel;
    item["available"] = soil.available;
    item["voltage"] = soil.voltage[channel];
    item["moisture_percent"] = soil.moisturePercent[channel];
    item["threshold_percent"] =
        soil_calibration::get(channel).thresholdPercent;
  }
  JsonObject airItem = document.createNestedObject("air");
  airItem["available"] = air.available;
  airItem["valid"] = air.valid;
  if (air.valid) {
    airItem["temperature_c"] = air.temperatureC;
    airItem["humidity_percent"] = air.humidityPercent;
  }
  JsonObject lightItem = document.createNestedObject("light");
  lightItem["available"] = light.available;
  lightItem["valid"] = light.valid;
  if (light.valid) {
    lightItem["lux"] = light.lux;
  }

  char payload[896];
  const size_t length = serializeJson(document, payload, sizeof(payload));
  client.publish(measurementsTopic, reinterpret_cast<const uint8_t*>(payload),
                 length, false);
}

void applyConfiguration(const uint8_t* payload, unsigned int length) {
  StaticJsonDocument<384> document;
  const DeserializationError error = deserializeJson(document, payload, length);
  if (error) {
    Serial.printf("[MQTT] Configurazione JSON non valida: %s\n", error.c_str());
    return;
  }
  const int channel = document["channel"] | -1;
  if (channel < 0 || channel > 3) {
    Serial.println("[MQTT] Configurazione ignorata: channel deve essere 0..3.");
    return;
  }
  if (document["reset"] | false) {
    soil_calibration::resetChannel(static_cast<uint8_t>(channel));
    Serial.printf("[MQTT] Calibrazione A%d ripristinata.\n", channel);
    publishMeasurements();
    return;
  }

  const soil_calibration::Values current = soil_calibration::get(channel);
  soil_calibration::Values candidate = current;
  if (document.containsKey("dry")) {
    candidate.dryVoltage = document["dry"].as<float>();
  }
  if (document.containsKey("wet")) {
    candidate.wetVoltage = document["wet"].as<float>();
  }
  if (document.containsKey("threshold")) {
    candidate.thresholdPercent = document["threshold"].as<float>();
  }
  if (!soil_calibration::set(static_cast<uint8_t>(channel), candidate)) {
    Serial.printf("[MQTT] Calibrazione A%d non valida.\n", channel);
    return;
  }
  Serial.printf("[MQTT] Calibrazione A%d salvata in NVS.\n", channel);
  publishMeasurements();
}

void callback(char* topic, uint8_t* payload, unsigned int length) {
  if (strcmp(topic, configTopic) == 0) {
    applyConfiguration(payload, length);
  }
}

bool connectToBroker() {
  if (!wifi_manager::isConnected()) {
    return false;
  }
  Serial.printf("[MQTT] Connessione a %s:%u...\n", MQTT_BROKER_HOST,
                MQTT_BROKER_PORT);
  char willPayload[128];
  snprintf(willPayload, sizeof(willPayload),
           "{\"node\":\"%s\",\"state\":\"offline\"}",
           config::NODE_ID);
  const bool connected = client.connect(config::NODE_ID, MQTT_USERNAME,
                                        MQTT_PASSWORD, stateTopic, 1, true,
                                        willPayload);
  if (!connected) {
    Serial.printf("[MQTT] Connessione fallita, stato %d.\n", client.state());
    return false;
  }
  client.subscribe(configTopic, 1);
  publishState("online");
  publishMeasurements();
  lastPublishAt = millis();
  Serial.println("[MQTT] Connesso e in ascolto sulla configurazione.");
  return true;
}

}  // namespace

void begin() {
  makeTopics();
  if (!configured()) {
    Serial.println("[MQTT] Broker non configurato: continuo offline.");
    return;
  }
  client.setServer(MQTT_BROKER_HOST, MQTT_BROKER_PORT);
  client.setBufferSize(1024);
  client.setCallback(callback);
}

void update() {
  if (!configured()) {
    return;
  }
  if (!wifi_manager::isConnected()) {
    return;
  }
  if (!client.connected()) {
    if (millis() - lastAttemptAt >= config::MQTT_RETRY_INTERVAL_MS) {
      lastAttemptAt = millis();
      connectToBroker();
    }
    return;
  }

  client.loop();
  if (millis() - lastPublishAt >= config::MQTT_PUBLISH_INTERVAL_MS) {
    lastPublishAt = millis();
    publishState("online");
    publishMeasurements();
  }
}

bool isConnected() { return client.connected(); }

}  // namespace mqtt_manager
