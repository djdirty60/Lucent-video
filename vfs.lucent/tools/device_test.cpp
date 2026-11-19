// Shows what vfs.lucent would choose: device_test [available MB total MB bits plan-cache MB plan-prefetch MB]
#include "engine/device.h"
#include <cstdio>
#include <cstdlib>
int main(int argc, char** argv)
{
  engine::DeviceInfo device;
  if (argc > 5)
  {
    device.available = std::atoll(argv[1]) << 20; device.total = std::atoll(argv[2]) << 20;
    device.bits64 = std::atoi(argv[3]) == 64; device.cores = 4;
  }
  else if (!engine::ReadDevice(device)) { std::puts("cannot read this box"); return 1; }
  engine::Plan plan;
  plan.cache = (argc > 5 ? std::atoll(argv[4]) : 128) << 20;
  plan.prefetch = (argc > 5 ? std::atoll(argv[5]) : 96) << 20;
  std::puts(engine::FitToDevice(plan, device).c_str());
  return 0;
}
