#include "soil_calibration.h"

#include <math.h>
#include <Preferences.h>
#include <string.h>

#include "config.h"

namespace soil_calibration {
namespace {

constexpr uint8_t CHANNEL_COUNT = 4;
constexpr float MIN_CALIBRATION_RANGE_V = 0.05F;

Preferences preferences;
Values calibrations[CHANNEL_COUNT];
char commandBuffer[80];
uint8_t commandLength = 0;

Values defaults() {
  return {config::SOIL_DRY_VOLTAGE, config::SOIL_WET_VOLTAGE,
          config::SOIL_MOISTURE_THRESHOLD_PERCENT};
}

bool validVoltage(float voltage) {
  return voltage >= 0.0F && voltage <= 4.096F;
}

bool validPair(float dryVoltage, float wetVoltage) {
  return validVoltage(dryVoltage) && validVoltage(wetVoltage) &&
         fabsf(dryVoltage - wetVoltage) >= MIN_CALIBRATION_RANGE_V;
}

bool parseChannel(const char* token, uint8_t* channel) {
  if (token == nullptr || token[0] != 'A' || token[2] != '\0' ||
      token[1] < '0' || token[1] > '3') {
    return false;
  }
  *channel = static_cast<uint8_t>(token[1] - '0');
  return true;
}

void keyFor(char prefix, uint8_t channel, char* key) {
  key[0] = prefix;
  key[1] = static_cast<char>('0' + channel);
  key[2] = '\0';
}

void save(uint8_t channel, char prefix, float value) {
  char key[3];
  keyFor(prefix, channel, key);
  preferences.putFloat(key, value);
}

void reset(uint8_t channel) {
  char key[3];
  for (const char prefix : {'d', 'w', 't'}) {
    keyFor(prefix, channel, key);
    preferences.remove(key);
  }
  calibrations[channel] = defaults();
}

void printOne(uint8_t channel) {
  const Values& value = calibrations[channel];
  Serial.printf("[CAL] A%u dry=%.3f V, wet=%.3f V, soglia=%.0f%%\n", channel,
                value.dryVoltage, value.wetVoltage, value.thresholdPercent);
}

void printHelp() {
  Serial.println("[CAL] Comandi: cal show | cal A0 dry 2.700 | cal A0 wet "
                 "1.250 | cal A0 threshold 35 | cal A0 reset");
}

void processCommand() {
  char* savePointer = nullptr;
  char* command = strtok_r(commandBuffer, " \t", &savePointer);
  if (command == nullptr || strcmp(command, "cal") != 0) {
    Serial.println("[CAL] Comando non riconosciuto. Scrivi: cal show");
    return;
  }

  char* target = strtok_r(nullptr, " \t", &savePointer);
  if (target != nullptr && strcmp(target, "show") == 0) {
    for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
      printOne(channel);
    }
    return;
  }

  uint8_t channel = 0;
  char* field = strtok_r(nullptr, " \t", &savePointer);
  char* valueToken = strtok_r(nullptr, " \t", &savePointer);
  if (!parseChannel(target, &channel) || field == nullptr) {
    printHelp();
    return;
  }
  if (strcmp(field, "reset") == 0 && valueToken == nullptr) {
    reset(channel);
    Serial.printf("[CAL] A%u ripristinato ai valori iniziali.\n", channel);
    printOne(channel);
    return;
  }
  if (valueToken == nullptr) {
    printHelp();
    return;
  }

  char* valueEnd = nullptr;
  const float value = strtof(valueToken, &valueEnd);
  if (valueEnd == valueToken || *valueEnd != '\0' || !isfinite(value)) {
    Serial.println("[CAL] Il valore deve essere un numero valido.");
    return;
  }
  Values candidate = calibrations[channel];
  char keyPrefix = '\0';
  if (strcmp(field, "dry") == 0) {
    candidate.dryVoltage = value;
    keyPrefix = 'd';
  } else if (strcmp(field, "wet") == 0) {
    candidate.wetVoltage = value;
    keyPrefix = 'w';
  } else if (strcmp(field, "threshold") == 0 && value >= 0.0F && value <= 100.0F) {
    candidate.thresholdPercent = value;
    keyPrefix = 't';
  } else {
    printHelp();
    return;
  }

  if ((keyPrefix == 'd' || keyPrefix == 'w') &&
      !validPair(candidate.dryVoltage, candidate.wetVoltage)) {
    Serial.println(
        "[CAL] dry e wet devono essere fra 0 e 4.096 V e distare almeno "
        "0.05 V.");
    return;
  }
  calibrations[channel] = candidate;
  save(channel, keyPrefix, value);
  Serial.printf("[CAL] A%u salvato in NVS.\n", channel);
  printOne(channel);
}

}  // namespace

void begin() {
  preferences.begin("plantcal", false);
  for (uint8_t channel = 0; channel < CHANNEL_COUNT; ++channel) {
    calibrations[channel] = defaults();
    char key[3];
    keyFor('d', channel, key);
    calibrations[channel].dryVoltage =
        preferences.getFloat(key, calibrations[channel].dryVoltage);
    keyFor('w', channel, key);
    calibrations[channel].wetVoltage =
        preferences.getFloat(key, calibrations[channel].wetVoltage);
    keyFor('t', channel, key);
    calibrations[channel].thresholdPercent =
        preferences.getFloat(key, calibrations[channel].thresholdPercent);
    if (!validPair(calibrations[channel].dryVoltage,
                   calibrations[channel].wetVoltage) ||
        calibrations[channel].thresholdPercent < 0.0F ||
        calibrations[channel].thresholdPercent > 100.0F) {
      calibrations[channel] = defaults();
      Serial.printf("[CAL] A%u: valori NVS non validi, uso valori iniziali.\n", channel);
    }
    printOne(channel);
  }
  printHelp();
}

const Values& get(uint8_t channel) {
  return calibrations[channel < CHANNEL_COUNT ? channel : 0];
}

float moisturePercent(uint8_t channel, float voltage) {
  const Values& values = get(channel);
  const float range = values.dryVoltage - values.wetVoltage;
  float percentage = (values.dryVoltage - voltage) * 100.0F / range;
  return constrain(percentage, 0.0F, 100.0F);
}

void update() {
  while (Serial.available() > 0) {
    const char character = static_cast<char>(Serial.read());
    if (character == '\r') {
      continue;
    }
    if (character == '\n') {
      commandBuffer[commandLength] = '\0';
      if (commandLength > 0) {
        processCommand();
      }
      commandLength = 0;
    } else if (commandLength < sizeof(commandBuffer) - 1) {
      commandBuffer[commandLength++] = character;
    } else {
      commandLength = 0;
      Serial.println("[CAL] Comando troppo lungo.");
    }
  }
}

}  // namespace soil_calibration
