/*
 * randmix: a random multi-pattern workload, different for every seed.
 *
 * Maps <total_mb> of anonymous memory, writes it once, and splits it into
 * 4-8 zones of random sizes. Each zone gets a random pattern, a random
 * intensity per page (log-uniform, 1 Hz to 20 kHz) and a random share of
 * writes:
 *
 *   uniform   random pages of the zone
 *   zipf      page i of the zone with weight 1 / (i + 1)
 *   window    a window of 5-30 % of the zone slides across it
 *   bursts    the whole zone, on for 20-60 % of a 0.5-3 s period
 *   stride    every k-th page (k = 2..16)
 *   hotset    1-5 % of the zone's pages, scattered, very hot
 *
 * The total rate is capped (by scaling every zone down) so that one core
 * keeps up. Prints the zones. Logs ground truth with QMEM_GT.
 *
 * usage: randmix <total_mb> <seconds> <seed>
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

#include "qmem_gt.h"

#define MAX_RATE 30e6		/* touches per second one core keeps up with */

enum { UNIFORM, ZIPF, WINDOW, BURSTS, STRIDE, HOTSET, NPATTERNS };
static const char *names[] = { "uniform", "zipf", "window", "bursts", "stride", "hotset" };

struct zone {
	size_t p0, n;		/* first page, pages */
	int pattern;
	double hz;		/* touches per page per second (of the touched pages) */
	double write;		/* share of writes */
	double rate;		/* touches per second of the zone */
	size_t win, stride, nhot;
	size_t *hot;
	double period, duty;
	uint64_t done;
	size_t rr;
};

static uint64_t rng;

static uint64_t next(void)
{
	rng ^= rng << 13;
	rng ^= rng >> 7;
	rng ^= rng << 17;
	return rng;
}

static double uni(void)
{
	return (next() >> 11) * 0x1.0p-53;
}

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

static volatile uint8_t *mem;
static size_t page;
static unsigned long sum;

static void touch(size_t p, double write)
{
	volatile uint8_t *a = mem + p * page;
	int w = uni() < write;

	if (w)
		*a += 1;
	else
		sum += *a;
	gt_touch(a, w);
}

int main(int argc, char **argv)
{
	if (argc != 4) {
		fprintf(stderr, "usage: %s <total_mb> <seconds> <seed>\n", argv[0]);
		return 2;
	}
	size_t total = strtoull(argv[1], NULL, 10) << 20;
	double seconds = strtod(argv[2], NULL);
	uint64_t seed = strtoull(argv[3], NULL, 10);

	page = sysconf(_SC_PAGESIZE);
	if (total < (16ULL << 20) || seconds <= 0) {
		fprintf(stderr, "need total_mb >= 16, seconds > 0\n");
		return 2;
	}
	size_t n = total / page;
	rng = seed * 2654435761ULL + 88172645463325252ULL;
	for (int i = 0; i < 10; i++)
		next();

	mem = mmap(NULL, total, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	gt_init();
	gt_range(mem, total);

	/* zones: random cut points, at least 256 pages (1 MB) each */
	int k = 4 + next() % 5;
	struct zone z[8] = { 0 };
	size_t cuts[9];
	cuts[0] = 0;
	cuts[k] = n;
	for (int i = 1; i < k; i++)
		cuts[i] = 256 + next() % (n - 512);
	for (int i = 1; i < k; i++)	/* sort */
		for (int j = i; j > 1 && cuts[j - 1] > cuts[j]; j--) {
			size_t t = cuts[j]; cuts[j] = cuts[j - 1]; cuts[j - 1] = t;
		}
	double total_rate = 0;
	for (int i = 0; i < k; i++) {
		struct zone *q = &z[i];
		static const double writes[] = { 0, 0.3, 1 };

		q->p0 = cuts[i];
		q->n = cuts[i + 1] - cuts[i];
		if (q->n < 16) {		/* a cut collision: merge into a neighbour */
			q->n = 0;
			continue;
		}
		q->pattern = next() % NPATTERNS;
		q->hz = exp(log(1) + uni() * (log(20000) - log(1)));
		q->write = writes[next() % 3];
		switch (q->pattern) {
		case WINDOW:
			q->win = q->n * (0.05 + 0.25 * uni()) + 1;
			q->rate = q->hz * q->win;
			break;
		case BURSTS:
			q->period = 0.5 + 2.5 * uni();
			q->duty = 0.2 + 0.4 * uni();
			q->rate = q->hz * q->n;		/* average; on: rate / duty */
			break;
		case STRIDE:
			q->stride = 2 + next() % 15;
			q->rate = q->hz * (q->n / q->stride);
			break;
		case HOTSET:
			q->nhot = q->n * (0.01 + 0.04 * uni()) + 1;
			q->hot = malloc(q->nhot * sizeof(*q->hot));
			for (size_t j = 0; j < q->nhot; j++)
				q->hot[j] = q->p0 + next() % q->n;
			q->rate = q->hz * q->nhot;
			break;
		default:
			q->rate = q->hz * q->n;
		}
		total_rate += q->rate;
	}
	double scale = total_rate > MAX_RATE ? MAX_RATE / total_rate : 1;

	printf("total 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + total));
	printf("seed %llu zones %d scale %.3f\n", (unsigned long long)seed, k, scale);
	for (int i = 0; i < k; i++) {
		if (!z[i].n)
			continue;
		z[i].rate *= scale;
		z[i].hz *= scale;
		printf("zone %d pages %zu-%zu %s %.1f Hz/page writes %.0f%%", i, z[i].p0,
		       z[i].p0 + z[i].n, names[z[i].pattern], z[i].hz, 100 * z[i].write);
		if (z[i].pattern == WINDOW)
			printf(" window %zu pages", z[i].win);
		if (z[i].pattern == BURSTS)
			printf(" period %.2f s duty %.0f%%", z[i].period, 100 * z[i].duty);
		if (z[i].pattern == STRIDE)
			printf(" every %zu", z[i].stride);
		if (z[i].pattern == HOTSET)
			printf(" %zu hot pages", z[i].nhot);
		printf("\n");
	}
	fflush(stdout);

	for (size_t p = 0; p < n; p++)
		touch(p, 1);

	double start = now(), t;

	while ((t = now() - start) < seconds) {
		gt_tick();
		for (int i = 0; i < k; i++) {
			struct zone *q = &z[i];
			uint64_t due;

			if (!q->n)
				continue;
			if (q->pattern == BURSTS) {
				if (fmod(t, q->period) >= q->duty * q->period) {
					q->done = q->rate / q->duty * t;	/* off: no backlog */
					continue;
				}
				due = q->rate / q->duty * t;
			} else {
				due = q->rate * t;
			}
			for (; q->done < due; q->done++) {
				size_t p;

				switch (q->pattern) {
				case UNIFORM:
					p = q->p0 + next() % q->n;
					break;
				case ZIPF: {
					size_t i2 = (size_t)(exp(uni() * log((double)q->n + 1)) - 1);
					p = q->p0 + (i2 < q->n ? i2 : q->n - 1);
					break;
				}
				case WINDOW: {
					size_t from = (size_t)((q->n - q->win) * (t / seconds));
					p = q->p0 + from + q->rr++ % q->win;
					break;
				}
				case BURSTS:
					p = q->p0 + q->rr++ % q->n;
					break;
				case STRIDE:
					p = q->p0 + (q->rr++ * q->stride) % q->n;
					break;
				default:
					p = q->hot[next() % q->nhot];
				}
				touch(p, q->write);
			}
		}
	}

	gt_finish();
	munmap((void *)mem, total);
	return sum == 0xdeadbeef;
}
