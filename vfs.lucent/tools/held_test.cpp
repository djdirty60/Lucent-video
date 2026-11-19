// The player reaches an article that was queued for an extra reader (a skip target) while extra readers are held
// back: it must still come. Plays straight through a low-bitrate film where the 30 s skip target lies inside the
// read-ahead. Exit 0 when it played through, 3 when a read hung.
// held_test <plan> <rate bytes/s> <seconds to play>
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
int main(int argc, char** argv)
{
  std::ifstream in(argv[1]); std::stringstream text; text << in.rdbuf();
  const double rate = std::atof(argv[2]);
  const double seconds = std::atof(argv[3]);
  auto plan = std::make_shared<engine::Plan>(engine::Plan::Parse(text.str()));
  engine::Stream stream(plan); std::string error;
  if (!stream.Start(error)) { std::fprintf(stderr, "start: %s\n", error.c_str()); return 2; }
  std::atomic<bool> done{false}; std::atomic<double> last{0};
  const auto t0 = Clock::now();
  auto since = [&]() { return std::chrono::duration<double>(Clock::now() - t0).count(); };
  std::thread player([&]() {
    std::vector<uint8_t> buf(1 << 20); int64_t pos = 0;
    while (since() < seconds) {
      int64_t want = int64_t(rate / 10), got = 0;
      while (got < want) { int64_t r = stream.Read(pos + got, buf.data(), size_t(want - got)); if (r <= 0) { done = true; return; } got += r; }
      pos += want; last = since();
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      if (pos == int64_t(rate)) { stream.Read(0, buf.data(), 65536); }   // Kodi reads the start again (a stream switch)
    }
    done = true;
  });
  while (!done.load()) {
    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    if (since() - last.load() > 15) { std::printf("HUNG at %.1f s\n", last.load()); std::fflush(stdout); std::_Exit(3); }
  }
  player.join();
  std::printf("played %.0f s\n", since());
  stream.Close(); return 0;
}
