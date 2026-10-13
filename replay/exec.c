/*
 * executors: who does the touches of a tick. For now one thread, the
 * scheduler's own; threads= is a stub.
 */
#include <stdio.h>
#include <time.h>

#include "profile.h"

uint64_t now_ns(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

void exec_init(void)
{
	if (max_threads > 1)
		fprintf(stderr, "qmem-replay: access threads=%u is a stub: replayed by one "
			"thread\n", max_threads);
}

/* Do one tick of the epoch's touches; stop at deadline_ns. Returns 0 if the
 * work did not fit. */
int exec_tick(struct epoch *e, uint64_t tick_ms, uint64_t deadline_ns)
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
			done += memory_touch(a->region, a->off / page + a->gen->next(a),
					     a->write_ratio);
		}
	}
	e->done += done - before;
	return fits;
}
