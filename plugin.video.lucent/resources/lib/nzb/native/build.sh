#!/bin/sh
# Builds libydec.so for the Linux processor types Kodi runs on (CoreELEC, LibreELEC, desktop Linux).
# Needs clang and lld. No C library is linked: the result works on any glibc or musl of that processor type.
set -e
cd "$(dirname "$0")"
for pair in "aarch64:aarch64-linux-gnu" "armv7:armv7a-linux-gnueabihf" "x86_64:x86_64-linux-gnu"; do
    arch=${pair%%:*}; target=${pair#*:}
    mkdir -p "linux-$arch"
    clang --target="$target" -O2 -fPIC -ffreestanding -fno-builtin -nostdlib -shared -fuse-ld=lld \
        -Wl,--build-id=none -s -o "linux-$arch/libydec.so" ydec.c
done
