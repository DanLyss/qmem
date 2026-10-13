/*
 * pattern=roundrobin: walk the range in a circle, going on in the next
 * epoch from where the same range stopped. Every page is touched at the
 * planned rate; random pages would leave about 1/e of them untouched, and
 * DAMON would see the range cooler than planned.
 */
#include <stddef.h>

#include "profile.h"

static void init(struct access *a, uint64_t seed)
{
	/* a new range starts at a random page */
	a->cursor = seed % a->pages;
}

static void epoch(struct access *a, const struct access *prev)
{
	if (prev)
		a->cursor = prev->cursor;
}

static uint64_t next(struct access *a)
{
	uint64_t p = a->cursor;

	a->cursor = (p + 1) % a->pages;
	return p;
}

const struct generator_ops gen_roundrobin = {
	.name = "roundrobin", .init = init, .epoch = epoch, .next = next,
};
