#pragma once

#include "plan.h"
#include "transport.h"

#include <atomic>
#include <memory>
#include <stdexcept>
#include <chrono>
#include <string>
#include <vector>

namespace engine
{

class NntpError : public std::runtime_error
{
public:
  NntpError(const std::string& message, int code = 0)
    : std::runtime_error(message), code_(code)
  {
  }
  int Code() const { return code_; }

private:
  int code_;
};

class NntpAuthError : public NntpError
{
public:
  using NntpError::NntpError;
};

class NntpTooManyError : public NntpError
{
public:
  using NntpError::NntpError;
};

struct NntpReply
{
  bool missing = false;
  std::vector<uint8_t> body;
};

class NntpConnection
{
public:
  NntpConnection(const ServerConfig& server, int timeout,
                 const std::string& cacert = std::string());
  ~NntpConnection();

  NntpConnection(const NntpConnection&) = delete;
  NntpConnection& operator=(const NntpConnection&) = delete;

  std::vector<NntpReply> BodyMany(const std::vector<std::string>& message_ids);
  // Is each article there? STAT only: nothing is downloaded. All questions go out first, then the answers are read.
  std::vector<bool> StatMany(const std::vector<std::string>& message_ids);
  // The two halves of StatMany, so a next packet of questions can go out before the answers to this one are read.
  void StatSend(const std::vector<std::string>& message_ids);
  std::vector<bool> StatRead(const std::vector<std::string>& message_ids);
  // The same, but gives up (NntpError) when the whole packet is not answered before 'until', or when *stop turns true:
  // a provider that trickles its answers can never hold a check for minutes.
  std::vector<bool> StatRead(const std::vector<std::string>& message_ids,
                             std::chrono::steady_clock::time_point until, const std::atomic<bool>* stop);
  const std::string& Greeting() const { return greeting_; }
  // Seconds from sending the last BODY batch until its first answer line (for the statistics).
  double FirstReplySeconds() const { return first_reply_seconds_; }
  void Abort();

private:
  void Fill();
  std::string ReadLine();
  std::vector<uint8_t> ReadPayload();
  std::pair<int, std::string> Reply();
  std::pair<int, std::string> Command(const std::string& text);

  std::unique_ptr<Transport> transport_;
  std::string buffer_;
  std::string greeting_;
  std::atomic<bool> closed_{false};
  double first_reply_seconds_ = 0;
};

}  // namespace engine
