#ifndef DIAGNOSTIC_LEASE_H
#define DIAGNOSTIC_LEASE_H

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <string.h>
#include <stdint.h>
#include <sys/socket.h>
#include <unistd.h>

enum { DIAGNOSTIC_PORT = 42861, DIAGNOSTIC_LEASE_MS = 500,
       DIAGNOSTIC_MIN_LEASE_MS = 140, DIAGNOSTIC_YIELD_MS = 165,
       DIAGNOSTIC_RETRY_MS = 300 };

/* The TCP connection must be closed before giving up this loopback lock.
 * One-shot diagnostics use the same lock as the gauge workers. */
static inline int reserve_gateway(void) {
    struct sockaddr_in address;
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;
    memset(&address, 0, sizeof address);
    address.sin_family = AF_INET;
    inet_pton(AF_INET, "127.0.0.1", &address.sin_addr);
    address.sin_port = htons(DIAGNOSTIC_PORT);
    if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0) {
        close(fd);
        return -1;
    }
    if (bind(fd, (struct sockaddr *)&address, sizeof address) < 0) {
        int saved = errno;
        if (saved == EADDRINUSE) {
            const char waiting = 1;
            sendto(fd, &waiting, 1, 0, (struct sockaddr *)&address, sizeof address);
        }
        close(fd);
        errno = saved;
        return -1;
    }
    return fd;
}

static inline int gateway_should_yield(int lock, uint64_t now, uint64_t until) {
    char waiting;
    if (now >= until) return 1;
    // Keep short transactions together instead of reconnecting for each request.
    return until - now <= DIAGNOSTIC_LEASE_MS - DIAGNOSTIC_MIN_LEASE_MS &&
           recv(lock, &waiting, sizeof waiting, 0) > 0;
}

/* For a worker that polls continuously: hold the connection for the minimum
 * lease, then yield only when another client has asked for the gateway.
 * With no waiter, an expired lease is renewed instead of reconnecting. */
static inline int gateway_yield_or_renew(int lock, uint64_t now, uint64_t *until) {
    char waiting;
    if (now < *until && *until - now > DIAGNOSTIC_LEASE_MS - DIAGNOSTIC_MIN_LEASE_MS) return 0;
    if (recv(lock, &waiting, sizeof waiting, 0) > 0) return 1;
    if (now >= *until) *until = now + DIAGNOSTIC_LEASE_MS;
    return 0;
}

static inline void release_gateway(int *connection, int *lock) {
    if (*connection >= 0) close(*connection);
    if (*lock >= 0) close(*lock);
    *connection = *lock = -1;
}

#endif
