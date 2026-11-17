#include "yenc.h"

#include "common.h"

#include <algorithm>
#include <cstring>

#if defined(__SSE2__) || defined(_M_X64)
#include <emmintrin.h>
#define LUCENT_YENC_SSE2 1
#elif defined(__ARM_NEON) || defined(__ARM_NEON__) || defined(__aarch64__)
#include <arm_neon.h>
#define LUCENT_YENC_NEON 1
#endif

namespace engine
{

namespace
{
const char kBegin[] = "=ybegin";
const char kPart[] = "=ypart";
const char kEnd[] = "=yend";

size_t FindBytes(const std::vector<uint8_t>& data, size_t from, const char* needle)
{
  const size_t length = std::strlen(needle);
  if (from >= data.size() || length > data.size() - from)
    return std::string::npos;
  const auto found = std::search(data.begin() + static_cast<std::ptrdiff_t>(from), data.end(),
                                 needle, needle + length);
  if (found == data.end())
    return std::string::npos;
  return static_cast<size_t>(found - data.begin());
}

size_t FindLast(const std::vector<uint8_t>& data, const char* needle)
{
  const size_t length = std::strlen(needle);
  if (length > data.size())
    return std::string::npos;
  for (size_t position = data.size() - length + 1; position-- > 0;)
    if (std::memcmp(data.data() + position, needle, length) == 0)
      return position;
  return std::string::npos;
}

std::string LineAt(const std::vector<uint8_t>& data, size_t start)
{
  if (start >= data.size())
    return std::string();
  size_t end = start;
  while (end < data.size() && data[end] != '\n')
    ++end;
  std::string line(reinterpret_cast<const char*>(data.data() + start), end - start);
  if (!line.empty() && line.back() == '\r')
    line.pop_back();
  return line;
}

void ParseHeader(const std::string& line, int64_t& size, int64_t& begin, int64_t& end,
                 std::string& part_crc, std::string& file_crc)
{
  size_t position = 0;
  while (position < line.size())
  {
    while (position < line.size() && (line[position] == ' ' || line[position] == '\t'))
      ++position;
    const size_t start = position;
    while (position < line.size() && line[position] != ' ' && line[position] != '\t')
      ++position;
    const std::string token = line.substr(start, position - start);
    const size_t equals = token.find('=');
    if (equals == std::string::npos)
      continue;
    const std::string key = Lower(token.substr(0, equals));
    const std::string value = token.substr(equals + 1);
    auto number = [&value](int64_t& target)
    {
      try
      {
        size_t used = 0;
        const long long parsed = std::stoll(value, &used);
        if (used == value.size())
          target = parsed;
      }
      catch (...)
      {
      }
    };
    if (key == "size")
      number(size);
    else if (key == "begin")
    {
      number(begin);
      if (begin > 0)
        --begin;
    }
    else if (key == "end")
      number(end);
    else if (key == "pcrc32")
      part_crc = value;
    else if (key == "crc32")
      file_crc = value;
  }
}

std::string StripName(const std::string& line)
{
  size_t at = line.find(" name=");
  if (at == std::string::npos && line.rfind("name=", 0) == 0)
    at = 0;
  return at == std::string::npos ? line : line.substr(0, at);
}
// The yEnc body: line breaks are dropped, "=x" is x - 106, every other byte is itself - 42.
// 16 bytes at a time: the block is decoded in one go and written; when it holds a line break or an
// '=', only the bytes before that one are kept and that one is handled on its own. `out` needs
// 16 bytes more room than the body is long.
size_t DecodeBody(const uint8_t* in, size_t length, uint8_t* out)
{
  size_t index = 0;
  size_t written = 0;
#if defined(LUCENT_YENC_SSE2)
  const __m128i cr = _mm_set1_epi8('\r');
  const __m128i lf = _mm_set1_epi8('\n');
  const __m128i eq = _mm_set1_epi8('=');
  const __m128i offset = _mm_set1_epi8(42);
  while (index + 16 <= length)
  {
    const __m128i block = _mm_loadu_si128(reinterpret_cast<const __m128i*>(in + index));
    _mm_storeu_si128(reinterpret_cast<__m128i*>(out + written), _mm_sub_epi8(block, offset));
    const __m128i special = _mm_or_si128(_mm_or_si128(_mm_cmpeq_epi8(block, cr), _mm_cmpeq_epi8(block, lf)),
                                         _mm_cmpeq_epi8(block, eq));
    const unsigned mask = static_cast<unsigned>(_mm_movemask_epi8(special));
    if (mask == 0)
    {
      index += 16;
      written += 16;
      continue;
    }
    const unsigned first = static_cast<unsigned>(__builtin_ctz(mask));
#elif defined(LUCENT_YENC_NEON)
  const uint8x16_t cr = vdupq_n_u8('\r');
  const uint8x16_t lf = vdupq_n_u8('\n');
  const uint8x16_t eq = vdupq_n_u8('=');
  const uint8x16_t offset = vdupq_n_u8(42);
  while (index + 16 <= length)
  {
    const uint8x16_t block = vld1q_u8(in + index);
    vst1q_u8(out + written, vsubq_u8(block, offset));
    const uint8x16_t special = vorrq_u8(vorrq_u8(vceqq_u8(block, cr), vceqq_u8(block, lf)), vceqq_u8(block, eq));
    // 4 bits per byte: the lowest set nibble is the first special byte
    const uint8x8_t narrowed = vshrn_n_u16(vreinterpretq_u16_u8(special), 4);
    const uint64_t mask = vget_lane_u64(vreinterpret_u64_u8(narrowed), 0);
    if (mask == 0)
    {
      index += 16;
      written += 16;
      continue;
    }
    const unsigned first = static_cast<unsigned>(__builtin_ctzll(mask)) >> 2;
#endif
#if defined(LUCENT_YENC_SSE2) || defined(LUCENT_YENC_NEON)
    index += first;
    written += first;
    if (in[index] == '=')
    {
      if (index + 1 < length)
        out[written++] = static_cast<uint8_t>(in[index + 1] - 106);
      index += 2;
    }
    else
      ++index;
  }
#endif
  while (index < length)
  {
    const uint8_t value = in[index];
    if (value == '\r' || value == '\n')
    {
      ++index;
      continue;
    }
    if (value == '=')
    {
      if (index + 1 < length)
        out[written++] = static_cast<uint8_t>(in[index + 1] - 106);
      index += 2;
      continue;
    }
    out[written++] = static_cast<uint8_t>(value - 42);
    ++index;
  }
  return written;
}
}  // namespace

YencResult YencDecode(const std::vector<uint8_t>& article)
{
  YencResult result;
  if (article.empty())
  {
    result.error = "empty article";
    return result;
  }
  const size_t begin_at = FindBytes(article, 0, kBegin);
  if (begin_at == std::string::npos)
  {
    result.error = "no =ybegin in this article";
    return result;
  }
  size_t line_end = begin_at;
  while (line_end < article.size() && article[line_end] != '\n')
    ++line_end;
  if (line_end >= article.size())
  {
    result.error = "truncated =ybegin";
    return result;
  }
  const std::string begin_line = StripName(LineAt(article, begin_at));
  int64_t total_size = 0;
  int64_t ignored_begin = 0;
  int64_t ignored_end = 0;
  std::string ignored_crc;
  std::string ignored_file_crc;
  ParseHeader(begin_line, total_size, ignored_begin, ignored_end, ignored_crc, ignored_file_crc);

  size_t position = line_end + 1;
  int64_t part_begin = 0;
  int64_t part_end = 0;
  bool has_part = false;
  if (position + 6 <= article.size() &&
      std::memcmp(article.data() + position, kPart, 6) == 0)
  {
    has_part = true;
    size_t part_line_end = position;
    while (part_line_end < article.size() && article[part_line_end] != '\n')
      ++part_line_end;
    if (part_line_end >= article.size())
    {
      result.error = "truncated =ypart";
      return result;
    }
    ParseHeader(LineAt(article, position), ignored_begin, part_begin, part_end, ignored_crc,
                ignored_file_crc);
    position = part_line_end + 1;
  }

  const size_t end_at = FindLast(article, kEnd);
  std::string part_crc;
  std::string file_crc;
  if (end_at != std::string::npos)
  {
    const std::string end_line = LineAt(article, end_at);
    int64_t ignored = 0;
    ParseHeader(end_line, ignored, ignored_begin, ignored_end, part_crc, file_crc);
  }

  const size_t body_end = end_at == std::string::npos ? article.size() : end_at;
  const size_t body_start = std::min(position, body_end);
  result.data.resize(body_end - body_start + 16);
  const size_t written = DecodeBody(article.data() + body_start, body_end - body_start,
                                    result.data.data());
  result.data.resize(written);
  if (result.data.empty())
  {
    result.error = "no payload";
    return result;
  }

  result.begin = has_part ? part_begin : 0;
  result.part_end = has_part ? part_end : static_cast<int64_t>(result.data.size());
  result.size = total_size > 0 ? total_size : result.part_end;

  const std::string& wanted = has_part ? part_crc : file_crc;
  if (!wanted.empty())
  {
    try
    {
      const uint32_t expected = static_cast<uint32_t>(std::stoul(wanted, nullptr, 16));
      result.crc_ok = Crc32(result.data.data(), result.data.size()) == expected;
    }
    catch (...)
    {
      result.crc_ok = true;
    }
  }
  result.ok = true;
  return result;
}

}  // namespace engine
