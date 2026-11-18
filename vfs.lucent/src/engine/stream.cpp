#include "stream.h"

#include "common.h"
#include "nntp.h"
#include "transport.h"
#include "yenc.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdio>
#include <cstring>
#include <deque>
#include <list>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace engine
{

namespace
{
constexpr int64_t kSeekLogBytes = 16 * 1024 * 1024;
constexpr int64_t kIndexTailBytes = 8 * 1024 * 1024;
constexpr int64_t kHeadKeepBytes = 64 * 1024 * 1024;  // the first 64 MB of the film are never evicted
constexpr int64_t kTailWarmBytes = 4 * 1024 * 1024;   // fetched (and kept) at the start: the Matroska index
constexpr int64_t kHardPrefetchBytes = 96 * 1024 * 1024;
constexpr int kWorkerMargin = 8;
constexpr double kHedgeSeconds = 4.0;             // an article nobody waits for yet
constexpr double kHedgeWaitedMin = 1.0;           // an article the player waits for: ask the other
constexpr double kHedgeWaitedFactor = 3.0;        //   provider after 3x a normal fetch, 1 to 4 s
constexpr int kHedgeWarmupBatches = 16;           // first learn what a normal fetch takes
constexpr size_t kHedgeQueueMax = 8;
constexpr int64_t kSideDistance = 256 * 1024 * 1024;  // extra readers queue behind the player's read-ahead
// Jump prefetch: where a skip forward lands (30 s, 1 min, 10 min later in the film), two articles are made ready
// with low priority, so the picture is there at once. Needs the running time in the plan ('seconds').
constexpr int kJumpSeconds[] = {30, 60, 600};
constexpr int kJumpArticles = 2;
constexpr int64_t kJumpRefreshSeconds = 10;     // planned again after the film moved on this far
constexpr double kHedgeAcquireSeconds = 2.0;

using ChunkPtr = std::shared_ptr<const std::vector<uint8_t>>;

struct ChunkInfo
{
  ChunkPtr data;
  bool zero = false;
};

struct Job
{
  uint64_t key = 0;
  int part = 0;
  int article = 0;
  int avoid = -1;
  bool hedge = false;
};

struct QueueEntry
{
  int64_t distance = 0;
  uint64_t key = 0;
};

struct QueueOrder
{
  bool operator()(const QueueEntry& left, const QueueEntry& right) const
  {
    if (left.distance != right.distance)
      return left.distance < right.distance;
    return left.key < right.key;
  }
};

uint64_t MakeKey(int part, int article)
{
  return (static_cast<uint64_t>(static_cast<uint32_t>(part)) << 32) |
         static_cast<uint32_t>(article);
}

int PartOf(uint64_t key)
{
  return static_cast<int>(key >> 32);
}

int ArticleOf(uint64_t key)
{
  return static_cast<int>(key & 0xFFFFFFFFu);
}

double Now()
{
  const auto stamp = std::chrono::steady_clock::now().time_since_epoch();
  return std::chrono::duration<double>(stamp).count();
}

class Pool
{
public:
  Pool(const ServerConfig& config, int index)
    : config(config), index_(index), limit_(std::max(1, config.connections))
  {
  }

  ~Pool() { Shutdown(); }

  bool Dead() const { return dead_.load(); }
  int Limit() const { return limit_; }

  bool TryAcquire(std::shared_ptr<NntpConnection>& connection, bool& reserved)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (dead_.load())
      return false;
    if (!idle_.empty())
    {
      connection = std::move(idle_.back());
      idle_.pop_back();
      active_.push_back(connection);
      reserved = false;
      return true;
    }
    if (count_ < limit_)
    {
      ++count_;
      connection.reset();
      reserved = true;
      return true;
    }
    return false;
  }

  bool Connected(std::shared_ptr<NntpConnection> connection)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (dead_.load())
    {
      count_ = std::max(0, count_ - 1);
      return false;
    }
    active_.push_back(std::move(connection));
    return true;
  }

  void ConnectFailed()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    count_ = std::max(0, count_ - 1);
  }

  void MarkDead()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    dead_.store(true);
  }

  void Narrow()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (limit_ > 1)
      --limit_;
  }

  void Release(const std::shared_ptr<NntpConnection>& connection)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    EraseActive(connection.get());
    if (dead_.load())
    {
      count_ = std::max(0, count_ - 1);
      return;
    }
    idle_.push_back(connection);
  }

  void Discard(const std::shared_ptr<NntpConnection>& connection)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    EraseActive(connection.get());
    count_ = std::max(0, count_ - 1);
  }

  void FlushIdle()
  {
    std::vector<std::shared_ptr<NntpConnection>> idle;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      idle.swap(idle_);
      count_ = std::max(0, count_ - static_cast<int>(idle.size()));
    }
    for (auto& connection : idle)
      connection->Abort();
    if (!idle.empty())
      Trace("pool %d flushed %zu idle connection(s)", index_, idle.size());
  }

  void Shutdown()
  {
    std::vector<std::shared_ptr<NntpConnection>> idle;
    std::vector<std::shared_ptr<NntpConnection>> active;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      dead_.store(true);
      idle.swap(idle_);
      active.swap(active_);
      count_ = 0;
    }
    Trace("pool %d shutdown, %zu idle, %zu active", index_, idle.size(), active.size());
    for (auto& connection : idle)
      connection->Abort();
    for (auto& connection : active)
      connection->Abort();
    Trace("pool %d shutdown done", index_);
  }

  const ServerConfig config;
  const int index_;

private:
  void EraseActive(NntpConnection* connection)
  {
    active_.erase(std::remove_if(active_.begin(), active_.end(),
                                 [connection](const std::shared_ptr<NntpConnection>& item)
                                 { return item.get() == connection; }),
                  active_.end());
  }

  std::mutex mutex_;
  std::vector<std::shared_ptr<NntpConnection>> idle_;
  std::vector<std::shared_ptr<NntpConnection>> active_;
  int count_ = 0;
  int limit_;
  std::atomic<bool> dead_{false};
};

struct Slot
{
  enum class State
  {
    Empty,
    Queued,
    Fetching,
    Ready
  };

  State state = State::Empty;
  ChunkPtr data;
  bool zero_filled = false;
  bool counted = false;
  int64_t bytes = 0;
  int attempts = 0;
  int avoid_pool = -1;
  int fetch_pool = -1;
  double fetch_started = 0;
  bool hedged = false;
  bool side_only = false;        // fetched for an extra reader (chapter pictures), not for the player
  std::vector<int> missing_pools;
  int64_t queue_distance = 0;
  std::list<uint64_t>::iterator lru;
};
}  // namespace

class Stream::Impl : public std::enable_shared_from_this<Stream::Impl>
{
public:
  explicit Impl(std::shared_ptr<const Plan> plan)
    : plan_(std::move(plan)), part_sizes_(plan_->parts.size()),
      head_logged_(plan_->parts.size(), 0)
  {
    for (size_t index = 0; index < plan_->parts.size(); ++index)
      part_sizes_[index].store(plan_->parts[index].part_size);
    cache_limit_ = plan_->cache;
  }

  ~Impl() { Close(); }

  int64_t Length() const { return plan_->length; }

  bool Usable() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return started_ && !closing_.load() && !fatal_.load();
  }

  bool Start(std::string& error)
  {
    if (started_)
      return true;
    started_ = true;
    started_at_ = Now();

    for (int index = 0; index < static_cast<int>(plan_->servers.size()); ++index)
      pools_.push_back(std::make_unique<Pool>(plan_->servers[index], index));
    int connections = 0;
    for (const auto& pool : pools_)
      connections += pool->Limit();
    workers_ = std::max(1, std::min(plan_->workers, std::max(1, connections)));
    pipeline_ = plan_->pipeline;
    max_attempts_ = std::max(4, static_cast<int>(pools_.size()) * 2 + 1);
    Trace("stream start: %d worker(s) x %d, %zu pool(s)", workers_, pipeline_, pools_.size());

    std::string pList;
    for (const auto& pool : pools_)
    {
      if (!pList.empty())
        pList += ", ";
      pList += pool->config.label + " " + std::to_string(pool->Limit()) +
               (pool->config.backup ? " backup" : "");
    }

    const auto self = shared_from_this();
    try
    {
      for (int index = 0; index < workers_; ++index)
      {
        ++active_workers_;
        threads_.emplace_back([self]() { self->WorkerLoop(); });
      }
      threads_.emplace_back([self]() { self->HedgeLoop(); });
    }
    catch (const std::exception& exception)
    {
      error = std::string("cannot start the Usenet workers: ") + exception.what();
      SetFatal(error);
      return false;
    }

    if (!EnsurePartSize(0))
    {
      error = "the first article of this release is gone from the provider";
      SetFatal(error);
      return false;
    }

    const int64_t part_size = part_sizes_[0].load();
    const int64_t per_article = std::max<int64_t>(1, part_size);
    const int64_t in_flight = static_cast<int64_t>(workers_) * pipeline_;
    const int64_t window_bytes =
        std::min<int64_t>(kHardPrefetchBytes, per_article * (in_flight + kWorkerMargin));
    cache_limit_ = std::max<int64_t>(plan_->cache, window_bytes + per_article);
    const int64_t cache_articles = std::max<int64_t>(2, cache_limit_ / per_article - 1);
    if (static_cast<int64_t>(workers_) * pipeline_ > cache_articles)
      pipeline_ = std::max(1, static_cast<int>(cache_articles / workers_));

    char cache_note[96] = {};
    if (cache_limit_ > plan_->cache)
      std::snprintf(cache_note, sizeof(cache_note), " -> %.0f MB (%.2f MB articles)",
                    cache_limit_ / 1048576.0, part_size / 1048576.0);
    Log(LogLevel::Info,
        "stream: %d worker(s) x %d on %d provider(s) [%s], prefetch %.0f MB, cache %.0f MB%s, "
        "timeout %ds",
        workers_, pipeline_, static_cast<int>(pools_.size()), pList.c_str(),
        plan_->prefetch / 1048576.0, plan_->cache / 1048576.0, cache_note, plan_->timeout);

    ScheduleAhead(0, 0, Lookahead(0, plan_->length));
    WarmTail();
    return true;
  }

  void Close()
  {
    std::call_once(close_once_, [this]() { DoClose(); });
  }

  void PrimeFirstArticle(std::vector<uint8_t> data)
  {
    PrimeArticle(0, std::move(data));
  }

  void PrimeArticle(int index, std::vector<uint8_t> data)
  {
    if (index < 0 || data.empty() || plan_->parts.empty() ||
        static_cast<size_t>(index) >= plan_->parts[0].articles.size())
      return;
    const uint64_t key = MakeKey(0, index);
    auto chunk = std::make_shared<std::vector<uint8_t>>(std::move(data));
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = slots_.find(key);
    if (found != slots_.end() && found->second.state == Slot::State::Ready)
      return;
    if (!stored_keys_.insert(key).second)
      ++refetches_;
    Slot& slot = slots_[key];
    slot.state = Slot::State::Ready;
    slot.data = chunk;
    slot.bytes = static_cast<int64_t>(chunk->size());
    slot.counted = true;
    if (index == 0)
      part_sizes_[0].store(slot.bytes);
    lru_.push_front(key);
    slot.lru = lru_.begin();
    cache_bytes_ += slot.bytes;
    bytes_decoded_ += slot.bytes;
  }

  int64_t Read(int64_t offset, uint8_t* dest, size_t size, bool lead)
  {
    if (!started_ || closing_.load() || fatal_.load())
      return -1;
    if (offset < 0 || offset >= plan_->length)
      return 0;
    if (!lead)
      return SideRead(offset, dest, size);

    const bool tail_read = offset >= plan_->length - kIndexTailBytes;
    const int64_t previous = last_read_.load();
    bool seek = false;
    if (!tail_read && previous >= 0 && std::llabs(offset - previous) > kSeekLogBytes)
      seek = true;
    if (!tail_read)
      last_read_.store(offset);
    if (seek)
    {
      Log(LogLevel::Info, "read jump to %.2f GB (%.0f MB from the previous read)",
          offset / 1073741824.0, (offset - previous) / 1048576.0);
      CancelFar(offset);
      seek_started_.store(Now());
      seek_pending_.store(true);
    }
    read_offset_.store(offset);

    size_t done = 0;
    int64_t position = offset;
    while (done < size && position < plan_->length)
    {
      int64_t local = 0;
      const int part_index = Locate(position, local);
      if (part_index < 0)
        break;
      const PlanPart& part = plan_->parts[part_index];
      const int64_t end_of_part = part.media_start + part.length;
      const int64_t remaining = std::min(plan_->length - position, end_of_part - position);
      const int64_t room = std::min<int64_t>(static_cast<int64_t>(size - done), remaining);
      if (room <= 0)
      {
        position = end_of_part;
        continue;
      }
      if (!EnsurePartSize(part_index))
      {
        SetFatal("cannot determine the article size for this part");
        return done > 0 ? static_cast<int64_t>(done) : -1;
      }
      const int64_t part_size = part_sizes_[part_index].load();
      const int64_t volume_position = part.skip + (position - part.media_start);
      const int64_t index = volume_position / part_size;
      if (index >= static_cast<int64_t>(part.articles.size()))
      {
        position = end_of_part;
        continue;
      }
      if (done == 0 && !tail_read)
        JumpAhead(position);                    // after EnsurePartSize: the size of this part is known now
      int window = Lookahead(part_index, end_of_part - position);
      if (articles_attempted_.load() < 4)
        window = std::min(window, std::max(2, std::min(8, workers_)));
      ScheduleAhead(part_index, static_cast<int>(index) + 1, window);
      ScheduleNextPart(part_index, end_of_part - position);
      const ChunkInfo chunk = GetChunk(part_index, static_cast<int>(index));
      if (!chunk.data)
      {
        if (closing_.load() || fatal_.load())
          return done > 0 ? static_cast<int64_t>(done) : -1;
        continue;
      }
      if (!first_read_logged_.exchange(true))
      {
        const int64_t centiseconds = static_cast<int64_t>((Now() - started_at_) * 100.0);
        first_read_cs_.store(centiseconds);
        Log(LogLevel::Info, "first byte after %.1f s", centiseconds / 100.0);
      }
      if (seek_pending_.exchange(false))
        Log(LogLevel::Info, "seek to %.2f GB: first byte in %.1f s", offset / 1073741824.0,
            Now() - seek_started_.load());
      const int64_t inside = volume_position - index * part_size;
      if (inside >= static_cast<int64_t>(chunk.data->size()))
      {
        int64_t next = part.media_start + (index + 1) * part_size - part.skip;
        if (index + 1 >= static_cast<int64_t>(part.articles.size()))
          next = end_of_part;
        if (next <= position)
          next = position + 1;
        position = std::min(next, end_of_part);
        continue;
      }
      const int64_t take =
          std::min<int64_t>(static_cast<int64_t>(chunk.data->size()) - inside, room);
      std::memcpy(dest + done, chunk.data->data() + inside, static_cast<size_t>(take));
      done += static_cast<size_t>(take);
      position += take;
      read_offset_.store(position);
    }
    if (done > 0)
      read_bytes_.fetch_add(static_cast<int64_t>(done));
    return static_cast<int64_t>(done);
  }

  // A second reader: only the articles it needs, fetched urgently, nothing scheduled ahead, and the
  // player's read position (read-ahead, cancelling, eviction order) is left alone.
  int64_t SideRead(int64_t offset, uint8_t* dest, size_t size)
  {
    size_t done = 0;
    int64_t position = offset;
    while (done < size && position < plan_->length)
    {
      int64_t local = 0;
      const int part_index = Locate(position, local);
      if (part_index < 0)
        break;
      const PlanPart& part = plan_->parts[part_index];
      const int64_t end_of_part = part.media_start + part.length;
      const int64_t room = std::min<int64_t>(static_cast<int64_t>(size - done),
                                             std::min(plan_->length, end_of_part) - position);
      if (room <= 0)
      {
        position = end_of_part;
        continue;
      }
      if (!EnsurePartSize(part_index))
        return done > 0 ? static_cast<int64_t>(done) : -1;
      const int64_t part_size = part_sizes_[part_index].load();
      const int64_t volume_position = part.skip + (position - part.media_start);
      const int64_t index = volume_position / part_size;
      if (index >= static_cast<int64_t>(part.articles.size()))
      {
        position = end_of_part;
        continue;
      }
      const ChunkInfo chunk = GetChunk(part_index, static_cast<int>(index), true);
      if (!chunk.data)
      {
        if (closing_.load() || fatal_.load())
          return done > 0 ? static_cast<int64_t>(done) : -1;
        continue;
      }
      const int64_t inside = volume_position - index * part_size;
      if (inside >= static_cast<int64_t>(chunk.data->size()))
      {
        int64_t next = part.media_start + (index + 1) * part_size - part.skip;
        if (index + 1 >= static_cast<int64_t>(part.articles.size()))
          next = end_of_part;
        if (next <= position)
          next = position + 1;
        position = std::min(next, end_of_part);
        continue;
      }
      const int64_t take =
          std::min<int64_t>(static_cast<int64_t>(chunk.data->size()) - inside, room);
      std::memcpy(dest + done, chunk.data->data() + inside, static_cast<size_t>(take));
      done += static_cast<size_t>(take);
      position += take;
    }
    if (done > 0)
      side_bytes_.fetch_add(static_cast<int64_t>(done));
    return static_cast<int64_t>(done);
  }

  StreamStats Stats() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    StreamStats stats;
    stats.articles = articles_attempted_.load();
    stats.missing = missing_;
    stats.bytes = bytes_decoded_;
    stats.article_seconds = article_seconds_;
    stats.providers = provider_stats_;
    return stats;
  }

  std::string LastError() const
  {
    std::lock_guard<std::mutex> lock(mutex_);
    return last_error_;
  }

  void LogTotals(const char* when) { LogStats(when); }

  std::string TakeReport()
  {
    std::lock_guard<std::mutex> lock(mutex_);
    const double now = Now();
    std::string text;
    for (const auto& item : provider_stats_)
    {
      ProviderStat current = item.second;
      const auto running = busy_since_.find(item.first);
      if (running != busy_since_.end() && busy_count_[item.first] > 0)
        current.busy_seconds += now - running->second;
      ProviderStat& before = reported_[item.first];
      const long long bytes = current.bytes - before.bytes;
      const long long articles = current.articles - before.articles;
      const long long missing = current.missing - before.missing;
      const long long errors = current.errors - before.errors;
      const long long replies = current.replies - before.replies;
      const long long reply_ms =
          static_cast<long long>((current.reply_seconds - before.reply_seconds) * 1000.0);
      const long long busy_ms =
          static_cast<long long>((current.busy_seconds - before.busy_seconds) * 1000.0);
      before = current;
      if (bytes <= 0 && articles <= 0 && missing <= 0 && errors <= 0)
        continue;
      std::string label;
      for (char value : item.first)
        if ((value >= 'a' && value <= 'z') || (value >= 'A' && value <= 'Z') ||
            (value >= '0' && value <= '9') || value == '-' || value == '_')
          label += value;
      if (label.empty())
        continue;
      char line[256] = {};
      std::snprintf(line, sizeof(line), "provider %s %lld %lld %lld %lld %lld %lld %lld\n",
                    label.c_str(), bytes, articles, missing, errors, replies, reply_ms,
                    busy_ms < 0 ? 0LL : busy_ms);
      text += line;
    }
    return text.empty() ? text : "lucent-stats 1\n" + text;
  }

private:
  // How long an article the player is waiting for may take before the other provider is asked
  // too: a few times what a fetch normally takes on this connection, between 1 and 4 seconds.
  double WaitedHedgeLimitLocked() const
  {
    if (batch_samples_ < kHedgeWarmupBatches)
      return kHedgeSeconds;
    return std::min(kHedgeSeconds, std::max(kHedgeWaitedMin, batch_ewma_ * kHedgeWaitedFactor));
  }

  void HedgeLoop()
  {
    Trace("hedge thread start");
    while (!closing_.load())
    {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      if (closing_.load() || fatal_.load())
        break;
      if (pools_.size() < 2)
        continue;
      int live = 0;
      for (const auto& pool : pools_)
        if (!pool->Dead())
          ++live;
      if (live < 2)
        continue;
      bool queued = false;
      {
        std::lock_guard<std::mutex> lock(mutex_);
        const double now = Now();
        const double waited_limit = WaitedHedgeLimitLocked();
        for (auto& item : slots_)
        {
          Slot& slot = item.second;
          if (slot.state != Slot::State::Fetching || slot.hedged ||
              slot.fetch_started <= 0.0)
            continue;
          const bool waited = waiting_.count(item.first) > 0;
          if (now - slot.fetch_started < (waited ? waited_limit : kHedgeSeconds))
            continue;
          if (hedges_.size() >= kHedgeQueueMax)
            break;
          slot.hedged = true;
          ++hedges_total_;
          if (waited)
            ++hedges_waited_;
          Job job;
          job.key = item.first;
          job.part = PartOf(item.first);
          job.article = ArticleOf(item.first);
          job.avoid = slot.fetch_pool;
          job.hedge = true;
          hedges_.push_back(job);
          queued = true;
          if (hedge_logs_ < 20)
          {
            ++hedge_logs_;
            Log(LogLevel::Info, "hedge %d:%d after %.1f s%s", job.part, job.article,
                now - slot.fetch_started, waited ? " (the player waits for it)" : "");
          }
        }
      }
      if (queued)
        job_cv_.notify_all();
    }
    Trace("hedge thread exit");
  }

  void WorkerLoop()
  {
    const int worker_id = next_worker_id_.fetch_add(1);
    Trace("worker %d start", worker_id);
    try
    {
      for (;;)
      {
        std::vector<Job> jobs;
        {
          std::unique_lock<std::mutex> lock(mutex_);
          job_cv_.wait(lock,
                       [this]()
                       {
                         return closing_.load() || fatal_.load() || !queue_.empty() ||
                                !hedges_.empty();
                       });
          if (closing_.load() || fatal_.load())
            break;
          jobs = TakeBatchLocked();
        }
        if (!jobs.empty())
          FetchBatch(jobs);
        if (closing_.load() || fatal_.load())
          break;
      }
    }
    catch (const std::exception& exception)
    {
      Trace("worker %d exception: %s", worker_id, exception.what());
      Log(LogLevel::Error, "worker stopped: %s", exception.what());
    }
    catch (...)
    {
      Trace("worker %d unknown exception", worker_id);
      Log(LogLevel::Error, "worker stopped");
    }
    Trace("worker %d exit", worker_id);
    --active_workers_;
  }

  std::vector<Job> TakeBatchLocked()
  {
    std::vector<Job> jobs;
    while (!hedges_.empty())
    {
      Job job = hedges_.front();
      hedges_.pop_front();
      const auto found = slots_.find(job.key);
      if (found == slots_.end() || found->second.state != Slot::State::Fetching ||
          !found->second.hedged)
        continue;
      job.hedge = true;
      jobs.push_back(job);
      return jobs;
    }
    if (queue_.empty())
      return jobs;
    int avoid = -2;
    auto entry = queue_.begin();
    while (entry != queue_.end() && static_cast<int>(jobs.size()) < pipeline_)
    {
      const auto found = slots_.find(entry->key);
      if (found == slots_.end() || found->second.state != Slot::State::Queued)
      {
        entry = queue_.erase(entry);
        continue;
      }
      Slot& slot = found->second;
      if (avoid == -2)
        avoid = slot.avoid_pool;
      if (slot.avoid_pool != avoid)
      {
        ++entry;
        continue;
      }
      // A cold part probe can take seconds; never let it hold back the
      // articles of the part that is being read right now.
      const bool head_probe =
          ArticleOf(entry->key) == 0 && part_sizes_[PartOf(entry->key)].load() <= 0;
      if ((head_probe || entry->key == tail_key_) && !jobs.empty())
        break;
      Job job;
      job.key = entry->key;
      job.part = PartOf(entry->key);
      job.article = ArticleOf(entry->key);
      job.avoid = avoid;
      slot.state = Slot::State::Fetching;
      ++slot.attempts;
      slot.fetch_started = Now();
      slot.fetch_pool = -1;
      if (!slot.counted)
      {
        slot.counted = true;
        ++articles_attempted_;
      }
      entry = queue_.erase(entry);
      jobs.push_back(job);
      if (head_probe || job.key == tail_key_)
        break;
    }
    return jobs;
  }

  bool Acquire(int avoid, std::shared_ptr<NntpConnection>& connection, Pool*& pool,
               std::string& error, double limit_seconds = 0.0)
  {
    const double started = Now();
    for (;;)
    {
      if (closing_.load())
      {
        error = "closing";
        return false;
      }
      if (limit_seconds > 0 && Now() - started >= limit_seconds)
      {
        error = "no free connection for the hedge";
        return false;
      }
      bool any = false;
      bool attempted = false;
      for (const auto& owner : pools_)
      {
        Pool* candidate = owner.get();
        if (candidate->index_ == avoid || candidate->Dead())
          continue;
        any = true;
        std::shared_ptr<NntpConnection> found;
        bool reserved = false;
        if (!candidate->TryAcquire(found, reserved))
          continue;
        attempted = true;
        if (found)
        {
          connection = std::move(found);
          pool = candidate;
          return true;
        }
        try
        {
          auto created =
              std::make_shared<NntpConnection>(candidate->config, plan_->timeout, plan_->cacert);
          if (!candidate->Connected(created))
          {
            error = "the provider is closing";
          }
          else
          {
            connection = std::move(created);
            pool = candidate;
            return true;
          }
        }
        catch (const NntpTooManyError& exception)
        {
          candidate->ConnectFailed();
          candidate->Narrow();
          error = exception.what();
        }
        catch (const NntpAuthError& exception)
        {
          candidate->ConnectFailed();
          candidate->MarkDead();
          error = exception.what();
        }
        catch (const std::exception& exception)
        {
          candidate->ConnectFailed();
          error = exception.what();
          LogWarning("provider", candidate->config.label + ": " + error);
        }
      }
      if (!any)
      {
        if (error.empty())
          error = "no Usenet provider is available";
        return false;
      }
      if (attempted && !error.empty())
        return false;
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
  }

  void FetchBatch(const std::vector<Job>& jobs)
  {
    const bool hedge = !jobs.empty() && jobs.front().hedge;
    std::vector<std::string> ids;
    ids.reserve(jobs.size());
    for (const Job& job : jobs)
      ids.push_back(plan_->parts[job.part].articles[job.article]);

    std::shared_ptr<NntpConnection> connection;
    Pool* pool = nullptr;
    std::string error;
    if (!Acquire(jobs.front().avoid, connection, pool, error,
                 hedge ? kHedgeAcquireSeconds : 0.0))
    {
      if (hedge)
      {
        Trace("hedge %d:%d skipped: %s", jobs.front().part, jobs.front().article,
              error.c_str());
        return;
      }
      for (const Job& job : jobs)
        HandleFailure(job, -1, false, error);
      return;
    }
    if (!hedge)
    {
      std::lock_guard<std::mutex> lock(mutex_);
      for (const Job& job : jobs)
      {
        const auto found = slots_.find(job.key);
        if (found != slots_.end() && found->second.state == Slot::State::Fetching)
          found->second.fetch_pool = pool->index_;
      }
    }

    const double started = Now();
    std::vector<NntpReply> replies;
    const std::string busy_label = pool->config.label;
    BusyBegin(busy_label);
    try
    {
      replies = connection->BodyMany(ids);
    }
    catch (const NntpTooManyError& exception)
    {
      pool->Discard(connection);
      BusyEnd(busy_label, !closing_.load());
      if (closing_.load())
        return;
      pool->Narrow();
      LogWarning("fetch", exception.what());
      const int avoid = pools_.size() > 1 ? pool->index_ : -1;
      if (!hedge)
        for (const Job& job : jobs)
          HandleFailure(job, avoid, false, exception.what());
      return;
    }
    catch (const NntpAuthError& exception)
    {
      pool->Discard(connection);
      BusyEnd(busy_label, !closing_.load());
      if (closing_.load())
        return;
      pool->MarkDead();
      LogWarning("fetch", exception.what());
      if (!hedge)
        for (const Job& job : jobs)
          HandleFailure(job, -1, false, exception.what());
      return;
    }
    catch (const std::exception& exception)
    {
      pool->Discard(connection);
      BusyEnd(busy_label, !closing_.load());
      if (closing_.load())
        return;
      const std::string reason = exception.what();
      if (!hedge)
      {
        pool->FlushIdle();
        if (reason.find("closed") != std::string::npos)
          std::this_thread::sleep_for(std::chrono::milliseconds(200));
      }
      LogWarning("fetch", pool->config.label + ": " + reason);
      if (!hedge)
        for (const Job& job : jobs)
          HandleFailure(job, -1, false, reason);
      return;
    }
    const double elapsed = Now() - started;
    const double first_reply = connection->FirstReplySeconds();
    pool->Release(connection);
    RecordBatch(pool, elapsed, replies, first_reply);
    BusyEnd(busy_label, false);

    if (replies.size() != jobs.size())
    {
      if (!hedge)
        for (const Job& job : jobs)
          HandleFailure(job, -1, false, "short reply");
      return;
    }
    double decode_seconds = 0;
    int64_t decoded_articles = 0;
    for (size_t index = 0; index < jobs.size(); ++index)
    {
      const Job& job = jobs[index];
      const NntpReply& reply = replies[index];
      if (reply.missing)
      {
        if (!hedge)
          HandleFailure(job, pool->index_, true, "article is missing");
        continue;
      }
      if (reply.body.empty())
      {
        if (!hedge)
          HandleFailure(job, -1, false, "empty article");
        continue;
      }
      const double decode_started = Now();
      YencResult decoded = YencDecode(reply.body);
      decode_seconds += Now() - decode_started;
      if (!decoded.ok || !decoded.crc_ok)
      {
        const int avoid = pools_.size() > 1 ? pool->index_ : -1;
        const std::string reason =
            decoded.ok ? "bad checksum (" + std::to_string(decoded.data.size()) +
                             " decoded, " +
                             std::to_string(decoded.part_end - decoded.begin) + " expected)"
                       : "bad yEnc";
        if (!hedge)
          HandleFailure(job, avoid, false, reason);
        continue;
      }
      ++decoded_articles;
      StoreDecoded(job, std::move(decoded.data));
    }
    if (decoded_articles > 0)
    {
      std::lock_guard<std::mutex> lock(mutex_);
      decode_seconds_ += decode_seconds;
      decoded_articles_ += decoded_articles;
    }
  }

  void BusyBegin(const std::string& label)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (busy_count_[label]++ == 0)
      busy_since_[label] = Now();
  }

  void BusyEnd(const std::string& label, bool error)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    ProviderStat& stat = provider_stats_[label];
    if (error)
      ++stat.errors;
    int& count = busy_count_[label];
    if (count > 0 && --count == 0)
      stat.busy_seconds += Now() - busy_since_[label];
  }

  void RecordBatch(Pool* pool, double elapsed, const std::vector<NntpReply>& replies,
                   double first_reply)
  {
    int64_t delivered = 0;
    int64_t bytes = 0;
    int64_t missing = 0;
    for (const NntpReply& reply : replies)
    {
      if (!reply.missing)
      {
        ++delivered;
        bytes += static_cast<int64_t>(reply.body.size());
      }
      else
        ++missing;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    ProviderStat& stat = provider_stats_[pool->config.label];
    stat.articles += delivered;
    stat.bytes += bytes;
    stat.seconds += elapsed;
    stat.missing += missing;
    if (!replies.empty())
    {
      ++stat.replies;
      stat.reply_seconds += first_reply;
    }
    article_seconds_ += elapsed;
    if (delivered > 0)
    {
      batch_ewma_ = batch_samples_ == 0 ? elapsed : batch_ewma_ * 0.9 + elapsed * 0.1;
      ++batch_samples_;
    }
  }

  void LogWarning(const char* where, const std::string& message)
  {
    if (fetch_error_logs_.fetch_add(1) < 12)
      Log(LogLevel::Warning, "%s: %s", where, message.c_str());
  }

  void StoreDecoded(const Job& job, std::vector<uint8_t> data)
  {
    auto chunk = std::make_shared<std::vector<uint8_t>>(std::move(data));
    std::lock_guard<std::mutex> lock(mutex_);
    const auto found = slots_.find(job.key);
    if (found != slots_.end() && found->second.state == Slot::State::Ready)
      return;
    if (!stored_keys_.insert(job.key).second)
      ++refetches_;
    if (found == slots_.end())
      return;
    Slot& slot = found->second;
    if (slot.state == Slot::State::Ready)
      return;
    slot.state = Slot::State::Ready;
    slot.zero_filled = false;
    slot.data = chunk;
    slot.bytes = static_cast<int64_t>(chunk->size());
    missing_run_ = 0;
    bytes_decoded_ += slot.bytes;
    lru_.push_front(job.key);
    slot.lru = lru_.begin();
    cache_bytes_ += slot.bytes;
    if (job.article == 0)
    {
      const int part_index = PartOf(job.key);
      if (part_sizes_[part_index].load() <= 0)
      {
        part_sizes_[part_index].store(slot.bytes);
        Trace("part %d size learned: %lld bytes", plan_->parts[part_index].id,
              static_cast<long long>(slot.bytes));
        const int64_t read = read_offset_.load();
        if (plan_->parts[part_index].media_start <= read + plan_->prefetch * 2)
          SchedulePartHeadLocked(part_index);
      }
    }
    EvictLocked();
    cv_.notify_all();
  }

  void HandleFailure(const Job& job, int pool_index, bool missing, const std::string& reason)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_.load() || fatal_.load())
      return;
    const auto found = slots_.find(job.key);
    if (found == slots_.end())
    {
      Trace("failure %d:%d has no slot", job.part, job.article);
      return;
    }
    Slot& slot = found->second;
    if (slot.state != Slot::State::Fetching)
    {
      Trace("failure %d:%d slot state %d", job.part, job.article,
            static_cast<int>(slot.state));
      return;
    }
    const auto& articles = plan_->parts[job.part].articles;
    const bool critical = job.article == 0 ||
                          static_cast<size_t>(job.article) + 1 == articles.size();
    const int limit = max_attempts_ + (critical ? 2 : 0);
    if (missing)
    {
      if (pool_index >= 0 &&
          std::find(slot.missing_pools.begin(), slot.missing_pools.end(), pool_index) ==
              slot.missing_pools.end())
        slot.missing_pools.push_back(pool_index);
      int untried = 0;
      for (const auto& pool : pools_)
      {
        if (pool->Dead())
          continue;
        if (std::find(slot.missing_pools.begin(), slot.missing_pools.end(), pool->index_) ==
            slot.missing_pools.end())
          ++untried;
      }
      if (untried == 0 || slot.attempts >= limit)
      {
        FailLocked(job, reason);
        return;
      }
      slot.state = Slot::State::Queued;
      slot.avoid_pool = pool_index;
      slot.queue_distance = 0;
      queue_.insert({0, job.key});
    }
    else
    {
      if (slot.attempts >= limit)
      {
        FailLocked(job, reason);
        return;
      }
      slot.state = Slot::State::Queued;
      slot.avoid_pool = pool_index;
      slot.queue_distance = 0;
      queue_.insert({0, job.key});
    }
    job_cv_.notify_all();
  }

  void FailLocked(const Job& job, const std::string& reason)
  {
    const auto found = slots_.find(job.key);
    if (found == slots_.end())
      return;
    Slot& slot = found->second;
    if (slot.state == Slot::State::Ready)
      return;
    const PlanPart& part = plan_->parts[job.part];
    const int64_t expected = part.part_size > 0 ? part.part_size : 1;
    auto zeros = std::make_shared<std::vector<uint8_t>>(static_cast<size_t>(expected), 0);
    slot.state = Slot::State::Ready;
    slot.zero_filled = true;
    slot.data = zeros;
    slot.bytes = expected;
    lru_.push_front(job.key);
    slot.lru = lru_.begin();
    cache_bytes_ += expected;
    ++missing_;
    ++missing_run_;
    if (missing_run_ > longest_run_)
      longest_run_ = missing_run_;
    if (missing_ <= 3 || missing_ % 25 == 0)
      Log(LogLevel::Warning,
          "article %d of part %d is missing (%s) - zero-filled (%lld so far)", job.article,
          part.id, reason.c_str(), static_cast<long long>(missing_));
    if (static_cast<size_t>(job.article) + 1 == part.articles.size())
      Log(LogLevel::Warning,
          "the last article of this file is missing - the end of the file was zero-filled "
          "and seeking can freeze");
    if (!plan_->hole_filling)
    {
      SetFatalLocked("this release has missing articles and gap filling is off");
    }
    else if (plan_->hole_consecutive > 0 && missing_run_ > plan_->hole_consecutive)
    {
      SetFatalLocked(std::to_string(static_cast<long long>(missing_run_)) +
                     " articles in a row are missing - the film would freeze");
    }
    else if (plan_->hole_percent > 0 && articles_attempted_.load() >= 20)
    {
      const double ratio = 100.0 * missing_ / static_cast<double>(articles_attempted_.load());
      if (ratio > plan_->hole_percent)
        SetFatalLocked("missing articles are " + std::to_string(ratio) + "% of the release");
    }
    EvictLocked();
    cv_.notify_all();
    job_cv_.notify_all();
  }

  void SetFatal(const std::string& message)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    SetFatalLocked(message);
  }

  void SetFatalLocked(const std::string& message)
  {
    if (!fatal_.exchange(true))
    {
      fatal_message_ = message;
      last_error_ = message;
      Log(LogLevel::Error, "%s", message.c_str());
    }
    cv_.notify_all();
    job_cv_.notify_all();
  }

  int64_t SlotStart(uint64_t key) const
  {
    const int part_index = PartOf(key);
    const int article = ArticleOf(key);
    const int64_t part_size = part_sizes_[part_index].load();
    const PlanPart& part = plan_->parts[part_index];
    if (part_size <= 0)
      return part.media_start;
    const int64_t start =
        part.media_start + static_cast<int64_t>(article) * part_size - part.skip;
    return start < 0 ? 0 : start;
  }

  // At most a third of the cache is kept for the film's start, so read-ahead always has room.
  int64_t HeadKeep() const { return std::min<int64_t>(kHeadKeepBytes, CacheLimit() / 3); }

  int64_t CacheLimit() const
  {
    return cache_limit_ > 0 ? cache_limit_ : plan_->cache;
  }

  // Articles fetched only for an extra reader go first, once read: they never cost the player its
  // read-ahead. One in the player's window ahead stays, the player will want it soon.
  bool EvictSideLocked(int64_t read)
  {
    for (auto entry = lru_.end(); entry != lru_.begin();)
    {
      --entry;
      const uint64_t key = *entry;
      if (pinned_.count(key) != 0)
        continue;
      const auto found = slots_.find(key);
      if (found == slots_.end() || found->second.state != Slot::State::Ready ||
          !found->second.side_only)
        continue;
      const int64_t start = SlotStart(key);
      if (start < HeadKeep() || (start + found->second.bytes > read && start < read + kHardPrefetchBytes))
        continue;
      cache_bytes_ -= found->second.bytes;
      slots_.erase(found);
      lru_.erase(entry);
      ++evictions_;
      return true;
    }
    return false;
  }

  void EvictLocked()
  {
    while (cache_bytes_ > CacheLimit() && !lru_.empty())
    {
      const int64_t read = read_offset_.load();
      if (EvictSideLocked(read))
        continue;
      bool removed = false;
      auto entry = lru_.end();
      while (entry != lru_.begin())
      {
        --entry;
        const uint64_t key = *entry;
        if (pinned_.count(key) != 0)
          continue;
        const auto found = slots_.find(key);
        if (found == slots_.end() || found->second.state != Slot::State::Ready)
        {
          entry = lru_.erase(entry);
          continue;
        }
        if (SlotStart(key) < HeadKeep())
          continue;                             // the film's start stays: "back to the beginning" is instant
        if (SlotStart(key) + found->second.bytes > read)
          continue;
        cache_bytes_ -= found->second.bytes;
        slots_.erase(found);
        lru_.erase(entry);
        ++evictions_;
        removed = true;
        break;
      }
      if (!removed)
      {
        auto victim = lru_.end();
        int64_t farthest = -1;
        for (auto candidate = lru_.begin(); candidate != lru_.end(); ++candidate)
        {
          const uint64_t key = *candidate;
          if (pinned_.count(key) != 0)
            continue;
          const auto found = slots_.find(key);
          if (found == slots_.end() || found->second.state != Slot::State::Ready)
            continue;
          const int64_t start = SlotStart(key);
          if (start < HeadKeep())
            continue;
          if (start > farthest)
          {
            farthest = start;
            victim = candidate;
          }
        }
        if (victim == lru_.end())
          break;
        const auto found = slots_.find(*victim);
        if (found == slots_.end())
        {
          lru_.erase(victim);
          continue;
        }
        cache_bytes_ -= found->second.bytes;
        slots_.erase(found);
        lru_.erase(victim);
        ++evictions_;
      }
    }
  }

  void TouchLocked(uint64_t key, Slot& slot)
  {
    if (slot.bytes <= 0)
      return;
    lru_.erase(slot.lru);
    lru_.push_front(key);
    slot.lru = lru_.begin();
  }

  void EnqueueLocked(uint64_t key, int64_t distance)
  {
    auto result = slots_.emplace(key, Slot{});
    Slot& slot = result.first->second;
    if (!result.second)
    {
      if (slot.state == Slot::State::Ready || slot.state == Slot::State::Fetching ||
          slot.state == Slot::State::Queued)
        return;
    }
    slot.state = Slot::State::Queued;
    slot.queue_distance = distance;
    queue_.insert({distance, key});
  }

  void PromoteLocked(uint64_t key, int64_t distance)
  {
    const auto found = slots_.find(key);
    if (found == slots_.end() || found->second.state != Slot::State::Queued)
      return;
    Slot& slot = found->second;
    if (slot.queue_distance == distance)
      return;
    queue_.erase({slot.queue_distance, key});
    slot.queue_distance = distance;
    queue_.insert({distance, key});
  }

  void ScheduleAhead(int part_index, int start, int count)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_.load() || fatal_.load())
      return;
    const PlanPart& part = plan_->parts[part_index];
    const int64_t part_size = part_sizes_[part_index].load();
    const int64_t read = read_offset_.load();
    const int begin = std::max(0, start);
    const int end =
        static_cast<int>(std::min<int64_t>(static_cast<int64_t>(begin) + count,
                                           static_cast<int64_t>(part.articles.size())));
    bool queued = false;
    for (int index = begin; index < end; ++index)
    {
      const uint64_t key = MakeKey(part_index, index);
      if (slots_.find(key) != slots_.end())
        continue;
      int64_t offset =
          part_size > 0 ? part.media_start + static_cast<int64_t>(index) * part_size - part.skip
                        : part.media_start;
      if (offset < 0)
        offset = 0;
      EnqueueLocked(key, offset > read ? offset - read : 0);
      queued = true;
    }
    if (queued)
      job_cv_.notify_all();
  }

  void JumpAhead(int64_t position)
  {
    if (plan_->seconds <= 0 || plan_->length <= 0)
      return;
    const int64_t rate = plan_->length / plan_->seconds;
    if (rate <= 0)
      return;
    const int64_t mark = jump_mark_.load();
    if (mark >= 0 && std::llabs(position - mark) < rate * kJumpRefreshSeconds)
      return;
    jump_mark_.store(position);
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_.load() || fatal_.load())
      return;
    bool queued = false;
    for (const int seconds : kJumpSeconds)
    {
      const int64_t target = position + rate * seconds;
      if (target >= plan_->length - kIndexTailBytes)
        break;
      int64_t local = 0;
      const int part_index = Locate(target, local);
      if (part_index < 0)
        continue;
      const PlanPart& part = plan_->parts[part_index];
      const int64_t part_size = part_sizes_[part_index].load();
      if (part_size <= 0)
        continue;                               // that part's article size is not known yet: no extra fetch for it
      const int64_t first = (part.skip + (target - part.media_start)) / part_size;
      for (int64_t index = first; index < first + kJumpArticles; ++index)
      {
        if (index < 0 || index >= static_cast<int64_t>(part.articles.size()))
          break;
        const uint64_t key = MakeKey(part_index, static_cast<int>(index));
        if (slots_.find(key) != slots_.end())
          continue;
        EnqueueLocked(key, kSideDistance);
        slots_.find(key)->second.side_only = true;
        queued = true;
        ++jump_articles_;
      }
    }
    if (queued)
      job_cv_.notify_all();
  }

  void ScheduleNextPart(int part_index, int64_t remaining)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_.load() || fatal_.load())
      return;
    const int next = part_index + 1;
    if (next >= static_cast<int>(plan_->parts.size()))
      return;
    const int64_t trigger =
        std::min<int64_t>(plan_->prefetch, std::max<int64_t>(4 * 1024 * 1024, CacheLimit() / 4));
    if (remaining > trigger)
      return;
    if (!head_logged_[next])
    {
      head_logged_[next] = 1;
      Log(LogLevel::Info, "part %d: head prefetch started %.0f MB before the boundary",
          plan_->parts[next].id, remaining / 1048576.0);
    }
    SchedulePartHeadLocked(next);
  }

  void SchedulePartHeadLocked(int part_index)
  {
    const PlanPart& part = plan_->parts[part_index];
    if (part.length <= 0 || part.articles.empty())
      return;
    const int64_t part_size = part_sizes_[part_index].load();
    if (part_size <= 0)
    {
      EnqueueLocked(MakeKey(part_index, 0), 0);
      job_cv_.notify_all();
      return;
    }
    const int64_t head_bytes = std::min<int64_t>(
        std::min<int64_t>(CacheLimit() / 5, plan_->prefetch / 2), kHardPrefetchBytes);
    int count = static_cast<int>((head_bytes + part_size - 1) / part_size);
    count = std::min(count, static_cast<int>(part.articles.size()));
    count = std::min(count, std::max(8, workers_ * pipeline_));
    if (count <= 0)
      return;
    const int64_t read = read_offset_.load();
    for (int index = 0; index < count; ++index)
    {
      int64_t offset = part.media_start + static_cast<int64_t>(index) * part_size - part.skip;
      if (offset < 0)
        offset = 0;
      EnqueueLocked(MakeKey(part_index, index), offset > read ? offset - read : 0);
    }
    job_cv_.notify_all();
  }

  int Lookahead(int part_index, int64_t remaining) const
  {
    const PlanPart& part = plan_->parts[part_index];
    const int64_t part_size = part_sizes_[part_index].load();
    if (part_size <= 0)
      return 2;
    const int64_t needed = (remaining + part_size - 1) / part_size;
    int64_t wanted;
    if (needed >= workers_)
      wanted = std::max<int64_t>(plan_->prefetch / part_size + 1,
                                 static_cast<int64_t>(workers_) * pipeline_ + kWorkerMargin);
    else
      wanted = needed + kWorkerMargin;
    wanted = std::min<int64_t>(wanted, std::max<int64_t>(2, kHardPrefetchBytes / part_size));
    wanted = std::min<int64_t>(wanted, std::max<int64_t>(2, CacheLimit() / part_size - 1));
    return static_cast<int>(std::max<int64_t>(
        2, std::min<int64_t>(wanted, static_cast<int64_t>(part.articles.size()))));
  }

  ChunkInfo GetChunk(int part_index, int article, bool side = false)
  {
    const uint64_t key = MakeKey(part_index, article);
    const double started = Now();
    bool waited_once = false;
    std::unique_lock<std::mutex> lock(mutex_);
    for (;;)
    {
      if (closing_.load() || fatal_.load())
        return {};
      auto found = slots_.find(key);
      if (found == slots_.end())
      {
        EnqueueLocked(key, side ? kSideDistance : 0);
        found = slots_.find(key);
        found->second.side_only = side;
        job_cv_.notify_all();
      }
      Slot& slot = found->second;
      if (!side)
        slot.side_only = false;
      if (slot.state == Slot::State::Ready)
      {
        TouchLocked(key, slot);
        if (!waited_once)
          ++cache_hits_;
        return {slot.data, slot.zero_filled};
      }
      if (slot.state == Slot::State::Queued && !side)
        PromoteLocked(key, 0);
      pinned_.insert(key);
      if (!side)
        ++waiting_[key];
      cv_.wait(lock,
               [this, key]()
               {
                 if (closing_.load() || fatal_.load())
                   return true;
                 const auto current = slots_.find(key);
                 return current != slots_.end() && current->second.state == Slot::State::Ready;
               });
      waited_once = true;
      if (!side)
      {
        const auto waiting = waiting_.find(key);
        if (waiting != waiting_.end() && --waiting->second <= 0)
          waiting_.erase(waiting);
      }
      if (key != tail_key_)
        pinned_.erase(key);
      if (closing_.load() || fatal_.load())
        return {};
      const double waited = Now() - started;
      if (waited > 0.1)
      {
        ++cache_waits_;
        wait_seconds_ += waited;
        if (waited > wait_seconds_max_)
          wait_seconds_max_ = waited;
      }
      if (waited > 2.5 && slow_reads_.fetch_add(1) < 8)
        Log(LogLevel::Warning,
            "read waited %.1fs for article %d of part %d (%lld MB decoded so far)", waited,
            article, plan_->parts[part_index].id,
            static_cast<long long>(bytes_decoded_ / 1048576));
    }
  }

  bool EnsurePartSize(int part_index)
  {
    if (part_sizes_[part_index].load() > 0)
      return true;
    const ChunkInfo info = GetChunk(part_index, 0);
    if (!info.data || info.zero || info.data->empty())
      return false;
    part_sizes_[part_index].store(static_cast<int64_t>(info.data->size()));
    return true;
  }

  void WarmTail()
  {
    int part_index = -1;
    for (int index = static_cast<int>(plan_->parts.size()) - 1; index >= 0; --index)
    {
      if (plan_->parts[index].length > 0)
      {
        part_index = index;
        break;
      }
    }
    if (part_index < 0)
      return;
    if (!EnsurePartSize(part_index))
    {
      Log(LogLevel::Warning, "tail prefetch skipped - cannot determine the article size");
      return;
    }
    const PlanPart& part = plan_->parts[part_index];
    const int64_t part_size = part_sizes_[part_index].load();
    const int64_t volume = part.skip + part.length - 1;
    int64_t index = volume / part_size;
    if (index >= static_cast<int64_t>(part.articles.size()))
      index = static_cast<int64_t>(part.articles.size()) - 1;
    const uint64_t key = MakeKey(part_index, static_cast<int>(index));
    // Kodi reads the Matroska index right after the start: it is in the last few MB, not only in the very last
    // article (seen: Kodi waited 2.6 s for the one before it). Fetch the last kTailWarmBytes as well.
    const int64_t first_volume = std::max<int64_t>(part.skip, volume - kTailWarmBytes + 1);
    const int64_t first_index = std::max<int64_t>(0, first_volume / part_size);
    {
      std::lock_guard<std::mutex> lock(mutex_);
      pinned_.insert(key);
      tail_key_ = key;
      EnqueueLocked(key, -1);
      PromoteLocked(key, -1);
      for (int64_t earlier = index - 1; earlier >= first_index; --earlier)
      {
        const uint64_t other = MakeKey(part_index, static_cast<int>(earlier));
        if (slots_.find(other) != slots_.end())
          continue;
        pinned_.insert(other);
        EnqueueLocked(other, -1);
        PromoteLocked(other, -1);
      }
    }
    job_cv_.notify_all();
  }

  void CancelFar(int64_t offset)
  {
    std::lock_guard<std::mutex> lock(mutex_);
    const int64_t limit = plan_->prefetch * 2;
    for (auto entry = slots_.begin(); entry != slots_.end();)
    {
      Slot& slot = entry->second;
      if (slot.state != Slot::State::Queued || entry->first == tail_key_ ||
          pinned_.count(entry->first) != 0)
      {
        ++entry;
        continue;
      }
      const int part_index = PartOf(entry->first);
      const int article = ArticleOf(entry->first);
      const int64_t part_size = part_sizes_[part_index].load();
      const PlanPart& part = plan_->parts[part_index];
      int64_t start = part.media_start;
      if (part_size > 0)
        start += static_cast<int64_t>(article) * part_size - part.skip;
      if (start < 0)
        start = 0;
      const int64_t distance = start > offset ? start - offset : offset - start;
      if (distance > limit)
      {
        queue_.erase({slot.queue_distance, entry->first});
        entry = slots_.erase(entry);
        continue;
      }
      ++entry;
    }
    job_cv_.notify_all();
  }

  int Locate(int64_t offset, int64_t& local) const
  {
    for (size_t index = 0; index < plan_->parts.size(); ++index)
    {
      const PlanPart& part = plan_->parts[index];
      if (part.length <= 0)
        continue;
      if (offset < part.media_start + part.length)
      {
        local = offset - part.media_start;
        return static_cast<int>(index);
      }
    }
    return -1;
  }

  void LogStats(const char* when = "stream closed")
  {
    StreamStats stats = Stats();
    if (stats.articles <= 0)
      return;
    const double wall = started_at_ > 0 ? Now() - started_at_ : 0.0;
    const double megabytes = stats.bytes / 1048576.0;
    const double average = stats.article_seconds * 1000.0 / stats.articles;
    const double fetched = wall > 0.1 ? megabytes / wall : 0.0;
    int64_t read_bytes = 0;
    int64_t hits = 0;
    int64_t waits = 0;
    int64_t evictions = 0;
    int64_t refetches = 0;
    double wait_seconds = 0;
    double wait_max = 0;
    double decode_seconds = 0;
    int64_t decoded_articles = 0;
    int64_t hedges = 0;
    int64_t hedges_waited = 0;
    double hedge_limit = 0;
    int64_t jumps = 0;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      read_bytes = read_bytes_.load();
      hits = cache_hits_;
      waits = cache_waits_;
      wait_seconds = wait_seconds_;
      wait_max = wait_seconds_max_;
      evictions = evictions_;
      refetches = refetches_;
      decode_seconds = decode_seconds_;
      decoded_articles = decoded_articles_;
      hedges = hedges_total_;
      hedges_waited = hedges_waited_;
      hedge_limit = WaitedHedgeLimitLocked();
      jumps = jump_articles_;
    }
    const double served = wall > 0.1 ? read_bytes / 1048576.0 / wall : 0.0;
    const double decode_average =
        decoded_articles > 0 ? decode_seconds * 1000.0 / decoded_articles : 0.0;
    std::string providers;
    for (const auto& item : stats.providers)
    {
      const double speed =
          item.second.seconds > 0 ? item.second.bytes / item.second.seconds / 1048576.0 : 0.0;
      char entry[160] = {};
      std::snprintf(entry, sizeof(entry), "%s %lld (%.1f MB/s)", item.first.c_str(),
                    static_cast<long long>(item.second.articles), speed);
      if (!providers.empty())
        providers += ", ";
      providers += entry;
    }
    Log(LogLevel::Info,
        "%s: %lld articles, %lld missing, %.1f MB decoded, %.1f MB/s fetched, "
        "%.0f ms/article%s%s",
        when, static_cast<long long>(stats.articles), static_cast<long long>(stats.missing), megabytes,
        fetched, average, providers.empty() ? "" : "; ", providers.c_str());
    Log(LogLevel::Info,
        "%s, details: %.1f MB served in %.1f s (%.1f MB/s), first byte %.1f s, %lld waits "
        "%.1f s (max %.1f s), %lld evictions, %lld refetches, %lld cache hits, decode %.1f "
        "ms/article, cache %.0f MB, %.1f MB to extra readers, %lld asked at the other provider "
        "too (%lld the player waited for, after %.1f s), %lld ready for skips",
        when, read_bytes / 1048576.0, wall, served, first_read_cs_.load() / 100.0,
        static_cast<long long>(waits), wait_seconds, wait_max,
        static_cast<long long>(evictions), static_cast<long long>(refetches),
        static_cast<long long>(hits), decode_average, CacheLimit() / 1048576.0,
        side_bytes_.load() / 1048576.0, static_cast<long long>(hedges), static_cast<long long>(hedges_waited),
        hedge_limit, static_cast<long long>(jumps));
  }

  void DoClose()
  {
    Trace("close begin, active workers %d", active_workers_.load());
    closing_.store(true);
    job_cv_.notify_all();
    cv_.notify_all();
    for (const auto& pool : pools_)
      pool->Shutdown();
    Trace("close pools shut down, active workers %d", active_workers_.load());

    for (int waited = 0; waited < 1500 && active_workers_.load() > 0; waited += 10)
      std::this_thread::sleep_for(std::chrono::milliseconds(10));

    std::vector<std::thread> threads;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      threads.swap(threads_);
    }
    const bool stopped = active_workers_.load() <= 0;
    Trace("close threads %zu, active workers %d, stopped %d", threads.size(),
          active_workers_.load(), stopped ? 1 : 0);
    for (auto& thread : threads)
    {
      if (!thread.joinable())
        continue;
      if (stopped)
        thread.join();
      else
        thread.detach();
    }
    Trace("close threads handled");
    LogStats();
    if (stopped)
    {
      std::lock_guard<std::mutex> lock(mutex_);
      queue_.clear();
      slots_.clear();
      lru_.clear();
      pinned_.clear();
      cache_bytes_ = 0;
    }
  }

  std::shared_ptr<const Plan> plan_;
  std::vector<std::atomic<int64_t>> part_sizes_;
  std::vector<char> head_logged_;
  mutable std::mutex mutex_;
  std::condition_variable cv_;
  std::condition_variable job_cv_;
  std::atomic<bool> closing_{false};
  std::atomic<bool> fatal_{false};
  std::atomic<int64_t> read_offset_{0};
  std::atomic<int64_t> side_bytes_{0};
  std::atomic<int64_t> jump_mark_{-1};
  int64_t jump_articles_ = 0;     // articles fetched in advance for skips forward
  std::atomic<int64_t> last_read_{-1};
  std::atomic<int64_t> articles_attempted_{0};
  std::atomic<int> active_workers_{0};
  std::atomic<int> next_worker_id_{0};
  std::atomic<int> fetch_error_logs_{0};
  std::atomic<int> slow_reads_{0};
  bool started_ = false;
  std::string fatal_message_;
  std::string last_error_;
  uint64_t tail_key_ = UINT64_MAX;
  std::unordered_map<uint64_t, Slot> slots_;
  std::list<uint64_t> lru_;
  int64_t cache_bytes_ = 0;
  std::set<QueueEntry, QueueOrder> queue_;
  std::deque<Job> hedges_;
  std::unordered_set<uint64_t> pinned_;
  std::unordered_map<uint64_t, int> waiting_;     // articles a reader is blocked on right now
  double batch_ewma_ = 0;                         // what one fetch normally takes (seconds)
  int64_t batch_samples_ = 0;
  std::vector<std::unique_ptr<Pool>> pools_;
  std::vector<std::thread> threads_;
  std::once_flag close_once_;
  int workers_ = 1;
  int pipeline_ = 2;
  int max_attempts_ = 4;
  int64_t missing_ = 0;
  int64_t missing_run_ = 0;
  int64_t longest_run_ = 0;
  int64_t bytes_decoded_ = 0;
  double article_seconds_ = 0;
  std::map<std::string, ProviderStat> provider_stats_;
  std::map<std::string, ProviderStat> reported_;      // what TakeReport handed over already
  std::map<std::string, int> busy_count_;
  std::map<std::string, double> busy_since_;
  int64_t cache_limit_ = 0;
  double started_at_ = 0;
  std::atomic<double> seek_started_{0};
  std::atomic<bool> first_read_logged_{false};
  std::atomic<bool> seek_pending_{false};
  std::atomic<int64_t> first_read_cs_{0};
  std::atomic<int64_t> read_bytes_{0};
  int64_t cache_hits_ = 0;
  int64_t cache_waits_ = 0;
  double wait_seconds_ = 0;
  double wait_seconds_max_ = 0;
  int64_t evictions_ = 0;
  int64_t refetches_ = 0;
  int hedge_logs_ = 0;
  int64_t hedges_total_ = 0;      // articles also asked at the other provider
  int64_t hedges_waited_ = 0;     //   of which the player was waiting for
  double decode_seconds_ = 0;
  int64_t decoded_articles_ = 0;
  std::unordered_set<uint64_t> stored_keys_;
};

Stream::Stream(std::shared_ptr<const Plan> plan)
  : impl_(std::make_shared<Impl>(std::move(plan)))
{
}

Stream::~Stream()
{
  if (impl_)
    impl_->Close();
}

bool Stream::Start(std::string& error)
{
  if (!impl_)
  {
    error = "no plan";
    return false;
  }
  const bool started = impl_->Start(error);
  if (!started)
    impl_->Close();
  return started;
}

bool Stream::Usable() const
{
  return impl_ ? impl_->Usable() : false;
}

void Stream::Close()
{
  if (impl_)
    impl_->Close();
}

void Stream::PrimeFirstArticle(std::vector<uint8_t> data)
{
  if (impl_)
    impl_->PrimeFirstArticle(std::move(data));
}

void Stream::PrimeArticle(int index, std::vector<uint8_t> data)
{
  if (impl_)
    impl_->PrimeArticle(index, std::move(data));
}

int64_t Stream::Length() const
{
  return impl_ ? impl_->Length() : 0;
}

int64_t Stream::Read(int64_t offset, uint8_t* dest, size_t size, bool lead)
{
  return impl_ ? impl_->Read(offset, dest, size, lead) : -1;
}

StreamStats Stream::Stats() const
{
  return impl_ ? impl_->Stats() : StreamStats{};
}

void Stream::LogTotals(const char* when)
{
  if (impl_)
    impl_->LogTotals(when);
}

std::string Stream::TakeReport()
{
  return impl_ ? impl_->TakeReport() : std::string();
}

std::string Stream::LastError() const
{
  return impl_ ? impl_->LastError() : std::string();
}

}  // namespace engine
