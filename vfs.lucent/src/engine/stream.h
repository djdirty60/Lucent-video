#pragma once

#include "plan.h"

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace engine
{

struct ProviderStat
{
  int64_t articles = 0;
  int64_t bytes = 0;
  double seconds = 0;
  int64_t missing = 0;
  int64_t errors = 0;
  int64_t replies = 0;           // batches whose first-answer time is in reply_seconds
  double reply_seconds = 0;
  double busy_seconds = 0;       // wall time with at least one fetch running at this provider
};

struct StreamStats
{
  int64_t articles = 0;
  int64_t missing = 0;
  int64_t bytes = 0;
  double article_seconds = 0;
  std::map<std::string, ProviderStat> providers;
};

class Stream
{
public:
  explicit Stream(std::shared_ptr<const Plan> plan);
  ~Stream();

  Stream(const Stream&) = delete;
  Stream& operator=(const Stream&) = delete;

  bool Start(std::string& error);
  bool Usable() const;
  void PrimeFirstArticle(std::vector<uint8_t> data);
  void PrimeArticle(int index, std::vector<uint8_t> data);
  void Close();
  int64_t Length() const;
  // lead=false: a second reader of the same film (Kodi making chapter pictures, stream details):
  // it gets its bytes without moving the read-ahead of the player.
  int64_t Read(int64_t offset, uint8_t* dest, size_t size, bool lead = true);
  StreamStats Stats() const;
  // The totals so far, in the log (when the player stops; the stream itself stays warm a while).
  void LogTotals(const char* when);
  // The provider counts since the previous call, as text for Lucent's statistics ("" when nothing new).
  std::string TakeReport();
  std::string LastError() const;

private:
  class Impl;
  std::shared_ptr<Impl> impl_;
};

}  // namespace engine
