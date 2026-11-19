#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace engine
{

enum class LogLevel
{
  Info,
  Warning,
  Error
};

using LogFn = void (*)(LogLevel level, const char* message);

void SetLog(LogFn fn);
#if defined(__MINGW32__)
// MinGW's plain 'printf' check is Microsoft's old one (no %zu); the UCRT that Kodi uses does know the standard one
void Log(LogLevel level, const char* fmt, ...) __attribute__((format(gnu_printf, 2, 3)));
#elif defined(__GNUC__)
void Log(LogLevel level, const char* fmt, ...) __attribute__((format(printf, 2, 3)));  // the compiler checks every log line
#else
void Log(LogLevel level, const char* fmt, ...);
#endif
void Trace(const char* fmt, ...);

bool Base64Decode(const std::string& text, std::string& out);
uint32_t Crc32(const void* data, size_t size, uint32_t seed = 0);

std::string Trim(const std::string& text);
bool StartsWith(const std::string& text, const char* prefix);
std::string Lower(const std::string& text);

}  // namespace engine
