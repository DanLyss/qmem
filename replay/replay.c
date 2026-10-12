/*
 * qmem-replay: replay a memory access profile (iteration 1).
 *
 * Reads a text profile (docs/modules/replay.md), maps and unmaps parts of
 * anonymous regions on its timeline and touches their pages at the given
 * rates in 10 ms ticks on absolute deadlines. Work that does not fit into
 * its tick is dropped, not queued, so the replay never drifts.
 *
 * usage: qmem-replay <profile.txt> [--seed N] [--stats <file>]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

struct region {
	uint64_t size, npages;
	uint8_t *base;		/* reserved on the first map */
	uint8_t *mapped;	/* bitmap: page is mapped */
	uint8_t *touched;	/* bitmap: page was touched since it was mapped */
};

struct event {
	uint64_t t_ms;
	int map;		/* 1 = map, 0 = unmap */
	uint32_t region;
	uint64_t off, len;
};

struct access {
	uint32_t region;
	uint64_t off, len;
	double rate;		/* page touches per second */
	double write_ratio;
	double carry;		/* fractional touches left from the last tick */
};

struct epoch {
	uint64_t t_ms;
	uint32_t first, n;	/* range in accesses[] */
	uint64_t planned, done;
	double max_lag_ms;
};

static struct region *regions;
static struct event *events;
static struct access *accesses;
static struct epoch *epochs;
static uint32_t n_regions, n_events, n_accesses, n_epochs;
static uint64_t epoch_ms, seed = 42;
static long page;

static uint64_t rng;
static uint64_t planned, done, skipped, checksum, lagged_ticks;
static double max_lag_ms;

/* ------------------------------------------------------------ helpers */

static void *grow(void *p, uint32_t n, size_t size)
{
	/* double the capacity at powers of two */
	if (n & (n - 1))
		return p;
	p = realloc(p, (n ? 2 * (size_t)n : 1) * size);
	if (!p) {
		perror("realloc");
		exit(1);
	}
	return p;
}

static int line_no;

static void bad(const char *fmt, ...)
{
	va_list ap;

	fprintf(stderr, "profile:%d: ", line_no);
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
	exit(2);
}

static uint64_t num(const char *s)
{
	char *end;
	uint64_t v;

	if (!s || *s == '-')
		bad("expected a number");
	errno = 0;
	v = strtoull(s, &end, 10);
	if (errno || *end || end == s)
		bad("bad number '%s'", s);
	return v;
}

static double real(const char *s)
{
	char *end;
	double v;

	if (!s)
		bad("expected a number");
	errno = 0;
	v = strtod(s, &end);
	if (errno || *end || end == s || v < 0)
		bad("bad number '%s'", s);
	return v;
}

static int bit(const uint8_t *map, uint64_t i)
{
	return map[i / 8] >> (i % 8) & 1;
}

static void set_bits(uint8_t *map, uint64_t from, uint64_t n, int v)
{
	for (uint64_t i = from; i < from + n; i++) {
		if (v)
			map[i / 8] |= 1 << (i % 8);
		else
			map[i / 8] &= ~(1 << (i % 8));
	}
}

static uint64_t next_rand(void)
{
	/* xorshift64* */
	rng ^= rng >> 12;
	rng ^= rng << 25;
	rng ^= rng >> 27;
	return rng * 0x2545F4914F6CDD1DULL;
}

static uint64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static void sleep_until(uint64_t ns)
{
	struct timespec ts = { .tv_sec = ns / 1000000000ULL,
			       .tv_nsec = ns % 1000000000ULL };

	while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR)
		;
}

/* ------------------------------------------------------------ profile */

static void check_range(uint32_t r, uint64_t off, uint64_t len, int aligned)
{
	if (r >= n_regions)
		bad("unknown region %" PRIu32, r);
	if (len == 0 || off + len < off || off + len > regions[r].size)
		bad("range %" PRIu64 "+%" PRIu64 " outside region %" PRIu32, off, len, r);
	if (aligned && (off % page || len % page))
		bad("range %" PRIu64 "+%" PRIu64 " is not page aligned", off, len);
}

static void load(const char *path)
{
	FILE *f = fopen(path, "r");
	char *line = NULL;
	size_t cap = 0;
	int header = 0;
	uint64_t last_event = 0, last_epoch = 0;

	if (!f) {
		perror(path);
		exit(1);
	}
	while (getline(&line, &cap, f) >= 0) {
		char *tok[8], *save, *hash = strchr(line, '#');
		int n = 0;

		line_no++;
		if (hash)
			*hash = 0;
		for (char *t = strtok_r(line, " \t\r\n", &save); t && n < 8;
		     t = strtok_r(NULL, " \t\r\n", &save))
			tok[n++] = t;
		if (n == 0)
			continue;
		for (int i = n; i < 8; i++)
			tok[i] = NULL;

		if (!header) {
			if (strcmp(tok[0], "qmem-profile") || !tok[1] || strcmp(tok[1], "1"))
				bad("expected 'qmem-profile 1'");
			header = 1;
		} else if (!strcmp(tok[0], "epoch_ms")) {
			epoch_ms = num(tok[1]);
			if (epoch_ms == 0)
				bad("epoch_ms must be positive");
		} else if (!strcmp(tok[0], "seed")) {
			seed = num(tok[1]);
		} else if (!strcmp(tok[0], "region")) {
			uint64_t id = num(tok[1]), size = num(tok[2]);

			if (id != n_regions)
				bad("regions must be numbered 0, 1, ... in order");
			if (size == 0)
				bad("empty region");
			regions = grow(regions, n_regions, sizeof(*regions));
			regions[n_regions++] = (struct region){
				.size = size, .npages = (size + page - 1) / page };
		} else if (!strcmp(tok[0], "map") || !strcmp(tok[0], "unmap")) {
			struct event e = { .t_ms = num(tok[1]), .map = tok[0][0] == 'm',
					   .region = num(tok[2]), .off = num(tok[3]),
					   .len = num(tok[4]) };

			check_range(e.region, e.off, e.len, 1);
			if (e.t_ms < last_event)
				bad("map/unmap times must not go back");
			last_event = e.t_ms;
			events = grow(events, n_events, sizeof(*events));
			events[n_events++] = e;
		} else if (!strcmp(tok[0], "epoch")) {
			uint64_t t = num(tok[1]);

			if (!epoch_ms)
				bad("epoch before epoch_ms");
			if (n_epochs && t <= last_epoch)
				bad("epoch times must grow");
			last_epoch = t;
			epochs = grow(epochs, n_epochs, sizeof(*epochs));
			epochs[n_epochs++] = (struct epoch){ .t_ms = t, .first = n_accesses };
		} else if (!strcmp(tok[0], "access")) {
			struct access a = { .region = num(tok[1]), .off = num(tok[2]),
					    .len = num(tok[3]), .rate = real(tok[4]),
					    .write_ratio = real(tok[5]) };

			if (!n_epochs)
				bad("access before the first epoch");
			check_range(a.region, a.off, a.len, 0);
			if (a.write_ratio > 1)
				bad("write ratio above 1");
			accesses = grow(accesses, n_accesses, sizeof(*accesses));
			accesses[n_accesses++] = a;
			epochs[n_epochs - 1].n++;
		} else {
			bad("unknown keyword '%s'", tok[0]);
		}
	}
	free(line);
	fclose(f);
	if (!header)
		bad("empty profile");
	if (!epoch_ms)
		bad("missing epoch_ms");
}

/* ------------------------------------------------------------ replay */

static void prepare(void)
{
	for (uint32_t r = 0; r < n_regions; r++) {
		struct region *g = &regions[r];
		size_t bytes = (g->npages + 7) / 8;

		g->mapped = calloc(bytes, 1);
		g->touched = calloc(bytes, 1);
		g->base = mmap(NULL, g->npages * page, PROT_NONE,
			       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
		if (!g->mapped || !g->touched || g->base == MAP_FAILED) {
			perror("prepare");
			exit(1);
		}
	}
}

static void apply(const struct event *e)
{
	struct region *g = &regions[e->region];
	void *p = mmap(g->base + e->off, e->len,
		       e->map ? PROT_READ | PROT_WRITE : PROT_NONE,
		       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED |
		       (e->map ? 0 : MAP_NORESERVE), -1, 0);

	if (p == MAP_FAILED) {
		perror("mmap");
		exit(1);
	}
	set_bits(g->mapped, e->off / page, e->len / page, e->map);
	set_bits(g->touched, e->off / page, e->len / page, 0);
}

static void touch(const struct access *a)
{
	struct region *g = &regions[a->region];
	uint64_t first = a->off / page;
	uint64_t last = (a->off + a->len - 1) / page;
	uint64_t p = first + next_rand() % (last - first + 1);
	volatile uint8_t *addr = g->base + p * page;

	if (!bit(g->mapped, p)) {
		skipped++;
		return;
	}
	checksum = checksum * 31 + p + 1;
	if (!bit(g->touched, p)) {
		/* first touch writes: a read would map the shared zero page */
		set_bits(g->touched, p, 1, 1);
		*addr = 1;
	} else if (a->write_ratio >= 1) {
		*addr += 1;
	} else {
		(void)*addr;
	}
	done++;
}

/* Do one tick of the epoch's touches; stop at deadline_ns. Returns 0 if the
 * work did not fit. */
static int run_tick(struct epoch *e, uint64_t tick_ms, uint64_t deadline_ns)
{
	uint64_t before = done;
	int fits = 1;

	for (uint32_t i = e->first; i < e->first + e->n && fits; i++) {
		struct access *a = &accesses[i];
		uint64_t n;

		a->carry += a->rate * tick_ms / 1000.0;
		n = (uint64_t)a->carry;
		a->carry -= n;
		for (uint64_t k = 0; k < n; k++) {
			if ((k & 1023) == 1023 && now_ns() >= deadline_ns) {
				fits = 0;
				break;
			}
			touch(a);
		}
	}
	e->done += done - before;
	return fits;
}

static void replay(uint64_t *wall_ns)
{
	/* ticks of 10 ms spread an epoch's touches over the epoch */
	uint64_t tick_ms = epoch_ms % 10 ? epoch_ms : 10;
	uint64_t end_ms = 0, start;
	uint32_t ei = 0, pi = 0;
	int cur = -1;

	if (n_events)
		end_ms = events[n_events - 1].t_ms;
	if (n_epochs && epochs[n_epochs - 1].t_ms + epoch_ms > end_ms)
		end_ms = epochs[n_epochs - 1].t_ms + epoch_ms;
	for (uint32_t i = 0; i < n_epochs; i++) {
		for (uint32_t j = epochs[i].first; j < epochs[i].first + epochs[i].n; j++)
			epochs[i].planned += (uint64_t)(accesses[j].rate * epoch_ms / 1000.0);
		planned += epochs[i].planned;
	}

	start = now_ns();
	for (uint64_t t = 0; t < end_ms; t += tick_ms) {
		uint64_t due = start + t * 1000000ULL;
		double lag_ms;

		sleep_until(due);
		lag_ms = (now_ns() - due) / 1e6;
		while (ei < n_events && events[ei].t_ms <= t)
			apply(&events[ei++]);
		while (pi < n_epochs && epochs[pi].t_ms <= t) {
			cur = pi++;
			for (uint32_t j = epochs[cur].first;
			     j < epochs[cur].first + epochs[cur].n; j++)
				accesses[j].carry = 0;
		}
		if (cur < 0 || t >= epochs[cur].t_ms + epoch_ms)
			continue;
		if (lag_ms > epochs[cur].max_lag_ms)
			epochs[cur].max_lag_ms = lag_ms;
		if (lag_ms > max_lag_ms)
			max_lag_ms = lag_ms;
		if (!run_tick(&epochs[cur], tick_ms, due + tick_ms * 1000000ULL)) {
			/* behind schedule: drop the missed ticks instead of queueing */
			uint64_t now_ms = (now_ns() - start) / 1000000ULL;
			uint64_t next = now_ms / tick_ms * tick_ms;

			lagged_ticks++;
			if (next > t + tick_ms) {
				lagged_ticks += (next - t) / tick_ms - 1;
				t = next - tick_ms;
			}
		}
	}
	while (ei < n_events)
		apply(&events[ei++]);
	sleep_until(start + end_ms * 1000000ULL);
	*wall_ns = now_ns() - start;
}

static void write_stats(const char *path)
{
	FILE *f = fopen(path, "w");

	if (!f) {
		perror(path);
		exit(1);
	}
	fprintf(f, "t_ms,planned,done,lag_ms\n");
	for (uint32_t i = 0; i < n_epochs; i++)
		fprintf(f, "%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%.0f\n", epochs[i].t_ms,
			epochs[i].planned, epochs[i].done, epochs[i].max_lag_ms);
	fclose(f);
}

int main(int argc, char **argv)
{
	const char *path = NULL, *stats = NULL;
	int seed_set = 0;
	uint64_t seed_arg = 0, wall_ns;
	struct rusage ru;

	page = sysconf(_SC_PAGESIZE);
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--seed") && i + 1 < argc) {
			char *end;

			seed_arg = strtoull(argv[++i], &end, 10);
			if (*end)
				goto usage;
			seed_set = 1;
		} else if (!strcmp(argv[i], "--stats") && i + 1 < argc) {
			stats = argv[++i];
		} else if (argv[i][0] == '-' || path) {
			goto usage;
		} else {
			path = argv[i];
		}
	}
	if (!path)
		goto usage;

	load(path);
	if (seed_set)
		seed = seed_arg;
	rng = seed ? seed : 1;
	prepare();
	replay(&wall_ns);
	if (stats)
		write_stats(stats);

	getrusage(RUSAGE_SELF, &ru);
	fprintf(stderr, "replay: epochs=%" PRIu32 " planned=%" PRIu64 " done=%" PRIu64
		" skipped=%" PRIu64 " lagged_ticks=%" PRIu64 " max_lag_ms=%.1f"
		" minflt=%ld majflt=%ld"
		" maxrss_kb=%ld cpu_s=%.2f wall_s=%.2f checksum=%016" PRIx64 "\n",
		n_epochs, planned, done, skipped, lagged_ticks, max_lag_ms,
		ru.ru_minflt, ru.ru_majflt,
		ru.ru_maxrss,
		ru.ru_utime.tv_sec + ru.ru_stime.tv_sec +
		(ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6,
		wall_ns / 1e9, checksum);
	return 0;

usage:
	fprintf(stderr, "usage: %s <profile.txt> [--seed N] [--stats <file>]\n",
		argv[0]);
	return 2;
}
