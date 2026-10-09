/*
 * rw_split: test workload that writes one region and only reads another.
 *
 * Maps region A (<a_mb>) and region B (<b_mb>) with a PROT_NONE guard page
 * between them, so they stay separate mappings, writes every page of both
 * once, then for <seconds> keeps writing A and only reading B.
 *
 * usage: rw_split <a_mb> <b_mb> <seconds>
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

static double now(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_MONOTONIC, &ts);
	return ts.tv_sec + ts.tv_nsec / 1e9;
}

int main(int argc, char **argv)
{
	if (argc != 4) {
		fprintf(stderr, "usage: %s <a_mb> <b_mb> <seconds>\n", argv[0]);
		return 2;
	}

	size_t a_size = strtoull(argv[1], NULL, 10) << 20;
	size_t b_size = strtoull(argv[2], NULL, 10) << 20;
	double seconds = strtod(argv[3], NULL);
	size_t page = sysconf(_SC_PAGESIZE);

	if (a_size == 0 || b_size == 0 || seconds < 0) {
		fprintf(stderr, "need a_mb > 0, b_mb > 0, seconds >= 0\n");
		return 2;
	}

	/* let non-parent tools (qmem-softdirty) read our pagemap under Yama */
	prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0);

	/* A, one guard page, B: adjacent anonymous mappings would be merged */
	volatile uint8_t *a = mmap(NULL, a_size + page + b_size,
				   PROT_READ | PROT_WRITE,
				   MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (a == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	if (mprotect((void *)(a + a_size), page, PROT_NONE)) {
		perror("mprotect");
		return 1;
	}
	volatile uint8_t *b = a + a_size + page;

	printf("a 0x%lx 0x%lx\n", (unsigned long)a, (unsigned long)(a + a_size));
	printf("b 0x%lx 0x%lx\n", (unsigned long)b, (unsigned long)(b + b_size));
	fflush(stdout);

	for (size_t off = 0; off < a_size; off += page)
		a[off] = 1;
	for (size_t off = 0; off < b_size; off += page)
		b[off] = 1;

	double end = now() + seconds;
	uint8_t v = 0;
	unsigned long sum = 0;

	while (now() < end) {
		for (size_t off = 0; off < a_size; off += page)
			a[off] = v;
		for (size_t off = 0; off < b_size; off += page)
			sum += b[off];
		v++;
	}

	return sum == 0xdeadbeef;	/* keep the reads */
}
