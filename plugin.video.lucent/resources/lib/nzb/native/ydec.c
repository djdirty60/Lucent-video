/* Lucent Video - yEnc unpacking in C (MIT licence, same as the add-on).
 *
 * The data lines of one yEnc article (everything between the =ypart/=ybegin line and the =yend line) -> the bytes.
 * Every byte was written as (byte + 42) % 256; '=' means the next one was written as (byte + 42 + 64) % 256; line
 * breaks are not data. Python calls this through ctypes, which lets go of Python's lock while it runs, so several
 * articles are unpacked at the same time on several processor cores.
 *
 * It uses nothing from the system (no C library), so one build works on every Linux of that processor type.
 * Build: see build.sh next to this file.
 */
#ifdef _WIN32
#define EXPORT __declspec(dllexport)
#else
#define EXPORT __attribute__((visibility("default")))
#endif

EXPORT unsigned long lucent_ydecode(const unsigned char *in, unsigned long n, unsigned char *out)
{
    unsigned long i, o = 0;
    int escaped = 0;
    for (i = 0; i < n; i++) {
        unsigned char c = in[i];
        if (c == '\r' || c == '\n')
            continue;
        if (escaped) {
            out[o++] = (unsigned char)(c - 106);
            escaped = 0;
        } else if (c == '=') {
            escaped = 1;
        } else {
            out[o++] = (unsigned char)(c - 42);
        }
    }
    return o;
}

/* So Lucent can check it loaded the right file. */
EXPORT unsigned long lucent_ydecode_version(void)
{
    return 1;
}
