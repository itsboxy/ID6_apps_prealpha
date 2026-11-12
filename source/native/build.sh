#!/bin/sh
set -eu
cd "$(dirname "$0")"
export QNX_HOST="${QNX_HOST:-/opt/qnx650/host/linux/x86}"
export QNX_TARGET="${QNX_TARGET:-/opt/qnx650/target/qnx6}"
export QNX_CONFIGURATION="${QNX_CONFIGURATION:-/etc/qnx}"
frameworks="${NBTEVO_FRAMEWORKS_DIR:-$PWD/frameworks}"
runtime="${NBTEVO_RUNTIME_DIR:-$PWD/runtime}"
for lib in libAppFramework.A.so libCoreFramework.A.so; do
    test -f "$frameworks/$lib" || { echo "Missing private framework: $frameworks/$lib" >&2; exit 1; }
done
export PATH="$QNX_HOST/usr/bin:$PATH"
mkdir -p out
for pair in libm.so:libm.so.2 libsocket.so:libsocket.so.3 libstdc++.so:libstdc++.so.6; do
    name=${pair%:*}
    versioned=${pair#*:}
    if test -f "$runtime/$versioned"; then ln -sf "$runtime/$versioned" "out/$name"; fi
done
ntoarm-gcc -dumpversion
ntoarm-gcc -dumpmachine
# SHIFT_FAST_RPM=1 sh native/build.sh enables the faster ShiftDeck RPM timing (drive_frame.h).
fast="-DSHIFT_FAST_RPM=${SHIFT_FAST_RPM:-0}"
echo "ShiftDeck RPM timing: $fast"
c() { qcc -V4.4.2,gcc_ntoarmv7le -Wc,-std=c99 -Wall -Wextra -Werror "$@"; }
cxx() { qcc -V4.4.2,gcc_ntoarmv7le_gpp -lang-c++ -Wc,-std=gnu++98 -Wall -Wextra -Werror "$@"; }
c -Isrc -O2 -fPIC -c src/gauge_core.c -o out/core.o
cxx -Isrc -Isdk -O2 -fno-strict-aliasing -fno-exceptions -fPIC -shared src/plugin.cpp out/core.o \
    "$frameworks/libAppFramework.A.so" "$frameworks/libCoreFramework.A.so" -Lout -lm -lsocket \
    -Wl,--allow-shlib-undefined -Wl,-soname,libBoostDeck.so -o out/libBoostDeck.so
c -Isrc -O2 src/collector.c src/gauge_core.c -Lout -lm -lsocket -o out/boostdeck-collector-qnx
cxx -Isrc -Isdk -O2 -fno-exceptions -fPIC -shared src/mhd_maps.cpp \
    "$frameworks/libAppFramework.A.so" "$frameworks/libCoreFramework.A.so" -Lout \
    -Wl,--allow-shlib-undefined -Wl,-soname,libMhdMaps.so -o out/libMhdMaps.so
cxx -Isrc -Isdk -O2 "$fast" -fno-exceptions -fPIC -shared src/shift_deck.cpp out/core.o \
    "$frameworks/libAppFramework.A.so" "$frameworks/libCoreFramework.A.so" -Lout -lm -lsocket \
    -Wl,--allow-shlib-undefined -Wl,-soname,libShiftDeck.so -o out/libShiftDeck.so
c -Isrc -O2 -DMHD_MAPS_ONLY src/collector.c src/gauge_core.c -Lout -lm -lsocket -o out/mhd-maps-qnx
c -Isrc -O2 "$fast" -DSHIFT_DECK_ONLY src/collector.c src/gauge_core.c -Lout -lm -lsocket -o out/shift-deck-qnx
c -O2 -static fileops.c -o out/gaugedeck-fileops
