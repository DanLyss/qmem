/*
 * memory manager: regions are reserved before the replay, mapped, unmapped
 * and populated on the profile's timeline, and their pages touched. Only
 * anonymous memory (kind=anon); the loader refuses kind=file.
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>

#include "profile.h"

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

/* A fixed pseudo-random fraction in [0, 1) for each page of each region. */
static double page_fraction(uint32_t region, uint64_t p)
{
	/* splitmix64 */
	uint64_t z = ((uint64_t)region << 40 ^ p) + 0x9E3779B97F4A7C15ULL;

	z = (z ^ z >> 30) * 0xBF58476D1CE4E5B9ULL;
	z = (z ^ z >> 27) * 0x94D049BB133111EBULL;
	z ^= z >> 31;
	return (z >> 11) * 0x1.0p-53;
}

void memory_prepare(void)
{
	for (uint32_t r = 0; r < n_regions; r++) {
		struct region *g = &regions[r];
		size_t bytes = (g->npages + 7) / 8;

		g->mapped = calloc(bytes, 1);
		g->touched = calloc(bytes, 1);
		/* one more page than needed stays PROT_NONE forever: regions are
		 * separate mappings in the original, and two reservations side
		 * by side would let the kernel merge their mapped parts */
		g->base = mmap(NULL, (g->npages + 1) * page, PROT_NONE,
			       MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
		if (!g->mapped || !g->touched || g->base == MAP_FAILED) {
			perror("prepare");
			exit(1);
		}
	}
}

void memory_apply(const struct event *e)
{
	struct region *g = &regions[e->region];
	void *p;

	if (e->kind == POPULATE) {
		/* write every mapped, untouched page once: memory the application
		 * kept resident without DAMON seeing accesses to it */
		for (uint64_t i = e->off / page; i < (e->off + e->len) / page; i++) {
			if (!bit(g->mapped, i) || bit(g->touched, i))
				continue;
			set_bits(g->touched, i, 1, 1);
			g->base[i * page] = 1;
			populated++;
		}
		return;
	}
	p = mmap(g->base + e->off, e->len,
		 e->kind == MAP ? PROT_READ | PROT_WRITE : PROT_NONE,
		 MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED |
		 (e->kind == MAP ? 0 : MAP_NORESERVE), -1, 0);
	if (p == MAP_FAILED) {
		perror("mmap");
		exit(1);
	}
	set_bits(g->mapped, e->off / page, e->len / page, e->kind == MAP);
	set_bits(g->touched, e->off / page, e->len / page, 0);
}

/* Touch page p of region r. Returns 0 (skipped) if it is not mapped. */
int memory_touch(uint32_t r, uint64_t p, double write_ratio)
{
	struct region *g = &regions[r];
	volatile uint8_t *addr = g->base + p * page;

	if (!bit(g->mapped, p)) {
		skipped++;
		return 0;
	}
	checksum = checksum * 31 + p + 1;
	if (!bit(g->touched, p)) {
		/* first touch writes: a read would map the shared zero page */
		set_bits(g->touched, p, 1, 1);
		*addr = 1;
	} else if (page_fraction(r, p) < write_ratio) {
		/* the same write_ratio share of pages is written every time, so
		 * the share of dirty pages per period equals write_ratio */
		*addr += 1;
	} else {
		(void)*addr;
	}
	return 1;
}
