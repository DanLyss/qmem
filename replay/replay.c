/*
 * qmem-replay: replay a memory access profile (docs/formats/profile.md).
 *
 * This file is the scheduler: 1 ms ticks on absolute deadlines, map, unmap
 * and populate events on the profile's timeline, epochs and their touches.
 * Work that does not fit into its tick is dropped, not queued, so the
 * replay never drifts. The other parts are listed in profile.h.
 *
 * usage: qmem-replay <profile> [--seed N] [--stats <file>]
 *                    [--log <file> [--log-level events|ticks|touches]]
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
static uint64_t lagged_ticks, end_ms;
static double max_lag_ms;

uint64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* Do one tick of the epoch's touches; stop at deadline_ns. Returns 0 if the
 * work did not fit. */
static int exec_tick(uint32_t epoch, uint64_t tick, uint64_t tick_ms, uint64_t deadline_ns)
{
	struct epoch *e = &epochs[epoch];
	uint64_t before = done;
	int fits = 1;

	for (uint32_t i = e->first; i < e->first + e->n && fits; i++) {
		struct access *a = &accesses[i];
		uint64_t n, k;

		/* the epsilon keeps rounding errors of the running sum from
		 * losing a touch: 100 x 1.92 adds up to 191.99999… */
		a->carry += a->rate * tick_ms / 1000.0;
		n = (uint64_t)(a->carry + 1e-9);
		a->carry -= n;
		for (k = 0; k < n; k++) {
			if ((k & 1023) == 1023 && now_ns() >= deadline_ns) {
				fits = 0;
				break;
			}
			done += memory_touch(a->region, a->off / page + a->gen->next(a),
					     a->write_ratio);
		}
		if (n)
			log_tick(tick, epoch, i - e->first, n, k, a->carry);
	}
	e->done += done - before;
	return fits;
}

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

/* 1 ms ticks spread an epoch's touches over the epoch. They must be
 * shorter than DAMON's sampling interval (5 ms by default): with
 * 10 ms ticks the touches came in a burst and half of DAMON's samples
 * fell into the pause, so replay looked half as hot. */
static const uint64_t tick_ms = 1;

static uint64_t start_ns;
static uint32_t next_event, next_epoch;
static int cur = -1, cur_ended;		/* the current epoch, -1 before the first */

/* The length of the run and the planned touches of each epoch. */
static void plan(void)
{
	if (n_events)
		end_ms = events[n_events - 1].t_ms;
	for (uint32_t i = 0; i < n_epochs; i++) {
		if (epochs[i].t_ms + epochs[i].dur_ms > end_ms)
			end_ms = epochs[i].t_ms + epochs[i].dur_ms;
		for (uint32_t j = epochs[i].first; j < epochs[i].first + epochs[i].n; j++)
			epochs[i].planned += (uint64_t)(accesses[j].rate * epochs[i].dur_ms / 1000.0 + 1e-9);
		planned += epochs[i].planned;
	}
}

static int epoch_over(uint64_t t)
{
	return t >= epochs[cur].t_ms + epochs[cur].dur_ms;
}

static void end_epoch(uint64_t t)
{
	if (cur >= 0 && !cur_ended && epoch_over(t)) {
		log_epoch(cur, 0, t);
		cur_ended = 1;
	}
}

/* Map, unmap and populate events due by tick t. */
static void exec_events(uint64_t t)
{
	while (next_event < n_events && events[next_event].t_ms <= t) {
		uint64_t n = memory_apply(&events[next_event]);

		log_event(&events[next_event++], t, n);
	}
}

/* Epochs due by tick t; a range goes on with the fraction of a touch and the
 * generator state of the same range in the previous epoch: short epochs at
 * low rates would lose them. */
static void start_epochs(uint64_t t)
{
	while (next_epoch < n_epochs && epochs[next_epoch].t_ms <= t) {
		if (cur >= 0 && !cur_ended)
			log_epoch(cur, 0, t);
		cur = next_epoch++;
		cur_ended = 0;
		log_epoch(cur, 1, t);
		for (uint32_t j = epochs[cur].first; j < epochs[cur].first + epochs[cur].n; j++) {
			struct access *a = &accesses[j];
			const struct access *prev = a->prev >= 0 ? &accesses[a->prev] : NULL;

			a->carry = prev ? prev->carry : 0;
			a->gen->epoch(a, prev);
		}
	}
}

static void note_lag(double lag_ms)
{
	if (lag_ms > epochs[cur].max_lag_ms)
		epochs[cur].max_lag_ms = lag_ms;
	if (lag_ms > max_lag_ms)
		max_lag_ms = lag_ms;
}

/* Tick t did not fit: drop the missed ticks instead of queueing them.
 * Returns the tick to go on from. */
static uint64_t drop_late_ticks(uint64_t t)
{
	uint64_t now = now_ns() - start_ns, now_ms = now / 1000000ULL;
	uint64_t next = now_ms / tick_ms * tick_ms, skip;

	/* the ticks after the drop start on time: only here the lag shows */
	note_lag(now / 1e6 - (t + tick_ms));
	lagged_ticks++;
	if (next <= t + tick_ms) {
		log_drop(t, 0);
		return t;
	}
	skip = (next - t) / tick_ms - 1;
	lagged_ticks += skip;
	log_drop(t, skip);
	return next - tick_ms;
}

static void replay(uint64_t *wall_ns)
{
	plan();
	start_ns = now_ns();
	log_start(start_ns);
	for (uint64_t t = 0; t < end_ms; t += tick_ms) {
		uint64_t due = start_ns + t * 1000000ULL;
		double lag_ms;

		sleep_until(due);
		lag_ms = (now_ns() - due) / 1e6;
		end_epoch(t);
		exec_events(t);
		start_epochs(t);
		if (cur < 0 || epoch_over(t))
			continue;
		note_lag(lag_ms);
		if (!exec_tick(cur, t, tick_ms, due + tick_ms * 1000000ULL))
			t = drop_late_ticks(t);
	}
	if (cur >= 0 && !cur_ended)
		log_epoch(cur, 0, end_ms);
	exec_events(end_ms);	/* the ones at end_ms itself */
	sleep_until(start_ns + end_ms * 1000000ULL);
	*wall_ns = now_ns() - start_ns;
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
	const char *path = NULL, *stats = NULL, *log_path = NULL;
	int level = LOG_EVENTS;
	int seed_set = 0, level_set = 0;
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
		} else if (!strcmp(argv[i], "--log") && i + 1 < argc) {
			log_path = argv[++i];
		} else if (!strcmp(argv[i], "--log-level") && i + 1 < argc) {
			level = log_parse_level(argv[++i]);
			if (level < 0)
				goto usage;
			level_set = 1;
		} else if (argv[i][0] == '-' || path) {
			goto usage;
		} else {
			path = argv[i];
		}
	}
	if (!path || (level_set && !log_path))
		goto usage;
	if (log_path)
		log_level = level;

	load(path);
	if (seed_set)
		seed = seed_arg;
	prepare();
	replay(&wall_ns);
	if (stats)
		write_stats(stats);
	if (log_path)
		log_write(log_path, end_ms, lagged_ticks);

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
		" [--log <file> [--log-level events|ticks|touches]]\n", argv[0]);
	return 2;
}
