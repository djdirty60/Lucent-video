// Lucent Video Check: asks a Usenet provider whether every article of a release is still there (STAT, no data), in
// C++ and over many connections at once. Lucent Video writes a job (a normal Lucent plan) as <token>.check in its
// usenet-vfs folder and opens lucentc://<token>/check.json; the open runs the check and the file holds the answer.
// Lucent Video asks it to stop (a stop file) the moment a film starts playing, so it never takes the line.
#include "engine/checker.h"
#include "engine/common.h"
#include "engine/plan.h"

#include <kodi/AddonBase.h>
#include <kodi/Filesystem.h>
#include <kodi/General.h>
#include <kodi/addon-instance/VFS.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <cstring>
#include <string>
#include <sys/types.h>
#include <thread>

#ifdef __ANDROID__
#include <android/log.h>
#endif

namespace
{
constexpr const char* PROTOCOL = "lucentc";
constexpr const char* JOB_FOLDER = "special://profile/addon_data/plugin.video.lucent/usenet-vfs/";
constexpr int kMostConnections = 32;
constexpr double kMostSeconds = 120.0;

struct Answer
{
  std::string text;
  int64_t position{0};
};

void EngineLog(engine::LogLevel level, const char* message)
{
  const ADDON_LOG kodi_level =
      level == engine::LogLevel::Error
          ? ADDON_LOG_ERROR
          : level == engine::LogLevel::Warning ? ADDON_LOG_WARNING : ADDON_LOG_INFO;
#ifdef __ANDROID__
  __android_log_print(ANDROID_LOG_INFO, "vfs.lucentcheck", "%s", message);
#endif
  kodi::Log(kodi_level, "%s", message);
}

bool ValidToken(const std::string& token)
{
  if (token.size() < 8 || token.size() > 64)
    return false;
  for (char value : token)
    if (!((value >= '0' && value <= '9') || (value >= 'a' && value <= 'f') || (value >= 'A' && value <= 'F')))
      return false;
  return true;
}

std::string Folder()
{
  return kodi::vfs::TranslateSpecialProtocol(JOB_FOLDER);
}

bool ReadTextFile(const std::string& path, std::string& text)
{
  kodi::vfs::CFile file;
  if (!file.OpenFile(path, ADDON_READ_NO_CACHE))
    return false;
  char buffer[64 * 1024];
  text.clear();
  for (;;)
  {
    const ssize_t got = file.Read(buffer, sizeof(buffer));
    if (got < 0)
      return false;
    if (got == 0)
      break;
    text.append(buffer, static_cast<size_t>(got));
    if (text.size() > 64u * 1024 * 1024)
      return false;
  }
  return true;
}

std::string Error(const std::string& text)
{
  engine::CheckResult result;
  result.error = text;
  return engine::CheckJson(result);
}

std::string RunJob(const std::string& token)
{
  std::string text;
  if (!ReadTextFile(Folder() + token + ".check", text))
    return Error("the check job is missing");
  engine::Plan plan;
  try
  {
    plan = engine::Plan::Parse(text);
  }
  catch (const std::exception& exception)
  {
    return Error(exception.what());
  }
  // The connections Lucent Video leaves for this check (it keeps the rest free); 'seconds' is the time limit.
  const int connections = std::max(1, std::min(kMostConnections, plan.workers));
  const double limit = plan.seconds > 0 ? std::min(kMostSeconds, static_cast<double>(plan.seconds)) : 60.0;
  std::atomic<bool> stop{false};
  std::atomic<bool> done{false};
  const std::string stop_path = Folder() + token + ".stop";
  std::thread watcher(
      [&]()
      {
        while (!done.load())
        {
          if (kodi::vfs::FileExists(stop_path, true))
          {
            stop = true;
            return;
          }
          std::this_thread::sleep_for(std::chrono::milliseconds(150));
        }
      });
  engine::CheckResult result;
  try
  {
    result = engine::CheckArticles(plan, connections, limit, &stop);
  }
  catch (const std::exception& exception)
  {
    result.error = exception.what();
  }
  done = true;
  watcher.join();
  engine::Log(engine::LogLevel::Info,
              "check %s: %lld of %lld articles there, %.1f s over %d connections + the backup (all asked once after "
              "%.1f s, %lld at the backup, %lld only at the backup; %d connection(s) signed in in %.0f ms each, %d broken; "
              "per connection %.0f answers/s)%s%s",
              token.substr(0, 8).c_str(), static_cast<long long>(result.present), static_cast<long long>(result.total),
              result.seconds, connections, result.first_pass, static_cast<long long>(result.at_backup),
              static_cast<long long>(result.from_backup), result.opened, result.signin_ms, result.broken, result.rate[0],
              result.error.empty() ? "" : ", ", result.error.c_str());
  return engine::CheckJson(result);
}
}  // namespace

class ATTR_DLL_LOCAL CCheckVFS : public kodi::addon::CInstanceVFS
{
public:
  explicit CCheckVFS(const kodi::addon::IInstanceInfo& instance) : CInstanceVFS(instance) {}

  kodi::addon::VFSFileHandle Open(const kodi::addon::VFSUrl& url) override
  {
    if (url.GetProtocol() != PROTOCOL || !ValidToken(url.GetHostname()))
      return nullptr;
    auto* answer = new Answer;
    answer->text = RunJob(url.GetHostname());
    return answer;
  }
  bool Close(kodi::addon::VFSFileHandle context) override
  {
    delete static_cast<Answer*>(context);
    return true;
  }
  ssize_t Read(kodi::addon::VFSFileHandle context, uint8_t* buffer, size_t size) override
  {
    auto* answer = static_cast<Answer*>(context);
    if (!answer)
      return -1;
    const int64_t left = static_cast<int64_t>(answer->text.size()) - answer->position;
    if (left <= 0)
      return 0;
    const size_t count = std::min(size, static_cast<size_t>(left));
    std::memcpy(buffer, answer->text.data() + answer->position, count);
    answer->position += static_cast<int64_t>(count);
    return static_cast<ssize_t>(count);
  }
  int64_t Seek(kodi::addon::VFSFileHandle context, int64_t position, int whence) override
  {
    auto* answer = static_cast<Answer*>(context);
    if (!answer)
      return -1;
    const int64_t length = static_cast<int64_t>(answer->text.size());
    int64_t target = whence == SEEK_SET ? position : whence == SEEK_CUR ? answer->position + position
                                                   : whence == SEEK_END ? length + position : -1;
    if (whence != SEEK_SET && whence != SEEK_CUR && whence != SEEK_END)
      return -1;
    answer->position = std::max<int64_t>(0, std::min(length, target));
    return answer->position;
  }
  int64_t GetLength(kodi::addon::VFSFileHandle context) override
  {
    auto* answer = static_cast<Answer*>(context);
    return answer ? static_cast<int64_t>(answer->text.size()) : 0;
  }
  int64_t GetPosition(kodi::addon::VFSFileHandle context) override
  {
    auto* answer = static_cast<Answer*>(context);
    return answer ? answer->position : 0;
  }
  int GetChunkSize(kodi::addon::VFSFileHandle) override { return 0; }
  bool IoControlGetSeekPossible(kodi::addon::VFSFileHandle) override { return true; }
  // Kodi asks these before opening: the job file decides; the answer's size is only known after the check.
  int Stat(const kodi::addon::VFSUrl& url, kodi::vfs::FileStatus& buffer) override
  {
    if (url.GetProtocol() != PROTOCOL || !ValidToken(url.GetHostname()) ||
        !kodi::vfs::FileExists(Folder() + url.GetHostname() + ".check", true))
      return -1;
    buffer.SetSize(0);
    buffer.SetIsRegular(true);
    return 0;
  }
  bool Exists(const kodi::addon::VFSUrl& url) override
  {
    return url.GetProtocol() == PROTOCOL && ValidToken(url.GetHostname()) &&
           kodi::vfs::FileExists(Folder() + url.GetHostname() + ".check", true);
  }

};

class ATTR_DLL_LOCAL CMyAddon : public kodi::addon::CAddonBase
{
public:
  CMyAddon() { engine::SetLog(&EngineLog); }
  ADDON_STATUS CreateInstance(const kodi::addon::IInstanceInfo& instance, KODI_ADDON_INSTANCE_HDL& hdl) override
  {
    if (!instance.IsType(ADDON_INSTANCE_VFS))
      return ADDON_STATUS_NOT_IMPLEMENTED;
    hdl = new CCheckVFS(instance);
    return ADDON_STATUS_OK;
  }
};

ADDONCREATOR(CMyAddon)
