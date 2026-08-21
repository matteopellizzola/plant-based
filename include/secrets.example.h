#pragma once

// Copia questo file come include/secrets.h e inserisci i dati della tua rete.
// Il file secrets.h è ignorato da Git, quindi le credenziali restano locali.
constexpr char WIFI_SSID[] = "NOME_RETE_WIFI";
constexpr char WIFI_PASSWORD[] = "PASSWORD_WIFI";

// Impostazioni del broker MQTT. Lascia username e password vuoti se il
// broker non richiede autenticazione.
#define MQTT_BROKER_HOST "192.168.1.20"
#define MQTT_BROKER_PORT 1883
#define MQTT_USERNAME ""
#define MQTT_PASSWORD ""
