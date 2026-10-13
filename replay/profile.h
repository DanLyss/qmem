/*
 * qmem-replay: the profile in memory and the interfaces between the parts
 * (docs/skeleton.md, section 5):
 *
 *   loader.c      profile v2 (and v1) -> the tables below
 *   memory.c      regions: reservation, map/unmap/populate, touching pages
 *   generators.c  which page of a range is touched next (gen_*.c)
 *   exec.c        who does the touches of a tick (one thread for now)
 *   replay.c      the scheduler: ticks, deadlines, epochs, statistics
 */
#ifndef QMEM_REPLAY_PROFILE_H
#define QMEM_REPLAY_PROFILE_H

#include <stdint.h>

struct region {
	uint64_t size, npages;
	uint8_t *base;		/* reserved before the replay starts */
	uint8_t *mapped;	/* bitmap: page is mapped */
	uint8_t *touched;	/* bitmap: page was touched since it was mapped */
};

enum { UNMAP, MAP, POPULATE };

struct event {
	uint64_t t_ms;
	int kind;		/* UNMAP, MAP or POPULATE */
	uint32_t region;
	uint64_t off, len;
};

struct access;

/* A generator picks the next page of a range (pattern= in the profile). */
struct generator_ops {
	const char *name;				/* the pattern= value */
	const char *stub;				/* warning if it is a stub */
	void (*init)(struct access *a, uint64_t seed);	/* at load time */
	/* at the start of the epoch; prev: the same range with the same
	 * generator in the previous epoch, or NULL */
	void (*epoch)(struct access *a, const struct access *prev);
	uint64_t (*next)(struct access *a);		/* page number in the range */
};

struct access {
	uint32_t region;
	uint64_t off, len, pages;
	double rate;		/* touches of the whole range per second */
	double write_ratio;
	uint32_t threads;	/* stub: always replayed by one thread */
	const struct generator_ops *gen;
	uint64_t cursor, rng;	/* generator state */
	double carry;		/* fractional touches left from the last tick */
	int64_t prev;		/* same range in the previous epoch, or -1 */
};

struct epoch {
	uint64_t t_ms, dur_ms;
	uint32_t first, n;	/* range in accesses[] */
	uint64_t planned, done;
	double max_lag_ms;
};

/* the profile (loader.c) */
extern struct region *regions;
extern struct event *events;
extern struct access *accesses;
extern struct epoch *epochs;
extern uint32_t n_regions, n_events, n_accesses, n_epochs, max_threads;
extern uint64_t epoch_ms, seed;
void load(const char *path);

/* replay.c */
extern long page;
extern uint64_t planned, done, skipped, checksum, populated;

/* generators.c */
const struct generator_ops *generator(const char *name);

/* memory.c */
void memory_prepare(void);
void memory_apply(const struct event *e);
int memory_touch(uint32_t r, uint64_t p, double write_ratio);

/* exec.c */
void exec_init(void);
int exec_tick(struct epoch *e, uint64_t tick_ms, uint64_t deadline_ns);
uint64_t now_ns(void);

#endif
