// A heavy film on a small cache, like 8 Mile on the Fire TV Cube: the player plays and skips while
// Kodi opens a dozen extra readers for chapter pictures. Prints the player's waits; the engine logs
// its totals (evictions, refetches) at the end.
// mile_test <plan> <rate bytes/s>
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
  const double rate = std::atof(argv[2]);
  auto plan = std::make_shared<engine::Plan>(engine::Plan::Parse(text.str()));
  engine::Stream stream(plan); std::string error;
  if (!stream.Start(error)) { std::fprintf(stderr, "start: %s\n", error.c_str()); return 2; }
  const int64_t size = stream.Length();
  std::vector<uint8_t> buf(1 << 20);
  int64_t pos = 0; int waits = 0; double waited = 0;
  const auto t0 = Clock::now();
  auto play = [&](double seconds) {
    std::printf("%.1f s: play from article %lld\n", Since(t0), (long long)(pos / (std::getenv("SEGKB") ? std::atoi(std::getenv("SEGKB")) * 1024 : 700 * 1024))); std::fflush(stdout);
    auto start = Clock::now(); const int steps = int(seconds * 10);
    for (int s = 0; s < steps; ++s) {
      auto t = Clock::now(); int64_t want = int64_t(rate / 10), got = 0;
      while (got < want) { int64_t r = stream.Read(pos + got, buf.data(), size_t(want - got)); if (r <= 0) break; got += r; }
      double dt = Since(t); pos += want;
      auto due = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>((s + 1) * 0.1));
      if (Clock::now() < due) std::this_thread::sleep_until(due); else if (dt > 0.15) { ++waits; waited += dt; }
    }
  };
  std::atomic<bool> stop{false}; int side_reads = 0;
  std::thread side([&]() {
    if (std::getenv("NOSIDE")) return;
    std::this_thread::sleep_for(std::chrono::seconds(2));
    std::vector<uint8_t> b(64 * 1024);
    for (int k = 0; !stop.load() && k < 12; ++k) {
      stream.Read(0, b.data(), b.size(), false);                    // the header, every time
      const int64_t at = int64_t(size * (0.04 + 0.075 * k));
      for (int r = 0; r < 10; ++r) { stream.Read(at + r * int64_t(b.size()), b.data(), b.size(), false); ++side_reads; }
      stream.Read(at, b.data(), b.size(), false);                   // the decoder goes back to the key frame
      std::this_thread::sleep_for(std::chrono::milliseconds(1500));
    }
  });
  const auto begin = Clock::now();
  if (std::getenv("START")) pos = int64_t(size * std::atof(std::getenv("START")));
  if (std::getenv("PLAIN")) { play(std::getenv("SECONDS") ? std::atof(std::getenv("SECONDS")) : 40); goto done; }
  play(6);
  pos = int64_t(size * 0.09); play(4);                              // a skip far ahead
  pos = int64_t(size * 0.13); play(4);
  pos += int64_t(rate * 30); play(3);                               // +30 s
  if (!std::getenv("NOBACK")) { pos -= int64_t(rate * 60); play(6); }  // back a minute
  else play(6);
  pos += int64_t(rate * 120); play(6);                              // +2 min
done:
  stop = true; side.join();
  std::printf("player: %d waits (%.1f s) in %.0f s; extra reader: %d reads\n", waits, waited, Since(begin), side_reads);
  stream.LogTotals("end");
  stream.Close(); return 0;
}
