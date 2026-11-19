// Runs the article check of the controle add-on over a plan and prints its answer.
// check_test <plan> <connections> [limit seconds] [times]: 'times' checks in a row in one process (like a source
// list), so connections kept from one check are used by the next.
#include "engine/checker.h"
#include "engine/plan.h"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <sstream>
#include <thread>
#include <chrono>
int main(int argc, char** argv)
{
  std::ifstream in(argv[1]); std::stringstream text; text << in.rdbuf();
  const engine::Plan plan = engine::Plan::Parse(text.str());
  const int times = argc > 4 ? std::atoi(argv[4]) : 1;
  bool good = true;
  for (int run = 0; run < times; ++run)
  {
    if (run > 0 && std::getenv("CHECK_PAUSE"))
      std::this_thread::sleep_for(std::chrono::milliseconds(static_cast<int>(1000 * std::atof(std::getenv("CHECK_PAUSE")))));
    const engine::CheckResult result = engine::CheckArticles(plan, std::atoi(argv[2]), argc > 3 ? std::atof(argv[3]) : 60.0, nullptr);
    std::fputs(engine::CheckJson(result).c_str(), stdout);
    std::fprintf(stderr, "per connection: %.0f/s (2 in flight), %.0f/s (4 in flight)\n", result.rate[0], result.rate[1]);
    if (times > 1)
      std::printf("  first pass %.2f s, opened %d, broken %d, at the backup %lld\n", result.first_pass,
                  result.opened, result.broken, static_cast<long long>(result.at_backup));
    good = good && result.error.empty();
  }
  return good ? 0 : 1;
}
