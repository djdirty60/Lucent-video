#pragma once

#include "plan.h"

#include <cstdint>
#include <string>

namespace engine
{
struct DeviceInfo
{
  int64_t total = 0;      // memory of the box, bytes
  int64_t available = 0;  // memory free for programs right now (what the system can hand out without swapping)
  int cores = 0;
  bool bits64 = sizeof(void*) == 8;
};

// Reads the memory and processor of this box; false when the memory cannot be told (then nothing is changed).
bool ReadDevice(DeviceInfo& device);

// Sets the cache (and, on a tight box, the read-ahead) of this plan to what the box has free. Returns one line for
// the log, or "" when the plan stays as it is.
std::string FitToDevice(Plan& plan, const DeviceInfo& device);
}  // namespace engine
