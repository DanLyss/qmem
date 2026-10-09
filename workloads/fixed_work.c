/*
 * fixed_work: test workload with a fixed amount of work, for timing.
 *
 * Maps <mb> of anonymous memory and writes every page <passes> times, so a
 * slowdown shows up as a longer run time.
 *
 * usage: fixed_work <mb> <passes>
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <unistd.h>

int main(int argc, char **argv)
{
	if (argc != 3) {
		fprintf(stderr, "usage: %s <mb> <passes>\n", argv[0]);
		return 2;
	}

	size_t size = strtoull(argv[1], NULL, 10) << 20;
	long passes = strtol(argv[2], NULL, 10);
	size_t page = sysconf(_SC_PAGESIZE);

	if (size == 0 || passes <= 0) {
		fprintf(stderr, "need mb > 0 and passes > 0\n");
		return 2;
	}

	volatile uint8_t *mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
				     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	for (long p = 0; p < passes; p++)
		for (size_t off = 0; off < size; off += page)
			mem[off] = p;

	munmap((void *)mem, size);
	return 0;
}
