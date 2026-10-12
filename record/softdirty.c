/*
 * qmem-softdirty: how many pages of each mapping a process wrote.
 *
 * Every <period_ms> prints, for each mapping of <pid> with present pages,
 *
 *     ts_ms,start,end,present_pages,dirty_pages,rewritten_pages
 *
 * where dirty_pages are present pages written since the previous sample
 * (soft-dirty bit 55 of /proc/<pid>/pagemap), then clears the soft-dirty
 * bits through /proc/<pid>/clear_refs. rewritten_pages are the dirty pages
 * that were already present at the previous clear: each of them took one
 * extra write fault because soft-dirty was re-armed, which is what has to
 * be subtracted from the process's fault count. ts_ms is wall-clock milliseconds
 * minus <start_ms>, so it lines up with the other qmem-record files.
 *
 * Runs until <pid> exits (exit 0) or SIGINT/SIGTERM (exit 0).
 *
 * usage: qmem-softdirty <pid> <period_ms> <start_ms>
 */
#include <errno.h>
#include <fcntl.h>
#include <inttypes.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define PM_PRESENT	(1ULL << 63)
#define PM_SOFT_DIRTY	(1ULL << 55)
#define CHUNK		4096
#define MAX_TRACKED	(16ULL << 30)	/* bigger mappings are not tracked */

/* Present pages of each mapping at one scan. */
struct vma_bits {
	uint64_t start, end;
	uint8_t *bits;		/* NULL: not tracked */
};

struct scan {
	struct vma_bits *v;
	size_t n, cap;
};

static struct scan prev, cur;

static void scan_reset(struct scan *s)
{
	for (size_t i = 0; i < s->n; i++)
		free(s->v[i].bits);
	s->n = 0;
}

static struct vma_bits *scan_add(struct scan *s, uint64_t start, uint64_t end,
				 long page)
{
	struct vma_bits *v;

	if (s->n == s->cap) {
		s->cap = s->cap ? 2 * s->cap : 64;
		s->v = realloc(s->v, s->cap * sizeof(*s->v));
		if (!s->v) {
			perror("realloc");
			exit(1);
		}
	}
	v = &s->v[s->n++];
	v->start = start;
	v->end = end;
	v->bits = end - start <= MAX_TRACKED ?
		  calloc((end - start) / page / 8 + 1, 1) : NULL;
	return v;
}

/* Was the page at addr present at the previous scan? */
static int was_present(uint64_t addr, long page)
{
	size_t lo = 0, hi = prev.n;

	while (lo < hi) {
		size_t mid = (lo + hi) / 2;

		if (prev.v[mid].end <= addr)
			lo = mid + 1;
		else
			hi = mid;
	}
	if (lo == prev.n || addr < prev.v[lo].start || !prev.v[lo].bits)
		return 0;
	uint64_t i = (addr - prev.v[lo].start) / page;
	return prev.v[lo].bits[i / 8] >> (i % 8) & 1;
}

static volatile sig_atomic_t stop;

static void on_signal(int sig)
{
	(void)sig;
	stop = 1;
}

static int parse_u64(const char *s, uint64_t *v)
{
	char *end;

	if (!*s || *s == '-')
		return -1;
	errno = 0;
	*v = strtoull(s, &end, 10);
	return errno || *end ? -1 : 0;
}

static uint64_t wall_ms(void)
{
	struct timespec ts;

	clock_gettime(CLOCK_REALTIME, &ts);
	return ts.tv_sec * 1000ULL + ts.tv_nsec / 1000000;
}

static int clear_soft_dirty(const char *path)
{
	int fd = open(path, O_WRONLY);
	int ok;

	if (fd < 0)
		return -1;
	ok = write(fd, "4", 1) == 1;
	close(fd);
	return ok ? 0 : -1;
}

/* Count present, soft-dirty and rewritten pages of [start, end) and note
 * the present ones in v. */
static void count_range(int pagemap, struct vma_bits *v, long page,
			uint64_t *present, uint64_t *dirty, uint64_t *rewritten)
{
	static uint64_t buf[CHUNK];
	uint64_t first = v->start / page, last = v->end / page;

	*present = *dirty = *rewritten = 0;
	for (uint64_t pfn = first; pfn < last; pfn += CHUNK) {
		uint64_t n = last - pfn < CHUNK ? last - pfn : CHUNK;
		ssize_t got = pread(pagemap, buf, n * 8, pfn * 8);

		if (got <= 0)
			return;
		for (ssize_t i = 0; i < got / 8; i++) {
			uint64_t p = pfn + i - first;

			if (!(buf[i] & PM_PRESENT))
				continue;
			(*present)++;
			if (v->bits)
				v->bits[p / 8] |= 1 << (p % 8);
			if (buf[i] & PM_SOFT_DIRTY) {
				(*dirty)++;
				if (was_present((pfn + i) * page, page))
					(*rewritten)++;
			}
		}
	}
}

/* Scan the mappings and print one sample (unless quiet); returns -1 if the
 * process is gone. The scan becomes the reference for the next one. */
static int sample(const char *maps_path, int pagemap, uint64_t start_ms,
		  long page, int quiet)
{
	FILE *maps = fopen(maps_path, "r");
	char line[4096];
	uint64_t ts = wall_ms() - start_ms;
	int any = 0;

	if (!maps)
		return -1;
	scan_reset(&cur);
	while (fgets(line, sizeof(line), maps)) {
		uint64_t start, end, present, dirty, rewritten;

		if (sscanf(line, "%" SCNx64 "-%" SCNx64, &start, &end) != 2)
			continue;
		any = 1;
		count_range(pagemap, scan_add(&cur, start, end, page), page,
			    &present, &dirty, &rewritten);
		if (present && !quiet)
			printf("%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64 ",%" PRIu64
			       ",%" PRIu64 "\n", ts, start, end, present, dirty, rewritten);
	}
	fclose(maps);
	fflush(stdout);
	/* this scan is what the next one compares with */
	{
		struct scan t = prev;

		prev = cur;
		cur = t;
	}
	return any ? 0 : -1;	/* a zombie has an empty maps file */
}

int main(int argc, char **argv)
{
	uint64_t pid, period_ms, start_ms;
	char maps_path[64], pagemap_path[64], clear_path[64];
	long page = sysconf(_SC_PAGESIZE);
	struct sigaction sa = { .sa_handler = on_signal };
	struct timespec next;
	int pagemap;

	if (argc != 4 || parse_u64(argv[1], &pid) || pid == 0 ||
	    parse_u64(argv[2], &period_ms) || period_ms == 0 ||
	    parse_u64(argv[3], &start_ms)) {
		fprintf(stderr, "usage: %s <pid> <period_ms> <start_ms>\n", argv[0]);
		return 2;
	}

	snprintf(maps_path, sizeof(maps_path), "/proc/%" PRIu64 "/maps", pid);
	snprintf(pagemap_path, sizeof(pagemap_path), "/proc/%" PRIu64 "/pagemap", pid);
	snprintf(clear_path, sizeof(clear_path), "/proc/%" PRIu64 "/clear_refs", pid);

	pagemap = open(pagemap_path, O_RDONLY);
	if (pagemap < 0) {
		fprintf(stderr, "qmem-softdirty: %s: %s\n", pagemap_path, strerror(errno));
		return 1;
	}
	/* note what is present at the first clear */
	sample(maps_path, pagemap, start_ms, page, 1);
	if (clear_soft_dirty(clear_path)) {
		fprintf(stderr, "qmem-softdirty: %s: %s\n", clear_path, strerror(errno));
		return 1;
	}

	/* no SA_RESTART: a signal cuts the sleep short */
	sigaction(SIGINT, &sa, NULL);
	sigaction(SIGTERM, &sa, NULL);

	clock_gettime(CLOCK_MONOTONIC, &next);
	while (!stop) {
		next.tv_sec += period_ms / 1000;
		next.tv_nsec += (period_ms % 1000) * 1000000;
		if (next.tv_nsec >= 1000000000) {
			next.tv_sec++;
			next.tv_nsec -= 1000000000;
		}
		while (!stop && clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME,
						&next, NULL) == EINTR)
			;
		if (stop)
			break;
		if (sample(maps_path, pagemap, start_ms, page, 0) ||
		    clear_soft_dirty(clear_path))
			break;	/* the process exited */
	}
	close(pagemap);
	return 0;
}
