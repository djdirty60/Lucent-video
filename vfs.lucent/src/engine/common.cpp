#include "common.h"

#include <cstring>
#if (defined(__aarch64__) || defined(__arm__)) && (defined(__linux__) || defined(__ANDROID__))
#include <sys/auxv.h>
#define LUCENT_ARM_CRC 1
#endif

#include <atomic>
#include <cstdarg>
#include <cstdio>
#include <cstring>
#include <mutex>

#ifdef __ANDROID__
#include <android/log.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif

namespace engine
{

namespace
{
std::atomic<LogFn> gLog{nullptr};

void DefaultLog(LogLevel level, const char* message)
{
  const char* prefix = level == LogLevel::Error
                           ? "vfs.lucent error: "
                           : level == LogLevel::Warning ? "vfs.lucent warning: " : "vfs.lucent: ";
  std::fprintf(stderr, "%s%s\n", prefix, message);
}
}  // namespace

void SetLog(LogFn fn)
{
  gLog.store(fn ? fn : &DefaultLog);
}

void Trace(const char* fmt, ...)
{
  char message[1024];
  va_list args;
  va_start(args, fmt);
  std::vsnprintf(message, sizeof(message), fmt, args);
  va_end(args);
#ifdef __ANDROID__
  __android_log_print(ANDROID_LOG_INFO, "vfs.lucent-trace", "[%ld] %s",
                      static_cast<long>(syscall(SYS_gettid)), message);
#else
  std::fprintf(stderr, "%s\n", message);
#endif
}

void Log(LogLevel level, const char* fmt, ...)
{
  char message[2048];
  va_list args;
  va_start(args, fmt);
  std::vsnprintf(message, sizeof(message), fmt, args);
  va_end(args);
  LogFn fn = gLog.load();
  if (fn == nullptr)
    DefaultLog(level, message);
  else
    fn(level, message);
}

bool Base64Decode(const std::string& text, std::string& out)
{
  out.clear();
  int value = 0;
  int bits = -8;
  for (unsigned char ch : text)
  {
    int digit;
    if (ch >= 'A' && ch <= 'Z')
      digit = ch - 'A';
    else if (ch >= 'a' && ch <= 'z')
      digit = ch - 'a' + 26;
    else if (ch >= '0' && ch <= '9')
      digit = ch - '0' + 52;
    else if (ch == '+' || ch == '-')
      digit = 62;
    else if (ch == '/' || ch == '_')
      digit = 63;
    else if (ch == '=')
      continue;
    else if (ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n')
      continue;
    else
      return false;
    value = (value << 6) | digit;
    bits += 6;
    if (bits >= 0)
    {
      out.push_back(static_cast<char>((value >> bits) & 0xFF));
      bits -= 8;
    }
  }
  return true;
}

namespace
{
// Slice-by-8: eight bytes per step instead of one (same result as the plain table method).
struct CrcTables
{
  uint32_t t[8][256];
  CrcTables()
  {
    for (uint32_t index = 0; index < 256; ++index)
    {
      uint32_t value = index;
      for (int bit = 0; bit < 8; ++bit)
        value = (value & 1) ? (0xEDB88320u ^ (value >> 1)) : (value >> 1);
      t[0][index] = value;
    }
    for (uint32_t index = 0; index < 256; ++index)
      for (int k = 1; k < 8; ++k)
        t[k][index] = (t[k - 1][index] >> 8) ^ t[0][t[k - 1][index] & 0xFF];
  }
};

const CrcTables& Tables()
{
  static const CrcTables tables;
  return tables;
}

#if defined(LUCENT_ARM_CRC)
// ARMv8 processors have CRC32 instructions, also when Android or Kodi runs in 32-bit mode
// (a 64-bit chip with a 32-bit system, like most Android TV boxes). Asked once from the kernel.
uint32_t CrcArm(uint32_t crc, const uint8_t* bytes, size_t size)
{
#if defined(__aarch64__)
  while (size >= 8)
  {
    uint64_t word;
    std::memcpy(&word, bytes, 8);
    __asm__(".arch_extension crc\n\tcrc32x %w0, %w0, %x1" : "+r"(crc) : "r"(word));
    bytes += 8;
    size -= 8;
  }
  while (size-- > 0)
  {
    const uint32_t byte = *bytes++;
    __asm__(".arch_extension crc\n\tcrc32b %w0, %w0, %w1" : "+r"(crc) : "r"(byte));
  }
#else
  while (size >= 4)
  {
    uint32_t word;
    std::memcpy(&word, bytes, 4);
    __asm__(".arch armv8-a\n\t.arch_extension crc\n\tcrc32w %0, %0, %1" : "+r"(crc) : "r"(word));
    bytes += 4;
    size -= 4;
  }
  while (size-- > 0)
  {
    const uint32_t byte = *bytes++;
    __asm__(".arch armv8-a\n\t.arch_extension crc\n\tcrc32b %0, %0, %1" : "+r"(crc) : "r"(byte));
  }
#endif
  return crc;
}

bool HasArmCrc()
{
#if defined(__aarch64__)
  static const bool has = (getauxval(AT_HWCAP) & (1UL << 7)) != 0;    // HWCAP_CRC32
#else
  static const bool has = (getauxval(AT_HWCAP2) & (1UL << 4)) != 0;   // HWCAP2_CRC32
#endif
  return has;
}
#endif
}  // namespace

uint32_t Crc32(const void* data, size_t size, uint32_t seed)
{
  uint32_t crc = seed ^ 0xFFFFFFFFu;
  const auto* bytes = static_cast<const uint8_t*>(data);
#if defined(LUCENT_ARM_CRC)
  if (HasArmCrc())
    return CrcArm(crc, bytes, size) ^ 0xFFFFFFFFu;
#endif
  const CrcTables& tables = Tables();
  const auto& t = tables.t;
  while (size >= 8)
  {
    const uint32_t low = crc ^ (static_cast<uint32_t>(bytes[0]) | (static_cast<uint32_t>(bytes[1]) << 8) |
                                (static_cast<uint32_t>(bytes[2]) << 16) | (static_cast<uint32_t>(bytes[3]) << 24));
    const uint32_t high = static_cast<uint32_t>(bytes[4]) | (static_cast<uint32_t>(bytes[5]) << 8) |
                          (static_cast<uint32_t>(bytes[6]) << 16) | (static_cast<uint32_t>(bytes[7]) << 24);
    crc = t[7][low & 0xFF] ^ t[6][(low >> 8) & 0xFF] ^ t[5][(low >> 16) & 0xFF] ^ t[4][low >> 24] ^
          t[3][high & 0xFF] ^ t[2][(high >> 8) & 0xFF] ^ t[1][(high >> 16) & 0xFF] ^ t[0][high >> 24];
    bytes += 8;
    size -= 8;
  }
  while (size-- > 0)
    crc = t[0][(crc ^ *bytes++) & 0xFF] ^ (crc >> 8);
  return crc ^ 0xFFFFFFFFu;
}

std::string Trim(const std::string& text)
{
  size_t start = 0;
  size_t end = text.size();
  while (start < end && (text[start] == ' ' || text[start] == '\t' || text[start] == '\r' ||
                         text[start] == '\n'))
    ++start;
  while (end > start && (text[end - 1] == ' ' || text[end - 1] == '\t' ||
                         text[end - 1] == '\r' || text[end - 1] == '\n'))
    --end;
  return text.substr(start, end - start);
}

bool StartsWith(const std::string& text, const char* prefix)
{
  const size_t length = std::strlen(prefix);
  return text.size() >= length && std::memcmp(text.data(), prefix, length) == 0;
}

std::string Lower(const std::string& text)
{
  std::string out = text;
  for (char& ch : out)
    if (ch >= 'A' && ch <= 'Z')
      ch = static_cast<char>(ch - 'A' + 'a');
  return out;
}

}  // namespace engine
