#include "device.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#endif

namespace engine
{
namespace
{
constexpr int64_t kMB = 1024 * 1024;
constexpr int64_t kShare = 6;                   // at most a sixth of the free memory: Kodi itself needs the rest
constexpr int64_t kLeast = 48 * kMB;            // never less than this, whatever the box
constexpr int64_t kMost64 = 512 * kMB;
constexpr int64_t kMost32 = 320 * kMB;          // a 32-bit Kodi can only address a few GB in all
constexpr int64_t kSmallBox = 2560 * kMB;       // a box with this much memory or less in all (Fire TV Cube: 1.9 GB) ...
constexpr int64_t kSmallMost = 160 * kMB;       // ... keeps at most this: Kodi itself held 1.5 GB during a 4K remux
}  // namespace

bool ReadDevice(DeviceInfo& device)
{
  device.cores = static_cast<int>(std::thread::hardware_concurrency());
#ifdef _WIN32
  MEMORYSTATUSEX status;
  std::memset(&status, 0, sizeof(status));
  status.dwLength = sizeof(status);
  if (!GlobalMemoryStatusEx(&status))
    return false;
  device.total = static_cast<int64_t>(status.ullTotalPhys);
  device.available = static_cast<int64_t>(status.ullAvailPhys);
#else
  FILE* file = std::fopen("/proc/meminfo", "r");
  if (!file)
    return false;
  char line[256];
  long long total = -1, available = -1, free_kb = -1, cached = -1, buffers = -1;
  while (std::fgets(line, sizeof(line), file))
  {
    long long value = 0;
    if (std::sscanf(line, "MemTotal: %lld kB", &value) == 1)
      total = value;
    else if (std::sscanf(line, "MemAvailable: %lld kB", &value) == 1)
      available = value;
    else if (std::sscanf(line, "MemFree: %lld kB", &value) == 1)
      free_kb = value;
    else if (std::sscanf(line, "Cached: %lld kB", &value) == 1)
      cached = value;
    else if (std::sscanf(line, "Buffers: %lld kB", &value) == 1)
      buffers = value;
  }
  std::fclose(file);
  if (available < 0 && free_kb >= 0)            // old kernels: free plus what the system can drop at once
    available = free_kb + std::max(0LL, cached) + std::max(0LL, buffers);
  if (total <= 0 || available < 0)
    return false;
  device.total = total * 1024;
  device.available = available * 1024;
#endif
  return device.total > 0;
}

std::string FitToDevice(Plan& plan, const DeviceInfo& device)
{
  if (device.total <= 0 || device.available <= 0 || plan.cache <= 0)
    return "";
  const int64_t most = device.bits64 ? kMost64 : kMost32;
  const int64_t budget = std::min(most, device.available / kShare);
  const int64_t asked = plan.cache;
  const int64_t asked_ahead = plan.prefetch;
  int64_t cache = asked;
  if (budget > asked)
    cache = budget;                                     // room to spare: more of the film kept, quicker skips
  else if (device.available < asked * 3)
    cache = std::max(kLeast, budget);                   // a really tight box: leave Kodi its memory
  // otherwise what Lucent Video asked stays: it fits the film (a remux needs more), and the box has room for it
  if (device.total <= kSmallBox && cache > kSmallMost)
    cache = kSmallMost;                                 // 2 GB boxes (Fire TV Cube): Kodi itself needs most of it for 4K
  plan.cache = cache;
  if (cache < asked && plan.prefetch > cache / 2)       // less memory: read less far ahead too
    plan.prefetch = std::max<int64_t>(8 * kMB, cache / 2);
  char change[96];
  if (cache == asked)
    std::snprintf(change, sizeof(change), "%.0f MB (kept)", asked / double(kMB));
  else
    std::snprintf(change, sizeof(change), "%.0f MB -> %.0f MB", asked / double(kMB), cache / double(kMB));
  char text[320];
  std::snprintf(text, sizeof(text), "box: %d-bit, %d core(s), %.0f MB free of %.0f MB: cache %s, read-ahead %.0f MB%s",
                device.bits64 ? 64 : 32, device.cores, device.available / double(kMB), device.total / double(kMB),
                change, plan.prefetch / double(kMB), plan.prefetch == asked_ahead ? "" : " (made smaller)");
  return text;
}
}  // namespace engine
