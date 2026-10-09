/*
 * hot_region: test workload with a known access pattern.
 *
 * Optionally waits <delay> seconds, maps <total_mb> of anonymous memory,
 * writes every page once, then keeps writing only the first <hot_mb> for
 * <seconds>. Prints both ranges so tests can check what DAMON saw.
 *
 * usage: hot_region <total_mb> <hot_mb> <seconds> [<delay>]
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
	if (argc != 4 && argc != 5) {
		fprintf(stderr, "usage: %s <total_mb> <hot_mb> <seconds> [<delay>]\n",
			argv[0]);
		return 2;
	}

	size_t total = strtoull(argv[1], NULL, 10) << 20;
	size_t hot = strtoull(argv[2], NULL, 10) << 20;
	double seconds = strtod(argv[3], NULL);
	double delay = argc == 5 ? strtod(argv[4], NULL) : 0;
	size_t page = sysconf(_SC_PAGESIZE);

	if (total == 0 || hot == 0 || hot > total || seconds < 0 || delay < 0) {
		fprintf(stderr, "need 0 < hot_mb <= total_mb, seconds >= 0, delay >= 0\n");
		return 2;
	}

	/* let non-parent tools (qmem-softdirty) read our pagemap under Yama */
	prctl(PR_SET_PTRACER, PR_SET_PTRACER_ANY, 0, 0, 0);

	if (delay > 0)
		usleep(delay * 1e6);

	volatile uint8_t *mem = mmap(NULL, total, PROT_READ | PROT_WRITE,
				     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	printf("total 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + total));
	printf("hot 0x%lx 0x%lx\n", (unsigned long)mem, (unsigned long)(mem + hot));
	fflush(stdout);

	for (size_t off = 0; off < total; off += page)
		mem[off] = 1;

	double end = now() + seconds;
	uint8_t v = 0;

	while (now() < end) {
		for (size_t off = 0; off < hot; off += page)
			mem[off] = v;
		v++;
	}

	munmap((void *)mem, total);
	return 0;
}
