/*
 * log: what replay did, step by step (docs/formats/replay_log.md), for tests
 * of the algorithm and comparisons with other workload generators.
 *
 * Records are kept in memory and written after the run, so writing does not
 * disturb the timing. With log_level LOG_OFF nothing is recorded.
 */
#define _GNU_SOURCE
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "profile.h"

int log_level = LOG_OFF;

enum { R_EVENT, R_EPOCH, R_DROP, R_TICK, R_TOUCH };

struct rec {
	uint8_t kind, op;
	uint32_t a, b;
	uint64_t v[4];
	double c;
};

static struct rec *recs;
static size_t n_recs, cap_recs;
static uint64_t start_ns;

static const char *const level_names[] = { "off", "events", "ticks", "touches" };

int log_parse_level(const char *name)
{
	for (int i = LOG_EVENTS; i <= LOG_TOUCHES; i++)
		if (!strcmp(name, level_names[i]))
			return i;
	return -1;
}

static struct rec *add(int kind)
{
	if (n_recs == cap_recs) {
		cap_recs = cap_recs ? 2 * cap_recs : 4096;
		recs = realloc(recs, cap_recs * sizeof(*recs));
		if (!recs) {
			perror("log");
			exit(1);
		}
	}
	memset(&recs[n_recs], 0, sizeof(*recs));
	recs[n_recs].kind = kind;
	return &recs[n_recs++];
}

void log_start(uint64_t ns)
{
	start_ns = ns;
}

void log_event(const struct event *e, uint64_t tick, uint64_t pages)
{
	struct rec *r;

	if (log_level < LOG_EVENTS)
		return;
	r = add(R_EVENT);
	r->op = e->kind;
	r->a = e->region;
	r->v[0] = e->t_ms;
	r->v[1] = tick;
	r->v[2] = e->off;
	r->v[3] = e->len;
	r->c = pages;
}

void log_epoch(uint32_t idx, int start, uint64_t tick)
{
	struct rec *r;

	if (log_level < LOG_EVENTS)
		return;
	r = add(R_EPOCH);
	r->op = start;
	r->a = idx;
	r->v[0] = tick;
}

void log_drop(uint64_t tick, uint64_t skipped_ticks)
{
	struct rec *r;

	if (log_level < LOG_EVENTS)
		return;
	r = add(R_DROP);
	r->v[0] = tick;
	r->v[1] = skipped_ticks;
}

void log_tick(uint64_t tick, uint32_t epoch, uint32_t access, uint64_t n, uint64_t n_done,
	      double carry)
{
	struct rec *r;

	if (log_level < LOG_TICKS)
		return;
	r = add(R_TICK);
	r->a = epoch;
	r->b = access;
	r->v[0] = tick;
	r->v[1] = n;
	r->v[2] = n_done;
	r->c = carry;
}

void log_touch(uint32_t region, uint64_t p, int op)
{
	struct rec *r;

	if (log_level < LOG_TOUCHES)
		return;
	r = add(R_TOUCH);
	r->op = op;
	r->a = region;
	r->v[0] = now_ns() - start_ns;
	r->v[1] = p;
}

static const char *event_name(int kind)
{
	return kind == MAP ? "map" : kind == UNMAP ? "unmap" : "populate";
}

void log_write(const char *path, uint64_t end_ms, uint64_t lagged_ticks)
{
	static const char *const ops[] = { "skip", "r", "w" };
	FILE *f = fopen(path, "w");

	if (!f) {
		perror(path);
		exit(1);
	}
	fprintf(f, "start seed=%" PRIu64 " regions=%" PRIu32 " events=%" PRIu32
		" epochs=%" PRIu32 " end_ms=%" PRIu64 " level=%s\n",
		seed, n_regions, n_events, n_epochs, end_ms, level_names[log_level]);
	for (uint32_t i = 0; i < n_regions; i++)
		fprintf(f, "region id=%" PRIu32 " pages=%" PRIu64 " base=0x%lx\n",
			i, regions[i].npages, (unsigned long)regions[i].base);
	for (size_t i = 0; i < n_recs; i++) {
		const struct rec *r = &recs[i];
		const struct epoch *e;

		switch (r->kind) {
		case R_EVENT:
			fprintf(f, "event kind=%s t=%" PRIu64 " tick=%" PRIu64 " region=%" PRIu32
				" off=%" PRIu64 " len=%" PRIu64, event_name(r->op), r->v[0],
				r->v[1], r->a, r->v[2], r->v[3]);
			if (r->op == POPULATE)
				fprintf(f, " pages=%.0f", r->c);
			fputc('\n', f);
			break;
		case R_EPOCH:
			e = &epochs[r->a];
			fprintf(f, "epoch what=%s idx=%" PRIu32 " t=%" PRIu64 " dur=%" PRIu64
				" tick=%" PRIu64, r->op ? "start" : "end", r->a, e->t_ms,
				e->dur_ms, r->v[0]);
			if (!r->op)
				fprintf(f, " planned=%" PRIu64 " done=%" PRIu64, e->planned, e->done);
			fputc('\n', f);
			break;
		case R_DROP:
			fprintf(f, "drop tick=%" PRIu64 " skipped=%" PRIu64 "\n", r->v[0], r->v[1]);
			break;
		case R_TICK:
			fprintf(f, "tick tick=%" PRIu64 " epoch=%" PRIu32 " access=%" PRIu32
				" n=%" PRIu64 " done=%" PRIu64 " carry=%.4f\n", r->v[0], r->a, r->b,
				r->v[1], r->v[2], r->c);
			break;
		case R_TOUCH:
			fprintf(f, "touch t_ns=%" PRIu64 " region=%" PRIu32 " page=%" PRIu64
				" op=%s\n", r->v[0], r->a, r->v[1], ops[r->op]);
			break;
		}
	}
	fprintf(f, "end planned=%" PRIu64 " done=%" PRIu64 " skipped=%" PRIu64
		" populated=%" PRIu64 " lagged_ticks=%" PRIu64 "\n",
		planned, done, skipped, populated, lagged_ticks);
	fclose(f);
}
