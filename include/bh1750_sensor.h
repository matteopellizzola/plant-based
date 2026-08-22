#pragma once

#include <stdint.h>

namespace bh1750_sensor {

struct Reading {
  bool available;
  bool valid;
  float lux;
  uint32_t measuredAt;
};

void begin();
void update();
const Reading& getReading();

}  // namespace bh1750_sensor