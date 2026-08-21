#include "wifi_manager.h"

#include <Arduino.h>
#include <WiFi.h>

#include "config.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
constexpr char WIFI_SSID[] = "";
constexpr char WIFI_PASSWORD[] = "";
#endif

namespace {

uint32_t lastAttemptAt = 0;

bool credentialsAreConfigured() {
  return WIFI_SSID[0] != '\0';
}

void connect() {
  if (!credentialsAreConfigured()) {
    Serial.println("[WiFi] Credenziali non configurate: continuo offline.");
    return;
  }

  Serial.printf("[WiFi] Connessione a %s...\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastAttemptAt = millis();
}

}  // namespace

namespace wifi_manager {

void begin() {
  WiFi.setAutoReconnect(true);
  connect();
}

void update() {
  static bool connectionWasAnnounced = false;

  if (WiFi.status() == WL_CONNECTED) {
    if (!connectionWasAnnounced) {
      Serial.printf("[WiFi] Connesso. IP: %s\n", WiFi.localIP().toString().c_str());
      connectionWasAnnounced = true;
    }
    return;
  }

  connectionWasAnnounced = false;

  if (!credentialsAreConfigured()) {
    return;
  }

  if (millis() - lastAttemptAt >= config::WIFI_RETRY_INTERVAL_MS) {
    Serial.println("[WiFi] Nuovo tentativo di connessione.");
    WiFi.disconnect();
    connect();
  }
}

bool isConnected() {
  return WiFi.status() == WL_CONNECTED;
}

}  // namespace wifi_manager
