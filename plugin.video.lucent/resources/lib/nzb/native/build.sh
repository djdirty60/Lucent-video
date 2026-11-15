#!/bin/sh
# Builds the yEnc unpacker for the systems Kodi runs on. Needs clang and lld (nothing else: no C library is linked,
# so one build works on every glibc, musl or Android of that processor type, and on every Windows 10/11 x64).
set -e
cd "$(dirname "$0")"
so() {  # folder target
    mkdir -p "$1"
    clang --target="$2" -O2 -fPIC -ffreestanding -fno-builtin -nostdlib -shared -fuse-ld=lld \
        -Wl,--build-id=none -Wl,-z,noexecstack -s -o "$1/libydec.so" ydec.c
}
so linux-aarch64 aarch64-linux-gnu
so linux-armv7 armv7a-linux-gnueabihf
so linux-x86_64 x86_64-linux-gnu
so android-aarch64 aarch64-linux-android21
so android-armv7 armv7a-linux-androideabi21
mkdir -p windows-x86_64
clang --target=x86_64-pc-windows-msvc -O2 -ffreestanding -fno-builtin -nostdlib -shared -fuse-ld=lld \
    -Wl,/noentry -Wl,/nodefaultlib -o windows-x86_64/ydec.dll ydec.c
rm -f windows-x86_64/ydec.lib windows-x86_64/ydec.exp
