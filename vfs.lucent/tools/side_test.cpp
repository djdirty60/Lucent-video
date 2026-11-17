// Player reads on at a set rate while a second reader jumps around (like Kodi making chapter pictures).
// side_test <plan> <side-as-lead 0|1> [rate]
#include "engine/plan.h"
#include "engine/stream.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <thread>
#include <vector>
using Clock = std::chrono::steady_clock;
static double Since(Clock::time_point t) { return std::chrono::duration<double>(Clock::now() - t).count(); }
int main(int argc, char** argv)
{
  std::ifstream in(argv[1]); std::stringstream text; text << in.rdbuf();
  const bool side_lead = std::atoi(argv[2]) != 0;
  const double rate = argc > 3 ? std::atof(argv[3]) : 11e6;
  auto plan = std::make_shared<engine::Plan>(engine::Plan::Parse(text.str()));
  engine::Stream stream(plan); std::string error;
  if (!stream.Start(error)) { std::fprintf(stderr, "start: %s\n", error.c_str()); return 2; }
  const int64_t size = stream.Length();
  std::vector<uint8_t> buf(4 << 20);
  int64_t pos = int64_t(size * 0.3);
  // warm up 10 s of playback first
  auto play = [&](int steps, int& waits, double& waited) {
    auto start = Clock::now();
    for (int s = 0; s < steps; ++s) {
      auto t = Clock::now(); int64_t want = int64_t(rate / 10), got = 0;
      while (got < want) { int64_t r = stream.Read(pos + got, buf.data(), size_t(want - got)); if (r <= 0) break; got += r; }
      double dt = Since(t); pos += want;
      auto due = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>((s + 1) * 0.1));
      if (Clock::now() < due) std::this_thread::sleep_until(due); else if (dt > 0.15) { ++waits; waited += dt; }
    }
  };
  int w0 = 0; double d0 = 0; play(100, w0, d0);
  std::atomic<bool> stop{false}; int side_reads = 0;
  std::thread side([&]() {
    std::vector<uint8_t> b(512 * 1024);
    for (int k = 0; !stop.load() && k < 12; ++k) {
      int64_t at = int64_t(size * (0.05 + 0.08 * k));
      for (int r = 0; r < 6; ++r) { stream.Read(at + r * 512 * 1024, b.data(), b.size(), side_lead); ++side_reads; }
      stream.Read(0, b.data(), 64 * 1024, side_lead);
    }
  });
  int w = 0; double d = 0; play(200, w, d);
  stop = true; side.join();
  std::printf("extra reader %s: %d side reads; player 20 s while it ran: %d waits (%.1f s)\n",
              side_lead ? "AS LEAD (old)" : "beside (new)", side_reads, w, d);
  stream.Close(); return 0;
}
