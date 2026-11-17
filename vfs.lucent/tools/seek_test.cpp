// Same reads as Lucent's Python seek test: start, MKV index at the end, back, three jumps, then 15 s of playback.
#include "engine/common.h"
#include "engine/plan.h"
#include "engine/stream.h"
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
  auto plan = std::make_shared<engine::Plan>(engine::Plan::Parse(text.str()));
  engine::Stream stream(plan); std::string error;
  auto t0 = Clock::now();
  if (!stream.Start(error)) { std::fprintf(stderr, "start: %s\n", error.c_str()); return 2; }
  const int64_t size = stream.Length();
  std::vector<uint8_t> buf(16 << 20);
  auto rd = [&](const char* label, int64_t off, int64_t n) {
    auto t = Clock::now(); int64_t got = 0;
    while (got < n) { int64_t r = stream.Read(off + got, buf.data(), static_cast<size_t>(n - got)); if (r <= 0) break; got += r; }
    std::printf("  %-24s %.2f s\n", label, Since(t)); };
  std::printf("start %.2f s\n", Since(t0));
  rd("begin", 0, 1 << 20); rd("eind (MKV-index)", size - (4 << 20), 4 << 20); rd("terug naar begin", 1 << 20, 1 << 20);
  for (double f : {0.4, 0.7, 0.15}) { char l[40]; std::snprintf(l, sizeof l, "sprong naar %d%%", int(f * 100 + 0.5)); rd(l, int64_t(size * f), 2 << 20); std::this_thread::sleep_for(std::chrono::milliseconds(300)); }
  const double rate = argc > 2 ? std::atof(argv[2]) : 11e6; int64_t pos = int64_t(size * 0.15) + (2 << 20);
  auto start = Clock::now(); int waits = 0; double waited = 0;
  for (int s = 0; s < 150; ++s) {
    auto t = Clock::now(); int64_t want = int64_t(rate / 10), got = 0;
    while (got < want) { int64_t r = stream.Read(pos + got, buf.data(), static_cast<size_t>(want - got)); if (r <= 0) break; got += r; }
    double dt = Since(t); pos += want;
    auto due = start + std::chrono::duration_cast<Clock::duration>(std::chrono::duration<double>((s + 1) * 0.1));
    if (Clock::now() < due) std::this_thread::sleep_until(due); else if (dt > 0.15) { ++waits; waited += dt; }
  }
  std::printf("  daarna 15 s afspelen op %.0f MB/s: %d keer gewacht (%.1f s), achter %.1f s\n", rate / 1e6, waits, waited, std::max(0.0, Since(start) - 15));
  stream.Close(); return 0;
}
