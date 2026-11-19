#include "checker.h"

#include "common.h"
#include "nntp.h"

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <deque>
#include <memory>
#include <mutex>
#include <thread>
#include <unordered_set>
#include <vector>

namespace engine
{
namespace
{
constexpr size_t kInFlight = 2;             // packets of questions on their way per connection
constexpr size_t kBatch = 200;              // STAT questions per packet (as Lucent Video: a longer burst can fill both buffers)
constexpr int kBackupKeep = 2;              // backup connections left for reading the files beside the check
constexpr int kBackupMost = 4;               // as in 0.1.1
constexpr int kRetriesInARow = 3;           // in a row, as in 0.1.1
// No deadline per packet: a provider looks old articles up slowly, and a packet of those can take longer than any
// fixed time (0.1.3-0.1.5 broke such releases off). Each answer still has the socket timeout (20 s without a byte),
// the whole check has its time limit, and a stop is seen between two answers.

using Clock = std::chrono::steady_clock;

double Since(Clock::time_point start)
{
  return std::chrono::duration<double>(Clock::now() - start).count();
}

// One provider (or the backup providers together) and how many connections it gets for the check.
struct Side
{
  std::vector<const ServerConfig*> servers;
  int connections = 0;
};

struct Counters
{
  std::atomic<int> opened{0};
  std::atomic<int> broken{0};
  // the measurement: answers and busy time of the main provider's connections, by packets in flight (2 or 4)
  std::atomic<int64_t> answers[2] = {{0}, {0}};
  std::atomic<int64_t> micros[2] = {{0}, {0}};
  std::atomic<int64_t> signin_micros{0};      // time to connect and sign in, all connections together
};

// Asks 'ids' over all sides at once: every connection takes the next packet of questions, whichever side it belongs
// to. answered[batch] tells which side gave the answer, so what one side lacks can be asked at the other.
std::string AskSides(const Plan& plan, const std::vector<Side>& sides, const std::vector<std::string>& ids,
                     std::vector<char>& present, std::vector<int>& answered, Clock::time_point started,
                     double limit_seconds, const std::atomic<bool>* stop, Counters& counters)
{
  const size_t batches = (ids.size() + kBatch - 1) / kBatch;
  answered.assign(batches, -1);
  if (ids.empty() || sides.empty())
    return "";
  std::atomic<size_t> next{0};
  std::atomic<size_t> finished{0};
  std::atomic<bool> any_answer{false};              // the provider answered at least one packet of this check
  std::mutex shared_mutex;
  std::deque<size_t> orphans;                       // packets of a side that went away: another side asks them
  std::string first_error;
  std::string stuck_id;
  std::vector<std::atomic<int>> alive(sides.size());
  std::vector<std::thread> threads;
  for (size_t side = 0; side < sides.size(); ++side)
  {
    int slots = 0;
    for (const ServerConfig* server : sides[side].servers)
      slots += std::max(1, server->connections);
    const int count = std::max(1, std::min<int>({sides[side].connections, slots, static_cast<int>(batches)}));
    alive[side] = count;
    for (int worker = 0; worker < count; ++worker)
    {
      const ServerConfig* server = sides[side].servers[static_cast<size_t>(worker) % sides[side].servers.size()];
      threads.emplace_back(
          [&, side, server, worker]()
          {
            // 2 packets on their way: 4 was measured on Wilfried's provider (0.1.7) and was not faster
            const int group = 0;
            const size_t depth = kInFlight;
            std::unique_ptr<NntpConnection> connection;
            std::string flight_ids_first;       // the first article of the packet this connection waits for
            std::deque<size_t> flight;
            int retries = 0;
            auto ids_of = [&](size_t batch)
            {
              const size_t from = batch * kBatch;
              const size_t to = std::min(ids.size(), from + kBatch);
              return std::vector<std::string>(ids.begin() + static_cast<long>(from),
                                              ids.begin() + static_cast<long>(to));
            };
            auto take = [&](size_t& batch) -> bool
            {
              {
                std::lock_guard<std::mutex> lock(shared_mutex);
                if (!orphans.empty())
                {
                  batch = orphans.front();
                  orphans.pop_front();
                  return true;
                }
              }
              batch = next.fetch_add(1);
              return batch < batches;
            };
            auto give_up = [&](const std::string& why)
            {
              std::lock_guard<std::mutex> lock(shared_mutex);
              for (size_t batch : flight)
                orphans.push_back(batch);
              flight.clear();
              if (--alive[side] == 0)
              {
                bool others = false;
                for (size_t other = 0; other < sides.size(); ++other)
                  others = others || (other != side && alive[other] > 0);
                if (!others && first_error.empty())
                  first_error = why;            // nobody left to ask
              }
            };
            for (;;)
            {
              if ((stop && stop->load()) || Since(started) > limit_seconds)
                return;
              try
              {
                if (!connection)
                {
                  const auto signing = Clock::now();
                  connection.reset(new NntpConnection(*server, plan.timeout, plan.cacert));
                  ++counters.opened;
                  counters.signin_micros +=
                      std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - signing).count();
                }
                size_t batch = 0;
                const auto round = Clock::now();
                while (flight.size() < depth && take(batch))
                {
                  flight.push_back(batch);
                  connection->StatSend(ids_of(batch));
                }
                if (flight.empty())
                  return;                       // nothing left: the connection closes (never kept between checks)
                const size_t done = flight.front();
                flight_ids_first = ids[done * kBatch];
                const std::vector<bool> answer = connection->StatRead(
                    ids_of(done), started + std::chrono::milliseconds(static_cast<int64_t>(limit_seconds * 1000)),
                    stop);
                flight.pop_front();
                const size_t from = done * kBatch;
                for (size_t index = 0; index < answer.size(); ++index)
                  if (answer[index])
                    present[from + index] = 1;
                answered[done] = static_cast<int>(side);
                any_answer = true;
                ++finished;
                retries = 0;
                if (side == 0)
                {
                  counters.answers[group] += static_cast<int64_t>(answer.size());
                  counters.micros[group] += std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - round).count();
                }
              }
              catch (const std::exception& error)
              {
                connection.reset();
                ++counters.broken;
                {
                  std::lock_guard<std::mutex> lock(shared_mutex);
                  for (auto batch = flight.rbegin(); batch != flight.rend(); ++batch)
                    orphans.push_front(*batch);
                  flight.clear();
                }
                // Not one answer to this check yet: the provider does not answer for this release at all (seen with a
                // few releases while others in the same minute were fine). Signing in again does not help and keeps
                // connections from the file reading next to it, so this connection stops; the check ends with an error.
                const bool silent = !any_answer.load();
                if (silent)
                {
                  std::lock_guard<std::mutex> lock(shared_mutex);
                  if (stuck_id.empty() && !flight_ids_first.empty())
                    stuck_id = flight_ids_first;
                }
                if (silent || std::string(error.what()) == "stopped" || Since(started) > limit_seconds ||
                    ++retries > kRetriesInARow)
                {
                  give_up(error.what());        // a provider that trickles does not get faster: the other side asks
                  return;
                }
              }
            }
          });
    }
  }
  for (std::thread& thread : threads)
    thread.join();
  // packets handed back after every connection of the other side had already finished
  while (!(stop && stop->load()) && Since(started) <= limit_seconds)
  {
    size_t batch = 0;
    {
      std::lock_guard<std::mutex> lock(shared_mutex);
      if (orphans.empty())
        break;
      batch = orphans.front();
      orphans.pop_front();
    }
    bool asked = false;
    for (size_t side = 0; side < sides.size() && !asked; ++side)
    {
      if (alive[side] <= 0)
        continue;
      try
      {
        const ServerConfig& server = *sides[side].servers.front();
        std::unique_ptr<NntpConnection> connection(new NntpConnection(server, plan.timeout, plan.cacert));
        const size_t from = batch * kBatch;
        const size_t to = std::min(ids.size(), from + kBatch);
        const std::vector<std::string> part(ids.begin() + static_cast<long>(from), ids.begin() + static_cast<long>(to));
        connection->StatSend(part);
        const std::vector<bool> answer = connection->StatRead(
            part, started + std::chrono::milliseconds(static_cast<int64_t>(limit_seconds * 1000)),
            stop);
        for (size_t index = 0; index < answer.size(); ++index)
          if (answer[index])
            present[from + index] = 1;
        answered[batch] = static_cast<int>(side);
        ++finished;
        asked = true;
      }
      catch (const std::exception& error)
      {
        ++counters.broken;
        alive[side] = 0;
        if (first_error.empty())
          first_error = error.what();
      }
    }
    if (!asked)
      break;
  }
  if (stop && stop->load())
    return "stopped";
  if (!any_answer.load() && !stuck_id.empty())
    return "no answer at all from the provider (" + first_error + "), first article asked: " + stuck_id;
  if (finished.load() < batches)
    return first_error.empty() ? "took too long" : first_error;
  return "";
}
}  // namespace

CheckResult CheckArticles(const Plan& plan, int connections, double limit_seconds, const std::atomic<bool>* stop)
{
  CheckResult result;
  const auto started = Clock::now();
  std::vector<std::string> ids;
  std::unordered_set<std::string> seen;
  for (const PlanPart& part : plan.parts)
    for (const std::string& id : part.articles)
      if (seen.insert(id).second)
        ids.push_back(id);
  result.total = static_cast<int64_t>(ids.size());
  Side main;
  Side backup;
  for (const ServerConfig& server : plan.servers)
    (server.backup ? backup : main).servers.push_back(&server);
  if (main.servers.empty())
    main.servers.swap(backup.servers);
  main.connections = std::max(1, connections);
  for (const ServerConfig* server : backup.servers)
    backup.connections += std::max(1, server->connections - kBackupKeep);
  backup.connections = std::min(backup.connections, kBackupMost);

  // 1. every article once at the main provider (as in 0.1.1, which worked reliably on Wilfried's provider: the backup
  //    taking part from the start, 0.1.2-0.1.4, came with refused and stalled connections there)
  std::vector<Side> sides{main};
  std::vector<char> present(ids.size(), 0);
  std::vector<int> answered;
  Counters counters;
  result.error = AskSides(plan, sides, ids, present, answered, started, limit_seconds, stop, counters);
  result.first_pass = Since(started);

  // 2. what the main provider does not have, once more at the backup provider
  if (result.error.empty() && !backup.servers.empty())
  {
    std::vector<std::string> missing;
    std::vector<size_t> where;
    for (size_t index = 0; index < ids.size(); ++index)
      if (!present[index])
      {
        missing.push_back(ids[index]);
        where.push_back(index);
      }
    if (!missing.empty())
    {
      Side spare = backup;
      spare.connections = std::min(spare.connections, kBackupMost);
      std::vector<char> extra(missing.size(), 0);
      std::vector<int> unused;
      result.error = AskSides(plan, {spare}, missing, extra, unused, started, limit_seconds, stop, counters);
      for (size_t index = 0; index < missing.size(); ++index)
        if (extra[index])
        {
          present[where[index]] = 1;
          ++result.from_backup;
        }
    }
  }
  for (size_t index = 0; index < ids.size(); ++index)
    if (present[index])
      ++result.present;
  result.opened = counters.opened.load();
  result.broken = counters.broken.load();
  result.signin_ms = counters.opened > 0 ? counters.signin_micros / 1000.0 / counters.opened : 0;
  for (int group = 0; group < 2; ++group)
    result.rate[group] = counters.micros[group] > 0 ? counters.answers[group] * 1e6 / counters.micros[group] : 0;
  result.seconds = Since(started);
  return result;
}

std::string CheckJson(const CheckResult& result)
{
  std::string error;
  for (char value : result.error.substr(0, 400))
    if (value != '"' && value != '\\' && static_cast<unsigned char>(value) >= 32)
      error.push_back(value);
  char text[512];
  std::snprintf(text, sizeof(text),
                "{\"total\": %lld, \"present\": %lld, \"backup\": %lld, \"seconds\": %.2f, \"error\": \"%s\"}\n",
                static_cast<long long>(result.total), static_cast<long long>(result.present),
                static_cast<long long>(result.from_backup), result.seconds, error.c_str());
  return text;
}
}  // namespace engine
