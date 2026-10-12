/*
 * random_access: test workload with non-sequential access.
 *
 * Maps <total_mb> of anonymous memory and writes it once. Then, for
 * <seconds>, keeps writing a dense hot core of <core_mb> in the middle and
 * touches random pages of the whole region at <random_per_s> touches per
 * second (70 % reads, 30 % writes). Prints the region and the core.
 *
 * usage: random_access <total_mb> <core_mb> <seconds> <random_per_s>
 */
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

int main(int argc, char **argv)
{
	if (argc != 5) {
		fprintf(stderr, "usage: %s <total_mb> <core_mb> <seconds> <random_per_s>\n",
			argv[0]);
		return 2;
	}

	size_t total = strtoull(argv[1], NULL, 10) << 20;
	size_t core = strtoull(argv[2], NULL, 10) << 20;
	double seconds = strtod(argv[3], NULL);
	double per_s = strtod(argv[4], NULL);
	size_t page = sysconf(_SC_PAGESIZE);

	if (total == 0 || core == 0 || core > total || seconds < 0 || per_s < 0) {
		fprintf(stderr, "need 0 < core_mb <= total_mb, seconds >= 0, random_per_s >= 0\n");
		return 2;
	}

	volatile uint8_t *mem = mmap(NULL, total, PROT_READ | PROT_WRITE,
				     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	size_t core_off = (total - core) / 2 / page * page;
	size_t npages = total / page;

	gt_init();
	gt_range(mem, total);
	printf("total 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + total));
	printf("core 0x%lx 0x%lx\n", (unsigned long)(mem + core_off),
	       (unsigned long)(mem + core_off + core));
	fflush(stdout);

	for (size_t off = 0; off < total; off += page) {
		mem[off] = 1;
		gt_touch(mem + off, 1);
	}

	uint64_t rng = 88172645463325252ULL, done = 0;
	unsigned long sum = 0;
	double start = now(), end = start + seconds;
	uint8_t v = 0;

	while (now() < end) {
		gt_tick();
		for (size_t off = core_off; off < core_off + core; off += page) {
			mem[off] = v;
			gt_touch(mem + off, 1);
		}
		v++;
		/* random touches to keep up with the requested rate */
		uint64_t due = (uint64_t)((now() - start) * per_s);
		for (; done < due; done++) {
			rng ^= rng << 13;
			rng ^= rng >> 7;
			rng ^= rng << 17;
			size_t off = (rng % npages) * page;
			if (rng % 10 < 3)
				mem[off] = v;
			else
				sum += mem[off];
			gt_touch(mem + off, rng % 10 < 3);
		}
	}

	gt_finish();

	return sum == 0xdeadbeef;	/* keep the reads */
}
