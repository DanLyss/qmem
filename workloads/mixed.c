/*
 * mixed: test workload made of several access patterns over one region.
 *
 * Maps <total_mb> of anonymous memory, writes it once, then for <seconds>
 * runs five patterns side by side, paced so that the whole region gets
 * about <avg_hz> touches per page per second on average:
 *
 *   A  first 10 %    Zipf-like: page i is picked with weight 1 / (i + 1)
 *                    23 % of the touches, 30 % writes
 *   B  next 30 %     a window of 4 % of the region slides across the zone
 *                    over the run, every window page touched evenly;
 *                    20 % of the touches, all writes
 *   C  next 20 %     bursts: 0.5 s on, 1.5 s off, every page touched evenly
 *                    while on; 20 % of the touches on average, reads only
 *   D  next 30 %     every 8th page, touched evenly; 19 % of the touches,
 *                    50 % writes
 *   E  whole region  uniform random; 18 % of the touches, 30 % writes
 *
 * Prints the region and the zones.
 *
 * usage: mixed <total_mb> <seconds> <avg_hz>
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "qmem_gt.h"

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static uint64_t rng = 88172645463325252ULL;

static uint64_t next(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static volatile uint8_t *mem;
static size_t page;
static unsigned long sum;

static void touch(size_t p, int write)
{
	volatile uint8_t *a = mem + p * page;

	if (write)
		*a += 1;
	else
		sum += *a;
	gt_touch(a, write);
}

int main(int argc, char **argv)
{
	if (argc != 4) {
		fprintf(stderr, "usage: %s <total_mb> <seconds> <avg_hz>\n", argv[0]);
		return 2;
	}

	size_t total = strtoull(argv[1], NULL, 10) << 20;
	double seconds = strtod(argv[2], NULL);
	double avg_hz = strtod(argv[3], NULL);

	page = sysconf(_SC_PAGESIZE);
	if (total < (100 * page) || seconds <= 0 || avg_hz <= 0) {
		fprintf(stderr, "need total_mb >= 1, seconds > 0, avg_hz > 0\n");
		return 2;
	}
	size_t n = total / page;

	mem = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	/* zones, in pages */
	size_t a0 = 0, a1 = n / 10;
	size_t b0 = a1, b1 = b0 + 3 * n / 10, bw = n / 25;
	size_t c0 = b1, c1 = c0 + n / 5;
	size_t d0 = c1, d1 = d0 + 3 * n / 10;

	gt_init();
	gt_range(mem, total);
	printf("total 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + total));
	printf("zones A %zu-%zu B %zu-%zu C %zu-%zu D %zu-%zu E 0-%zu (pages)\n",
	       a0, a1, b0, b1, c0, c1, d0, d1, n);
	fflush(stdout);

	for (size_t p = 0; p < n; p++)
		touch(p, 1);

	/* touches per second of each pattern (C: while on) */
	double total_rate = avg_hz * n;
	double ra = 0.23 * total_rate, rb = 0.20 * total_rate, rc = 0.20 * total_rate * 4;
	double rd = 0.19 * total_rate, re = 0.18 * total_rate;
	/* Zipf over zone A: inverse of the cumulative weight ln(i + 1) */
	double za = log((double)(a1 - a0) + 1);
	uint64_t da = 0, db = 0, dc = 0, dd = 0, de = 0;
	size_t cb = 0, cc = 0, cd = 0;
	double start = now(), t;

	while ((t = now() - start) < seconds) {
		gt_tick();
		/* A: Zipf */
		for (; da < (uint64_t)(ra * t); da++) {
			double u = (next() >> 11) * 0x1.0p-53;
			size_t i = (size_t)(exp(u * za) - 1);

			touch(a0 + (i < a1 - a0 ? i : a1 - a0 - 1), next() % 10 < 3);
		}
		/* B: sliding window, round robin inside it */
		size_t from = b0 + (size_t)((b1 - b0 - bw) * (t / seconds));
		for (; db < (uint64_t)(rb * t); db++)
			touch(from + cb++ % bw, 1);
		/* C: bursts of 0.5 s every 2 s */
		if (fmod(t, 2.0) < 0.5) {
			for (; dc < (uint64_t)(rc * t); dc++)
				touch(c0 + cc++ % (c1 - c0), 0);
		} else {
			dc = (uint64_t)(rc * t);
		}
		/* D: every 8th page */
		for (; dd < (uint64_t)(rd * t); dd++) {
			touch(d0 + (cd * 8) % (d1 - d0), cd % 2);
			cd++;
		}
		/* E: uniform random */
		for (; de < (uint64_t)(re * t); de++)
			touch(next() % n, next() % 10 < 3);
	}

	gt_finish();
	munmap((void *)mem, total);
	return sum == 0xdeadbeef;
}
