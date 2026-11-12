#define _POSIX_C_SOURCE 200809L
#include "gauge_core.h"
#include "diagnostic_lease.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static volatile sig_atomic_t running = 1;
static void stop(int sig) { (void)sig; running = 0; }

static int ready(int fd, short events, uint64_t deadline) {
    struct pollfd p;
    int rc;
    p.fd = fd; p.events = events; p.revents = 0;
    while (running) {
        uint64_t now = bd_mono_ms();
        if (now >= deadline) return 0;
        rc = poll(&p, 1, (int)(deadline - now));
        if (rc < 0 && errno == EINTR) continue;
        return rc > 0 && (p.revents & events);
    }
    return 0;
}
static int transfer(int fd, unsigned char *buf, size_t n, int sending, uint64_t deadline) {
    while (n && running) {
        ssize_t k;
        if (!ready(fd, sending ? POLLOUT : POLLIN, deadline)) return 0;
        k = sending ? send(fd, buf, n, 0) : recv(fd, buf, n, 0);
        if (k < 0 && (errno == EINTR || errno == EAGAIN || errno == EWOULDBLOCK)) continue;
        if (k <= 0) return 0;
        buf += k; n -= (size_t)k;
    }
    return n == 0;
}
static int connect_gw(const char *host, unsigned port) {
    struct sockaddr_in a;
    int fd, error = 0, no_delay = 1;
    socklen_t len = sizeof error;
    memset(&a, 0, sizeof a); a.sin_family = AF_INET; a.sin_port = htons((uint16_t)port);
    if (inet_pton(AF_INET, host, &a.sin_addr) != 1) return -1;
    fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return -1;
    setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &no_delay, sizeof no_delay);
    if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0) { close(fd); return -1; }
    if (connect(fd, (struct sockaddr *)&a, sizeof a) < 0 && errno != EINPROGRESS) {
        close(fd); return -1;
    }
    if (!ready(fd, POLLOUT, bd_mono_ms() + 1000) ||
        getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &len) || error) {
        close(fd); return -1;
    }
    return fd;
}

/* The fixed callers below supply the UDS service, target, and response prefix. */
static int exchange(int fd, unsigned ecu, const unsigned char *uds, unsigned uds_size,
                    const unsigned char *prefix, unsigned prefix_size,
                    unsigned char *reply, unsigned reply_size, unsigned timeout_ms,
                    unsigned *negative) {
    unsigned char request[12] = {0,0,0,0,0,1,0xf4,0};
    unsigned char header[6], body[4096];
    uint64_t deadline = bd_mono_ms() + timeout_ms;
    if (uds_size > sizeof request - 8) return -1;
    request[3] = (unsigned char)(uds_size+2);
    request[7] = (unsigned char)ecu;
    memcpy(request+8,uds,uds_size);
    *negative=0;
    if (!transfer(fd,request,8+uds_size,1,deadline)) return -1;
    while (running) {
        uint32_t len;
        unsigned ctrl;
        if (!transfer(fd,header,6,0,deadline)) return -1;
        len=((uint32_t)header[0]<<24)|((uint32_t)header[1]<<16)|((uint32_t)header[2]<<8)|header[3];
        ctrl=((unsigned)header[4]<<8)|header[5];
        if (len>sizeof body || !transfer(fd,body,len,0,deadline)) return -1;
        if (ctrl==0x12) {
            unsigned char alive[8]={0,0,0,2,0,0x13,0,0xf4};
            if (!transfer(fd,alive,8,1,deadline)) return -1;
            continue;
        }
        if (ctrl!=1 || len<3 || body[0]!=ecu || body[1]!=0xf4) continue;
        if (len==5 && body[2]==0x7f && body[3]==uds[0]) {
            if (body[4]==0x78) continue;
            *negative=body[4];
            return -2;
        }
        if (len-2<prefix_size || memcmp(body+2,prefix,prefix_size)) continue;
        if (len-2>reply_size) return -1;
        memcpy(reply,body+2,len-2);
        return (int)(len-2);
    }
    return -1;
}

static unsigned number(const char *s, unsigned lo, unsigned hi) {
    char *end; unsigned long n = strtoul(s, &end, 10);
    if (!*s || *end || n < lo || n > hi) { fprintf(stderr, "Invalid number: %s\n", s); exit(2); }
    return (unsigned)n;
}

#ifdef ENGINE_CODES_ONLY
static void fault_codes(int fd, int clear) {
    /* Fault, pending, confirmed, failed since clear, or warning requested.
     * Bits 0x10 and 0x40 only report incomplete tests. */
    enum { fault_status = 0xaf };
    const unsigned char read_request[]={0x19,0x02,fault_status}, read_reply[]={0x59,0x02};
    const unsigned char clear_request[]={0x14,0xff,0xff,0xff}, clear_reply[]={0x54};
    unsigned char reply[4094];
    unsigned negative;
    unsigned supported_fault_status;
    int n, i, count=0, shown=0;
    if (fd<0) { puts("Diagnostic gateway unavailable"); return; }
    if (clear) {
        n=exchange(fd,0x12,clear_request,4,clear_reply,1,reply,sizeof reply,1800,&negative);
        if (n!=1) {
            if (n==-2) printf("Clear rejected (NRC %02X)\n",negative);
            else puts("Clear result unknown: read codes again");
            return;
        }
    }
    n=exchange(fd,0x12,read_request,3,read_reply,2,reply,sizeof reply,1800,&negative);
    if (n==-2) { printf("Read rejected (NRC %02X)\n",negative); return; }
    if (n<3 || (n-3)%4!=0) { puts(clear ? "Clear acknowledged; recheck unavailable" : "No valid code response"); return; }
    supported_fault_status=reply[2] & fault_status;
    for (i=3;i<n;i+=4)
        if (reply[i+3] & supported_fault_status) ++count;
    printf("Engine: %d %s%s\n",count,
           count==1 ? "code" : "codes",clear ? " after clear" : "");
    for (i=3;i<n && shown<128;i+=4) {
        if (!(reply[i+3] & supported_fault_status)) continue;
        printf("%02X%02X%02X  status %02X\n",reply[i],reply[i+1],reply[i+2],reply[i+3]);
        ++shown;
    }
    if (count>128) puts("More codes returned; first 128 shown");
}

int main(int argc, char **argv) {
    const char *host=0;
    unsigned port=6801;
    int mode=0, lock, gw, i;
    for (i=1;i<argc;++i) {
        if (!strcmp(argv[i],"--host") && i+1<argc && !host) host=argv[++i];
        else if (!strcmp(argv[i],"--port") && i+1<argc) port=number(argv[++i],1,65535);
        else if (!strcmp(argv[i],"--codes") && !mode) mode=1;
        else if (!strcmp(argv[i],"--clear") && !mode) mode=2;
        else break;
    }
    if (i!=argc || !host || !mode) {
        fprintf(stderr,"Usage: %s --host IPv4 [--port 6801] (--codes | --clear)\n",argv[0]);
        return 2;
    }
    signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGPIPE,SIG_IGN);
    lock=reserve_gateway();
    if (lock<0) {
        puts(errno==EADDRINUSE ? "Diagnostics busy. Wait a moment and try again."
                               : "Could not reserve the diagnostics connection");
        return 0;
    }
    gw=connect_gw(host,port);
    fault_codes(gw,mode==2);
    if (gw>=0) close(gw);
    close(lock);
    return 0;
}
#elif defined(MHD_MAPS_ONLY)
int main(int argc, char **argv) {
    const char *host=0;
    unsigned port=6801, slot=0, negative;
    int fd, lock, n, i;
    unsigned char request[]={0x2f,0x01,0}, prefix[]={0x6f,0x01,0}, reply[16];
    for (i=1;i<argc;++i) {
        if (!strcmp(argv[i],"--host") && i+1<argc && !host) host=argv[++i];
        else if (!strcmp(argv[i],"--port") && i+1<argc) port=number(argv[++i],1,65535);
        else if (!strcmp(argv[i],"--map") && i+1<argc && !slot) slot=number(argv[++i],1,4);
        else return 2;
    }
    if (!host || !slot) return 2;
    signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGPIPE,SIG_IGN);
    lock=reserve_gateway();
    if (lock<0) { puts("Diagnostics busy. Wait a moment and try again."); return 0; }
    fd=connect_gw(host,port);
    if (fd<0) {
        puts("Diagnostic gateway unavailable");
        close(lock);
        return 0;
    }
    /* MHD+ slot selector documented in idrive7-apps, tune.js at cca7f9a.
       The echoed value acknowledges the request but does not read active state. */
    request[2]=prefix[2]=(unsigned char)(slot-1);
    n=exchange(fd,0x12,request,sizeof request,prefix,sizeof prefix,reply,sizeof reply,1600,&negative);
    if (n>=3) printf("Confirmed: Map %u\n",slot);
    else if (n==-2) printf("Map %u refused by DME (NRC %02X)\n",slot,negative);
    else puts("Map result unknown. Check Current Map in MHD.");
    close(fd);
    close(lock);
    return 0;
}
#elif defined(SHIFT_DECK_ONLY)
#include "drive_frame.h"

/* RPM is read every cycle; speed is read every SPEED_EVERY cycles and the
 * last value is reused in between. See SHIFT_FAST_RPM in drive_frame.h. */
#if SHIFT_FAST_RPM
enum { SHIFT_CYCLE_MS = 30, SHIFT_MIN_IDLE_MS = 5, SPEED_EVERY = 4 };
#else
enum { SHIFT_CYCLE_MS = 70, SHIFT_MIN_IDLE_MS = 10, SPEED_EVERY = 1 };
#endif

static int read_live_pid(int fd, unsigned char pid, int width, int *value) {
    unsigned char req[]={0x01,pid}, prefix[]={0x41,pid}, reply[4];
    unsigned negative;
    int n=exchange(fd,0x12,req,sizeof req,prefix,sizeof prefix,reply,sizeof reply,250,&negative);
    *value=-1;
    if (n==width+2) *value=width==1 ? reply[2] : ((unsigned)reply[2]*256+reply[3])/4;
    return n!=-1;
}

int main(int argc, char **argv) {
    const char *host=0;
    unsigned port=6801, udp_port=DRIVE_PORT, cycles=0, count=0;
    pid_t parent=getppid();
    struct sockaddr_in dest;
    int fd=-1, sock, lock=-1, i, kph=-1;
    unsigned speed_due=0;
    uint64_t lease_until=0, retry_at=0;
    for (i=1;i<argc;++i) {
        if (!strcmp(argv[i],"--host") && i+1<argc && !host) host=argv[++i];
        else if (!strcmp(argv[i],"--port") && i+1<argc) port=number(argv[++i],1,65535);
        else if (!strcmp(argv[i],"--udp-port") && i+1<argc) udp_port=number(argv[++i],1,65535);
        else if (!strcmp(argv[i],"--cycles") && i+1<argc) cycles=number(argv[++i],1,1000);
        else return 2;
    }
    if (!host || parent<=1) return 2;
    signal(SIGINT,stop); signal(SIGTERM,stop); signal(SIGPIPE,SIG_IGN);
    sock=socket(AF_INET,SOCK_DGRAM,0);
    if (sock<0) return 1;
    memset(&dest,0,sizeof dest);
    dest.sin_family=AF_INET;
    dest.sin_port=htons((uint16_t)udp_port);
    inet_pton(AF_INET,"127.0.0.1",&dest.sin_addr);
    while (running && getppid()==parent && (!cycles || count<cycles)) {
        struct drive_frame f;
        int rpm=-1;
        uint64_t start=bd_mono_ms(), elapsed;
        if (fd<0 && start>=retry_at) {
            lock=reserve_gateway();
            if (lock>=0) {
                fd=connect_gw(host,port);
                if (fd<0) {
                    release_gateway(&fd,&lock);
                    retry_at=bd_mono_ms()+DIAGNOSTIC_RETRY_MS;
                } else {
                    lease_until=bd_mono_ms()+DIAGNOSTIC_LEASE_MS;
                    speed_due=0;
                }
            }
        }
        if (fd>=0) {
            int ok=read_live_pid(fd,0x0c,2,&rpm);
            if (ok && speed_due==0) ok=read_live_pid(fd,0x0d,1,&kph);
            if (!ok) {
                release_gateway(&fd,&lock);
                kph=-1;
                retry_at=bd_mono_ms()+DIAGNOSTIC_RETRY_MS;
            } else {
                speed_due=(speed_due+1)%SPEED_EVERY;
                f.sampled_ms=bd_mono_ms();
                f.rpm=rpm;
                f.kph=kph;
                if (sendto(sock,&f,sizeof f,0,(struct sockaddr *)&dest,sizeof dest)!=(ssize_t)sizeof f) break;
#if SHIFT_FAST_RPM
                if (gateway_yield_or_renew(lock,f.sampled_ms,&lease_until)) {
#else
                if (gateway_should_yield(lock,f.sampled_ms,lease_until)) {
#endif
                    release_gateway(&fd,&lock);
                    kph=-1;
                    retry_at=f.sampled_ms+DIAGNOSTIC_YIELD_MS;
                }
            }
        }
        ++count;
        elapsed=bd_mono_ms()-start;
        if (running) poll(0,0,elapsed<SHIFT_CYCLE_MS ? (int)(SHIFT_CYCLE_MS-elapsed) : SHIFT_MIN_IDLE_MS);
    }
    release_gateway(&fd,&lock);
    close(sock);
    return 0;
}
#else
static int trace_reads = 0;
static void pause_ms(int ms) { while (running && poll(0, 0, ms) < 0 && errno == EINTR) {} }

static int read_did(int fd, unsigned ecu, unsigned did, double *value) {
    unsigned char request[]={0x22,(unsigned char)(did>>8),(unsigned char)did};
    unsigned char prefix[]={0x62,request[1],request[2]};
    unsigned char reply[4094];
    unsigned negative, i;
    int n=exchange(fd,ecu,request,3,prefix,3,reply,sizeof reply,500,&negative);
    int decoded;
    if (n==-2) {
        fprintf(stderr,"ECU %02X DID %04X NRC %02X\n",ecu,did,negative);
        return 0;
    }
    if (n<0) return -1;
    decoded=bd_decode_did(did,reply+3,(size_t)n-3,value);
    if (trace_reads) {
        fprintf(stderr,"RAW mono_ms=%llu ecu=%02X did=%04X payload=",
                (unsigned long long)bd_mono_ms(),ecu,did);
        for (i=3;i<(unsigned)n;++i) fprintf(stderr,"%02X",reply[i]);
        if (decoded) fprintf(stderr," decoded=%.6f\n",*value);
        else fprintf(stderr," unavailable%s\n",did==0x4a49 && n==5 &&
                     reply[3]==255 && reply[4]==255 ?
                     " reason=ambiguous-FFFF" : " reason=invalid-or-out-of-range");
    }
    return decoded;
}

static int send_frame(int sock, struct sockaddr_in *dest, bd_frame *f, uint64_t *observed) {
    char packet[BD_MAX_PACKET];
    uint64_t now = bd_mono_ms();
    unsigned i;
    int len;
    f->sent_ms=now;
    for (i = 0; i < BD_COUNT; ++i) {
        uint64_t age = now - observed[i];
        f->age_ms[i] = age > 999999 ? 999999 : (unsigned)age;
    }
    len = bd_encode(f, packet, sizeof packet);
    return len > 0 && sendto(sock, packet, (size_t)len, 0,
        (struct sockaddr *)dest, sizeof *dest) == len;
}
int main(int argc, char **argv) {
    const char *host = 0;
    unsigned port = 6801, udp_port = BD_UDP_PORT, cycles = 0, count = 0, i;
    int demo = 0, sock, gw = -1, no_trans = 0, session = 0;
    int lock = -1;
    pid_t parent = getppid();
    struct sockaddr_in dest;
    bd_frame f;
    uint64_t observed[BD_COUNT] = {0};
    double ambient = 0;
    uint64_t ambient_time = 0;
    int have_ambient = 0;
    const unsigned tick_ms = 150;
    const unsigned minimum_idle_ms = 10;
    /* Each slow signal has its own deadline. Stagger temperature reads
     * after the first scan so they do not delay the fast gauges together. */
    const unsigned dids[] = {0x4201,0x4205,0x5889,0x4a49,0x580f,0xda12,0x56d7,0x586f,0x4300};
    const unsigned periods[] = {1000,0,0,0,2000,2000,0,0,2000};
    const unsigned first_delay[] = {900,0,0,0,500,1150,0,0,1700};
    uint64_t due[sizeof dids/sizeof dids[0]] = {0};
    uint64_t failed_until[sizeof dids/sizeof dids[0]] = {0};
    uint64_t lease_until=0, retry_at=0;
    int scanned=0;
    for (i = 1; i < (unsigned)argc; ++i) {
        if (!strcmp(argv[i], "--demo")) demo = 1;
        else if (!strcmp(argv[i], "--session")) session = 1;
        else if (!strcmp(argv[i], "--trace")) trace_reads = 1;
        else if (!strcmp(argv[i], "--no-trans")) no_trans = 1;
        else if (!strcmp(argv[i], "--host") && i+1 < (unsigned)argc) host = argv[++i];
        else if (!strcmp(argv[i], "--port") && i+1 < (unsigned)argc) port = number(argv[++i],1,65535);
        else if (!strcmp(argv[i], "--udp-port") && i+1 < (unsigned)argc) udp_port = number(argv[++i],1,65535);
        else if (!strcmp(argv[i], "--cycles") && i+1 < (unsigned)argc) cycles = number(argv[++i],1,1000000);
        else { fprintf(stderr, "Usage: %s (--host IPv4 | --demo) [--port 6801] [--udp-port 42860] [--cycles N] [--no-trans] [--trace] [--session]\n", argv[0]); return 2; }
    }
    if ((!host && !demo) || (host && demo)) { fprintf(stderr, "Choose --host or --demo, never both.\n"); return 2; }
    signal(SIGINT, stop); signal(SIGTERM, stop); signal(SIGPIPE, SIG_IGN);
    if (session && (parent <= 1 || demo)) return 2;
    sock = socket(AF_INET, SOCK_DGRAM, 0);
    if (sock < 0) { perror("UDP socket"); return 1; }
    memset(&dest,0,sizeof dest); dest.sin_family=AF_INET; dest.sin_port=htons((uint16_t)udp_port);
    inet_pton(AF_INET,"127.0.0.1",&dest.sin_addr); /* local process IPC only */
    memset(&f,0,sizeof f); f.demo=(unsigned)demo;
    fprintf(stderr, "%s: 2016 340i candidate profile; validate against ISTA/logger.\n",
        demo ? "DEMO / NO VEHICLE CONNECTION" : "READ ONLY / HSFZ");
    while (running && (!cycles || count < cycles) && (!session || getppid() == parent)) {
        uint64_t start = bd_mono_ms();
        f.seq++;
        if (demo) {
            double t = count * (tick_ms / 1000.0);
            f.value[0]=12+8*sin(t); f.value[1]=13.2+.6*sin(t);
            f.value[2]=8+3*sin(t); f.value[3]=44+2*sin(t); f.value[4]=88;
            f.value[BD_RAIL]=95+22*sin(t); f.value[BD_OIL]=2.5+.5*sin(t);
            f.value[BD_COOLANT]=86+3*sin(t);
            f.valid=(1u<<BD_COUNT)-1;
            for (i=0;i<BD_COUNT;++i) observed[i]=bd_mono_ms();
        } else {
            int full_scan = session ? !scanned : gw < 0;
            if (gw < 0 && start >= retry_at) {
                if (session) lock=reserve_gateway();
                if (!session || lock>=0) {
                    gw=connect_gw(host,port);
                    if (gw<0 && session) {
                        release_gateway(&gw,&lock);
                        retry_at=bd_mono_ms()+DIAGNOSTIC_RETRY_MS;
                    } else if (session) lease_until=bd_mono_ms()+DIAGNOSTIC_LEASE_MS;
                }
            }
            for (i=0; gw >= 0 && i < sizeof dids / sizeof dids[0] && running; ++i) {
                double v = 0;
                int rc;
                if (no_trans && dids[i] == 0xda12) continue;
                if (session && start < failed_until[i]) continue;
                if (!full_scan && periods[i] && start < due[i]) continue;
                if (full_scan) due[i]=start+first_delay[i];
                else if (periods[i]) {
                    due[i]+=periods[i];
                    if (due[i]<=start) due[i]=start+periods[i];
                }
                if (i != 0) f.valid &= ~(1u << (i-1));
                rc = read_did(gw, dids[i] == 0xda12 ? 0x18 : 0x12, dids[i], &v);
                if (rc < 0) {
                    release_gateway(&gw,&lock);
                    if (session) {
                        failed_until[i]=bd_mono_ms()+2000;
                        retry_at=bd_mono_ms()+DIAGNOSTIC_RETRY_MS;
                    } else {
                        f.valid=0;
                        have_ambient=0;
                    }
                    break;
                }
                if (i == 0) {
                    have_ambient=rc != 0;
                    if (have_ambient) { ambient=v; ambient_time=bd_mono_ms(); }
                    continue;
                }
                if (!rc) continue;
                if (i == 1) {
                    if (!have_ambient || bd_mono_ms()-ambient_time > 1800) continue;
                    v = (v-ambient) * .145037738; /* kPa absolute - measured ambient -> gauge psi */
                }
                if (i == 2) v *= 14.7; /* measured lambda -> gasoline-equivalent AFR */
                f.value[i-1]=v; observed[i-1]=bd_mono_ms(); f.valid |= 1u << (i-1);
            }
            if (gw>=0) {
                scanned=1;
                if (session && gateway_should_yield(lock,bd_mono_ms(),lease_until)) {
                    release_gateway(&gw,&lock);
                    retry_at=bd_mono_ms()+DIAGNOSTIC_YIELD_MS;
                }
            }
        }
        if (!send_frame(sock,&dest,&f,observed)) { perror("send frame"); running=0; }
        ++count;
        {
            uint64_t elapsed = bd_mono_ms()-start;
            unsigned interval = session && gw<0 ? 55 : tick_ms;
            unsigned idle_ms = elapsed < interval ? (unsigned)(interval-elapsed) : 0;
            if (!demo && idle_ms < minimum_idle_ms) idle_ms = minimum_idle_ms;
            if (idle_ms) pause_ms((int)idle_ms);
        }
    }
    release_gateway(&gw,&lock);
    close(sock);
    return 0;
}
#endif
