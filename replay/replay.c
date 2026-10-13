/*
 * qmem-replay: replay a memory access profile (docs/formats/profile.md).
 *
 * This file is the scheduler: 1 ms ticks on absolute deadlines, map, unmap
 * and populate events on the profile's timeline, epochs and their touches.
 * Work that does not fit into its tick is dropped, not queued, so the
 * replay never drifts. The other parts are listed in profile.h.
 *
 * usage: qmem-replay <profile> [--seed N] [--stats <file>] [--layout <file>]
 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#include "profile.h"

long page;
uint64_t planned, done, skipped, checksum, populated;
static uint64_t lagged_ticks;
static double max_lag_ms;

static void sleep_until(uint64_t ns)
{
	struct timespec ts = { .tv_sec = ns / 1000000000ULL,
			       .tv_nsec = ns % 1000000000ULL };

	while (clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL) == EINTR)
		;
}

/* Generator state at load time; prev links a range to the same range (with
 * the same generator) in the previous epoch. */
static void prepare(void)
{
	uint64_t rng = seed ? seed : 1;

	for (uint32_t i = 0; i < n_epochs; i++) {
		struct epoch *e = &epochs[i], *pe = i ? &epochs[i - 1] : NULL;

		for (uint32_t j = e->first; j < e->first + e->n; j++) {
			struct access *a = &accesses[j];

			for (uint32_t k = pe ? pe->first : 0; pe && k < pe->first + pe->n; k++)
				if (accesses[k].region == a->region && accesses[k].gen == a->gen &&
				    accesses[k].off == a->off && accesses[k].len == a->len)
					a->prev = k;
			/* xorshift64* */
			rng ^= rng >> 12;
			rng ^= rng << 25;
			rng ^= rng >> 27;
			a->gen->init(a, rng * 0x2545F4914F6CDD1DULL);
		}
	}
	memory_prepare();
}

static uint64_t start_boottime_ns;

static void replay(uint64_t *wall_ns)
{
	/* 1 ms ticks spread an epoch's touches over the epoch. They must be
	 * shorter than DAMON's sampling interval (5 ms by default): with
	 * 10 ms ticks the touches came in a burst and half of DAMON's samples
	 * fell into the pause, so replay looked half as hot. */
	uint64_t tick_ms = 1;
	uint64_t end_ms = 0, start;
	uint32_t ei = 0, pi = 0;
	int cur = -1;

	if (n_events)
		end_ms = events[n_events - 1].t_ms;
	for (uint32_t i = 0; i < n_epochs; i++) {
		if (epochs[i].t_ms + epochs[i].dur_ms > end_ms)
			end_ms = epochs[i].t_ms + epochs[i].dur_ms;
		for (uint32_t j = epochs[i].first; j < epochs[i].first + epochs[i].n; j++)
			epochs[i].planned += (uint64_t)(accesses[j].rate * epochs[i].dur_ms / 1000.0);
		planned += epochs[i].planned;
	}

	start = now_ns();
	{
		struct timespec ts;

		clock_gettime(CLOCK_BOOTTIME, &ts);
		start_boottime_ns = ts.tv_sec * 1000000000ULL + ts.tv_nsec;
		memory_start(start_boottime_ns);
	}
	for (uint64_t t = 0; t < end_ms; t += tick_ms) {
		uint64_t due = start + t * 1000000ULL;
		double lag_ms;

		sleep_until(due);
		lag_ms = (now_ns() - due) / 1e6;
		memory_window(t);
		while (ei < n_events && events[ei].t_ms <= t)
			memory_apply(&events[ei++]);
		while (pi < n_epochs && epochs[pi].t_ms <= t) {
			cur = pi++;
			for (uint32_t j = epochs[cur].first;
			     j < epochs[cur].first + epochs[cur].n; j++) {
				struct access *a = &accesses[j];

				a->carry = 0;
				a->gen->epoch(a, a->prev >= 0 ? &accesses[a->prev] : NULL);
			}
		}
		if (cur < 0 || t >= epochs[cur].t_ms + epochs[cur].dur_ms)
			continue;
		if (lag_ms > epochs[cur].max_lag_ms)
			epochs[cur].max_lag_ms = lag_ms;
		if (lag_ms > max_lag_ms)
			max_lag_ms = lag_ms;
		if (!exec_tick(&epochs[cur], tick_ms, due + tick_ms * 1000000ULL)) {
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
		memory_apply(&events[ei++]);
	sleep_until(start + end_ms * 1000000ULL);
	*wall_ns = now_ns() - start;
}

/* Peak RSS of this process in kB. ru_maxrss would also count the process
 * image before execve (e.g. a Python parent), VmHWM does not. */
static long peak_rss_kb(void)
{
	FILE *f = fopen("/proc/self/status", "r");
	char line[256];
	long kb = -1;

	if (!f)
		return -1;
	while (fgets(line, sizeof(line), f))
		if (sscanf(line, "VmHWM: %ld", &kb) == 1)
			break;
	fclose(f);
	return kb;
}

/* Where the regions are in this run and when profile time 0 was, for
 * verify to map the replay's DAMON addresses and times. */
static void write_layout(const char *path)
{
	FILE *f = fopen(path, "w");

	if (!f) {
		perror(path);
		exit(1);
	}
	fprintf(f, "qmem-replay-layout 1\nstart_boottime_ns %" PRIu64 "\n",
		start_boottime_ns);
	for (uint32_t r = 0; r < n_regions; r++)
		fprintf(f, "region %" PRIu32 " 0x%lx %" PRIu64 "\n", r,
			(unsigned long)regions[r].base, regions[r].size);
	fclose(f);
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
	const char *path = NULL, *stats = NULL, *layout = NULL;
	int seed_set = 0;
	uint64_t seed_arg = 0, wall_ns;
	struct rusage ru;

	page = sysconf(_SC_PAGESIZE);
	memory_init();
	for (int i = 1; i < argc; i++) {
		if (!strcmp(argv[i], "--seed") && i + 1 < argc) {
			char *end;

			seed_arg = strtoull(argv[++i], &end, 10);
			if (*end)
				goto usage;
			seed_set = 1;
		} else if (!strcmp(argv[i], "--stats") && i + 1 < argc) {
			stats = argv[++i];
		} else if (!strcmp(argv[i], "--layout") && i + 1 < argc) {
			layout = argv[++i];
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
	exec_init();
	prepare();
	replay(&wall_ns);
	memory_finish();
	if (stats)
		write_stats(stats);
	if (layout)
		write_layout(layout);

	getrusage(RUSAGE_SELF, &ru);
	fprintf(stderr, "replay: epochs=%" PRIu32 " planned=%" PRIu64 " done=%" PRIu64
		" skipped=%" PRIu64 " populated=%" PRIu64 " lagged_ticks=%" PRIu64 " max_lag_ms=%.1f"
		" minflt=%ld majflt=%ld"
		" maxrss_kb=%ld cpu_s=%.2f wall_s=%.2f checksum=%016" PRIx64 "\n",
		n_epochs, planned, done, skipped, populated, lagged_ticks, max_lag_ms,
		ru.ru_minflt, ru.ru_majflt,
		peak_rss_kb(),
		ru.ru_utime.tv_sec + ru.ru_stime.tv_sec +
		(ru.ru_utime.tv_usec + ru.ru_stime.tv_usec) / 1e6,
		wall_ns / 1e9, checksum);
	return 0;

usage:
	fprintf(stderr, "usage: %s <profile> [--seed N] [--stats <file>]"
		" [--layout <file>]\n", argv[0]);
	return 2;
}
