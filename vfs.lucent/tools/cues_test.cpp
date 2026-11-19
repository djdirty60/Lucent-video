// Reads like Kodi opening a Matroska file: the head, the index at the end, then playback at a spot.
// cues_test <plan> <index offset> <play offset>
#include "engine/plan.h"
#include "engine/stream.h"
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <thread>
#include <vector>
int main(int argc, char** argv)
{
  std::ifstream in(argv[1]); std::stringstream text; text << in.rdbuf();
  const int64_t index = std::atoll(argv[2]);
  const int64_t play = std::atoll(argv[3]);
  auto plan = std::make_shared<engine::Plan>(engine::Plan::Parse(text.str()));
  engine::Stream stream(plan); std::string error;
  if (!stream.Start(error)) { std::fprintf(stderr, "start: %s\n", error.c_str()); return 2; }
  std::vector<uint8_t> buf(1 << 20);
  auto read = [&](int64_t at, int64_t size) {
    int64_t got = 0;
    while (got < size) { int64_t r = stream.Read(at + got, buf.data(), size_t(std::min<int64_t>(size - got, int64_t(buf.size())))); if (r <= 0) return false; got += r; }
    return true;
  };
  if (!read(0, 256 * 1024)) return 3;
  if (!read(index, stream.Length() - index)) return 4;
  if (!read(play, 1 << 20)) return 5;
  std::this_thread::sleep_for(std::chrono::seconds(2));
  stream.Close();
  return 0;
}
