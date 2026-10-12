/*
 * qmem_gt.h: ground truth of page accesses, for tests.
 *
 * A program that includes this header records exactly which pages it
 * touched (and wrote) in each time window, with no sampling: every touch
 * sets a bit. It is enabled by the environment:
 *
 *   QMEM_GT=<file>            where to write the record (off when unset)
 *   QMEM_GT_WINDOW_MS=<ms>    window length (default 100)
 *
 * File format (little endian):
 *   "QGT2", u32 window_ms, u64 start_boottime_ns, u32 nranges,
 *   u32 chunk_pages,
 *   nranges x { u64 start, u64 npages },
 *   then until EOF, for each window and range with touches:
 *   u32 window, u32 range, u64 touches, u64 writes,
 *   touched bitmap (ceil(npages / 8) bytes), written bitmap (same size),
 *   touch counts per chunk of chunk_pages pages (ceil(npages / chunk_pages)
 *   x u32).
 *
 * Window w covers [w * window_ms, (w + 1) * window_ms) after
 * start_boottime_ns (CLOCK_BOOTTIME).
 */
#ifndef QMEM_GT_H
#define QMEM_GT_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define GT_CHUNK_PAGES	16		/* touch counts per 64 KB */

struct gt_range {
	uint64_t start, npages;
	uint8_t *touched, *written;	/* bitmaps of the current window */
	uint32_t *counts;		/* touches per chunk, current window */
	uint64_t touches, writes;
};

struct gt_saved {			/* one finished window of one range */
	uint32_t window, range;
	uint64_t touches, writes;
	uint8_t *touched, *written;
	uint32_t *counts;
};

static struct {
	int on;
	const char *path;
	uint32_t window_ms, cur;
	uint64_t start_mono_ns, start_boot_ns;
	long page;
	struct gt_range *r;
	uint32_t n;
	struct gt_saved *saved;
	size_t nsaved, cap;
} gt;

static inline uint64_t gt_clock_ns(clockid_t id)
{
	struct timespec ts;

	clock_gettime(id, &ts);
	return ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

static inline void gt_init(void)
{
	const char *w = getenv("QMEM_GT_WINDOW_MS");

	gt.path = getenv("QMEM_GT");
	gt.on = gt.path && *gt.path;
	gt.window_ms = w ? (uint32_t)atoi(w) : 100;
	if (!gt.window_ms)
		gt.window_ms = 100;
	gt.page = sysconf(_SC_PAGESIZE);
	gt.start_mono_ns = gt_clock_ns(CLOCK_MONOTONIC);
	gt.start_boot_ns = gt_clock_ns(CLOCK_BOOTTIME);
}

static inline uint32_t gt_range(const volatile void *start, uint64_t len)
{
	struct gt_range *g;
	size_t bytes;

	if (!gt.on)
		return 0;
	gt.r = realloc(gt.r, (gt.n + 1) * sizeof(*gt.r));
	g = &gt.r[gt.n];
	g->start = (uint64_t)(uintptr_t)start;
	g->npages = (len + gt.page - 1) / gt.page;
	bytes = (g->npages + 7) / 8;
	g->touched = calloc(bytes, 1);
	g->written = calloc(bytes, 1);
	g->counts = calloc((g->npages + GT_CHUNK_PAGES - 1) / GT_CHUNK_PAGES, 4);
	g->touches = g->writes = 0;
	if (!gt.r || !g->touched || !g->written || !g->counts) {
		perror("qmem_gt");
		exit(1);
	}
	return gt.n++;
}

/* Keep the current window's bitmaps and start a new window. */
static inline void gt_flush(void)
{
	for (uint32_t i = 0; i < gt.n; i++) {
		struct gt_range *g = &gt.r[i];
		size_t bytes = (g->npages + 7) / 8;
		size_t cbytes = (g->npages + GT_CHUNK_PAGES - 1) / GT_CHUNK_PAGES * 4;
		struct gt_saved *s;

		if (!g->touches)
			continue;
		if (gt.nsaved == gt.cap) {
			gt.cap = gt.cap ? 2 * gt.cap : 64;
			gt.saved = realloc(gt.saved, gt.cap * sizeof(*gt.saved));
		}
		s = &gt.saved[gt.nsaved++];
		s->window = gt.cur;
		s->range = i;
		s->touches = g->touches;
		s->writes = g->writes;
		s->touched = malloc(bytes);
		s->written = malloc(bytes);
		s->counts = malloc(cbytes);
		if (!gt.saved || !s->touched || !s->written || !s->counts) {
			perror("qmem_gt");
			exit(1);
		}
		memcpy(s->touched, g->touched, bytes);
		memcpy(s->written, g->written, bytes);
		memcpy(s->counts, g->counts, cbytes);
		memset(g->touched, 0, bytes);
		memset(g->written, 0, bytes);
		memset(g->counts, 0, cbytes);
		g->touches = g->writes = 0;
	}
}

/* Move to window w (a window number in this run's own time). */
static inline void gt_window(uint32_t w)
{
	if (gt.on && w != gt.cur) {
		gt_flush();
		gt.cur = w;
	}
}

/* Move to the window of the current time. */
static inline void gt_tick(void)
{
	if (gt.on)
		gt_window((gt_clock_ns(CLOCK_MONOTONIC) - gt.start_mono_ns) /
			  1000000ULL / gt.window_ms);
}

/* Note a touch of page i of range r. */
static inline void gt_mark(uint32_t r, uint64_t i, int write)
{
	struct gt_range *g;

	if (!gt.on)
		return;
	g = &gt.r[r];
	g->touched[i / 8] |= 1 << (i % 8);
	g->counts[i / GT_CHUNK_PAGES]++;
	g->touches++;
	if (write) {
		g->written[i / 8] |= 1 << (i % 8);
		g->writes++;
	}
}

/* Note a touch at an address of a registered range. */
static inline void gt_touch(const volatile void *addr, int write)
{
	uint64_t a = (uint64_t)(uintptr_t)addr;

	if (!gt.on)
		return;
	for (uint32_t r = 0; r < gt.n; r++)
		if (a >= gt.r[r].start && a < gt.r[r].start + gt.r[r].npages * gt.page) {
			gt_mark(r, (a - gt.r[r].start) / gt.page, write);
			return;
		}
}

static inline void gt_finish(void)
{
	FILE *f;
	uint32_t chunk = GT_CHUNK_PAGES;

	if (!gt.on)
		return;
	gt_flush();
	f = fopen(gt.path, "wb");
	if (!f) {
		perror(gt.path);
		return;
	}
	fwrite("QGT2", 4, 1, f);
	fwrite(&gt.window_ms, 4, 1, f);
	fwrite(&gt.start_boot_ns, 8, 1, f);
	fwrite(&gt.n, 4, 1, f);
	fwrite(&chunk, 4, 1, f);
	for (uint32_t i = 0; i < gt.n; i++) {
		fwrite(&gt.r[i].start, 8, 1, f);
		fwrite(&gt.r[i].npages, 8, 1, f);
	}
	for (size_t i = 0; i < gt.nsaved; i++) {
		struct gt_saved *s = &gt.saved[i];
		size_t bytes = (gt.r[s->range].npages + 7) / 8;

		fwrite(&s->window, 4, 1, f);
		fwrite(&s->range, 4, 1, f);
		fwrite(&s->touches, 8, 1, f);
		fwrite(&s->writes, 8, 1, f);
		fwrite(s->touched, bytes, 1, f);
		fwrite(s->written, bytes, 1, f);
		fwrite(s->counts, (gt.r[s->range].npages + GT_CHUNK_PAGES - 1) /
		       GT_CHUNK_PAGES * 4, 1, f);
	}
	fclose(f);
}

#endif
