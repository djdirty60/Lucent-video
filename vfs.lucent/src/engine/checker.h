#pragma once

#include "plan.h"

#include <atomic>
#include <cstdint>
#include <string>

namespace engine
{
struct CheckResult
{
  int64_t total = 0;      // different articles in the plan
  int64_t present = 0;    // there at the provider or at a backup provider
  int64_t from_backup = 0;    // missing at the main provider, found at the backup
  int64_t at_backup = 0;      // asked at the backup first (it takes part of the work) and there
  double seconds = 0;
  double first_pass = 0;      // seconds until every article had been asked once
  int opened = 0;             // connections signed in for this check
  int broken = 0;
  double signin_ms = 0;      // average time to connect and sign in
  double rate[2] = {0, 0};    // answers a second per connection of the main provider: 2 packets in flight, 4
  std::string error;      // set when the check could not be finished (then present means nothing)
};

// Asks every article of the plan with STAT (no data), once, at the main provider (over 'connections' connections) and
// at the backup provider at the same time; what one of them lacks is asked once more at the other. Stops after
// limit_seconds, or when *stop turns true.
CheckResult CheckArticles(const Plan& plan, int connections, double limit_seconds, const std::atomic<bool>* stop);

// The answer as one line of JSON for Lucent Video.
std::string CheckJson(const CheckResult& result);
}  // namespace engine
