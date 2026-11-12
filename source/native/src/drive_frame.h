#ifndef DRIVE_FRAME_H
#define DRIVE_FRAME_H

#include <stdint.h>

enum { DRIVE_PORT = 42863, DRIVE_STALE_MS = 750 };

/* ShiftDeck RPM timing. 0 (default): original timing, reconnecting each lease
 * and reading RPM and speed every 70 ms. 1: faster RPM, keeping the gateway
 * connection while no other client waits, reading RPM every 30 ms and speed
 * every 4th cycle, and refreshing the screen every 25 ms. Not yet validated
 * on a car. Build with SHIFT_FAST_RPM=1 (see build.sh and Makefile). */
#ifndef SHIFT_FAST_RPM
#define SHIFT_FAST_RPM 0
#endif

struct drive_frame {
    uint64_t sampled_ms;
    int32_t rpm;
    int32_t kph;
};

#endif
