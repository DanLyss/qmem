/*
 * moving_window: test workload whose hot spot moves.
 *
 * Maps <total_mb> of anonymous memory and writes it once. Then, for
 * <seconds>, keeps writing a window of <window_mb> that slides evenly from
 * the start of the region to its end. Prints the region.
 *
 * usage: moving_window <total_mb> <window_mb> <seconds>
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
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
		fprintf(stderr, "usage: %s <total_mb> <window_mb> <seconds>\n", argv[0]);
		return 2;
	}

	size_t total = strtoull(argv[1], NULL, 10) << 20;
	size_t window = strtoull(argv[2], NULL, 10) << 20;
	double seconds = strtod(argv[3], NULL);
	size_t page = sysconf(_SC_PAGESIZE);

	if (total == 0 || window == 0 || window > total || seconds <= 0) {
		fprintf(stderr, "need 0 < window_mb <= total_mb, seconds > 0\n");
		return 2;
	}

	volatile uint8_t *mem = mmap(NULL, total, PROT_READ | PROT_WRITE,
				     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}
	printf("total 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + total));
	fflush(stdout);

	for (size_t off = 0; off < total; off += page)
		mem[off] = 1;

	double start = now(), t;
	uint8_t v = 0;

	while ((t = now() - start) < seconds) {
		size_t from = (size_t)((total - window) * (t / seconds)) / page * page;

		for (size_t off = from; off < from + window; off += page)
			mem[off] = v;
		v++;
	}

	munmap((void *)mem, total);
	return 0;
}
