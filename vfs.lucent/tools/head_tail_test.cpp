// Start like Kodi (begin, then the Matroska index near the end), play a while elsewhere, then jump back to the start.
#include "engine/plan.h"
#include "engine/stream.h"
#include <chrono>
#include <cstdio>
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
  if (!stream.Start(error)) return 2;
  const int64_t size = stream.Length();
  std::vector<uint8_t> buf(32 << 20);
  auto rd = [&](int64_t off, int64_t n) { auto t = Clock::now(); int64_t got = 0;
    while (got < n) { int64_t r = stream.Read(off + got, buf.data(), size_t(n - got)); if (r <= 0) break; got += r; } return Since(t); };
  double a = rd(0, 1 << 20);
  std::this_thread::sleep_for(std::chrono::milliseconds(600));   // Kodi parses the header
  double b = rd(size - (3 << 20), 1 << 20);                       // the index, 3 MB before the end (not the last article)
  rd(1 << 20, 30 << 20);                                          // plays the start a bit
  int64_t pos = int64_t(size * 0.4);
  for (int i = 0; i < 40; ++i) { rd(pos, 8 << 20); pos += 8 << 20; } // plays 320 MB elsewhere: the cache turns over
  double c = rd(0, 4 << 20);                                      // back to the beginning
  std::printf("start %.2f s, index near the end %.2f s, back to the beginning %.2f s\n", a, b, c);
  stream.Close(); return 0;
}
