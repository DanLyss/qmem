/*
 * pattern=random (STUB): an equally likely random page of the range.
 * pattern=poisson (STUB): the same for now; the touches still come at the
 * scheduler's ticks, not at Poisson moments.
 */
#include <stddef.h>

#include "profile.h"

static void init(struct access *a, uint64_t seed)
{
	a->rng = seed ? seed : 1;
}

static void epoch(struct access *a, const struct access *prev)
{
	if (prev)
		a->rng = prev->rng;
}

static uint64_t next(struct access *a)
{
	/* xorshift64* */
	a->rng ^= a->rng >> 12;
	a->rng ^= a->rng << 25;
	a->rng ^= a->rng >> 27;
	return a->rng * 0x2545F4914F6CDD1DULL % a->pages;
}

const struct generator_ops gen_random = {
	.name = "random", .init = init, .epoch = epoch, .next = next,
};

const struct generator_ops gen_poisson = {
	.name = "poisson",
	.stub = "pattern=poisson is a stub: replayed as pattern=random",
	.init = init, .epoch = epoch, .next = next,
};
