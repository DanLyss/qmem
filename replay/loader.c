/*
 * loader: profile v2 (docs/formats/profile.md) into the tables of
 * profile.h. Errors exit with code 2 and "profile:<line>: ...".
 *
 * Rules: lines are "type key=value ..."; an unknown key is skipped, an
 * unknown line type too (one warning per type); a key written "!key" must
 * be understood, so an unknown one is an error.
 *
 * Checks marked "ext" matter only for profiles from outside the pipeline
 * (written by hand or by other tools): qmem-build writes correct ones.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "profile.h"

struct region *regions;
struct event *events;
struct access *accesses;
struct epoch *epochs;
uint32_t n_regions, n_events, n_accesses, n_epochs;
uint64_t epoch_ms = 100, seed = 42;

#define MAX_TOKENS 32

static int line_no, header;
static char *tok[MAX_TOKENS];
static int n_tok;

static void *grow(void *p, uint32_t n, size_t size)
{
	/* double the capacity at powers of two */
	if (n & (n - 1))
		return p;
	p = realloc(p, (n ? 2 * (size_t)n : 1) * size);
	if (!p) {
		perror("realloc");
		exit(1);
	}
	return p;
}

static void bad(const char *fmt, ...)
{
	va_list ap;

	fprintf(stderr, "profile:%d: ", line_no);
	va_start(ap, fmt);
	vfprintf(stderr, fmt, ap);
	va_end(ap);
	fputc('\n', stderr);
	exit(2);
}

static uint64_t num(const char *s)
{
	char *end;
	uint64_t v;

	if (!s || *s == '-')
		bad("expected a number");	/* ext */
	errno = 0;
	v = strtoull(s, &end, 10);
	if (errno || *end || end == s)
		bad("bad number '%s'", s);	/* ext */
	return v;
}

static double real(const char *s)
{
	char *end;
	double v;

	if (!s)
		bad("expected a number");	/* ext */
	errno = 0;
	v = strtod(s, &end);
	if (errno || *end || end == s || v < 0)
		bad("bad number '%s'", s);	/* ext */
	return v;
}

static void check_base(const char *s)
{
	if (s && (strncmp(s, "0x", 2) || !s[2] ||
		  s[2 + strspn(s + 2, "0123456789abcdefABCDEF")]))
		bad("bad region base '%s'", s);	/* ext */
}

static void check_range(uint32_t r, uint64_t off, uint64_t len, int aligned)
{
	if (r >= n_regions)
		bad("unknown region %" PRIu32, r);	/* ext */
	if (len == 0 || off + len < off || off + len > regions[r].size)
		bad("range %" PRIu64 "+%" PRIu64 " outside region %" PRIu32, off, len, r);	/* ext */
	if (aligned && (off % page || len % page))
		bad("range %" PRIu64 "+%" PRIu64 " is not page aligned", off, len);	/* ext */
}

/* ------------------------------------------------------------ v2 keys */

/* The value of key in the current line (written key= or !key=), or NULL. */
static const char *opt(const char *key)
{
	size_t n = strlen(key);

	for (int i = 1; i < n_tok; i++) {
		const char *t = tok[i] + (tok[i][0] == '!');

		if (!strncmp(t, key, n) && t[n] == '=')
			return t + n + 1;
	}
	return NULL;
}

static const char *need(const char *key)
{
	const char *v = opt(key);

	if (!v)
		bad("%s: missing %s=", tok[0], key);	/* ext */
	return v;
}

/* Every token is key=value; a !key must be one of known (space separated). */
static void check_keys(const char *known)
{
	for (int i = 1; i < n_tok; i++) {
		char *eq = strchr(tok[i], '=');
		size_t n;
		const char *k;

		if (!eq || eq == tok[i])
			bad("expected key=value, got '%s'", tok[i]);	/* ext */
		if (tok[i][0] != '!')
			continue;
		n = eq - tok[i] - 1;
		for (k = known; *k; k += strcspn(k, " "), k += *k == ' ')
			if (strcspn(k, " ") == n && !strncmp(k, tok[i] + 1, n))
				break;
		if (!*k)
			bad("unknown mandatory key '%.*s'", (int)n, tok[i] + 1);	/* ext */
	}
}

/* ------------------------------------------------------------ line types */

static uint64_t last_event, last_epoch;

static void add_region(uint64_t id, uint64_t size, const char *base)
{
	if (id != n_regions)
		bad("regions must be numbered 0, 1, ... in order");	/* ext */
	if (size == 0)
		bad("empty region");	/* ext */
	/* the base in the original record, for verify only */
	check_base(base);
	regions = grow(regions, n_regions, sizeof(*regions));
	regions[n_regions++] = (struct region){
		.size = size, .npages = (size + page - 1) / page };
}

static void add_event(int kind, uint64_t t, uint64_t r, uint64_t off, uint64_t len)
{
	struct event e = { .t_ms = t, .kind = kind, .region = r, .off = off, .len = len };

	check_range(e.region, e.off, e.len, 1);
	if (e.t_ms < last_event)
		bad("map/unmap/populate times must not go back");	/* ext */
	last_event = e.t_ms;
	events = grow(events, n_events, sizeof(*events));
	events[n_events++] = e;
}

static void add_epoch(uint64_t t, uint64_t dur)
{
	if (n_epochs && t <= last_epoch)
		bad("epoch times must grow");	/* ext */
	last_epoch = t;
	epochs = grow(epochs, n_epochs, sizeof(*epochs));
	/* dur 0: epoch_ms, known at the end */
	epochs[n_epochs++] = (struct epoch){ .t_ms = t, .dur_ms = dur, .first = n_accesses };
}

static void add_access(struct access a)
{
	if (!n_epochs)
		bad("access before the first epoch");	/* ext */
	check_range(a.region, a.off, a.len, 0);
	if (a.write_ratio > 1)
		bad("write ratio above 1");	/* ext */
	a.pages = (a.off + a.len - 1) / page - a.off / page + 1;
	a.rate *= a.pages;	/* the profile gives touches per page */
	a.prev = -1;
	accesses = grow(accesses, n_accesses, sizeof(*accesses));
	accesses[n_accesses++] = a;
	epochs[n_epochs - 1].n++;
}

static int kind_of(const char *s)
{
	return s[0] == 'm' ? MAP : s[0] == 'u' ? UNMAP : POPULATE;
}

static int is_event(const char *s)
{
	return !strcmp(s, "map") || !strcmp(s, "unmap") || !strcmp(s, "populate");
}

static void parse_line(void)
{
	static char unknown[16][32];
	static int n_unknown;

	if (!strcmp(tok[0], "meta")) {
		const char *v;

		check_keys("epoch_ms seed");
		if ((v = opt("epoch_ms"))) {
			epoch_ms = num(v);
			if (epoch_ms == 0)
				bad("epoch_ms must be positive");	/* ext */
		}
		if ((v = opt("seed")))
			seed = num(v);
	} else if (!strcmp(tok[0], "region")) {
		const char *kind;

		check_keys("id size kind base file");
		kind = opt("kind");
		if (kind && !strcmp(kind, "file"))
			bad("file regions are not supported yet");	/* ext */
		if (kind && strcmp(kind, "anon"))
			bad("unknown region kind '%s'", kind);	/* ext */
		add_region(num(need("id")), num(need("size")), opt("base"));
	} else if (is_event(tok[0])) {
		check_keys("t region off len");
		add_event(kind_of(tok[0]), num(need("t")), num(need("region")),
			  num(need("off")), num(need("len")));
	} else if (!strcmp(tok[0], "epoch")) {
		const char *dur;

		check_keys("t dur");
		dur = opt("dur");
		if (dur && num(dur) == 0)
			bad("dur must be positive");	/* ext */
		add_epoch(num(need("t")), dur ? num(dur) : 0);
	} else if (!strcmp(tok[0], "access")) {
		const char *pattern, *w;
		struct access a;

		check_keys("region off len rate write pattern");
		pattern = opt("pattern");
		w = opt("write");
		a = (struct access){ .region = num(need("region")), .off = num(need("off")),
				     .len = num(need("len")), .rate = real(need("rate")),
				     .write_ratio = w ? real(w) : 0,
				     .gen = generator(pattern ? pattern : "roundrobin") };
		if (!a.gen)
			bad("unknown pattern '%s'", pattern);	/* ext */
		if (a.gen->stub) {
			/* one warning per stub generator */
			static const struct generator_ops *warned[16];
			int i = 0;

			while (i < 16 && warned[i] && warned[i] != a.gen)
				i++;
			if (i < 16 && !warned[i]) {
				warned[i] = a.gen;
				fprintf(stderr, "qmem-replay: %s\n", a.gen->stub);
			}
		}
		add_access(a);
	} else if (!strcmp(tok[0], "counter")) {
		/* for future estimators and modes; not replayed */
		check_keys("t name value");
		need("t");
		need("name");
		need("value");
	} else {
		for (int i = 0; i < n_unknown; i++)
			if (!strncmp(unknown[i], tok[0], sizeof(unknown[i]) - 1))
				return;
		if (n_unknown < 16)
			snprintf(unknown[n_unknown++], sizeof(unknown[0]), "%s", tok[0]);
		fprintf(stderr, "qmem-replay: profile:%d: unknown line type '%s', skipped\n",
			line_no, tok[0]);
	}
}

void load(const char *path)
{
	FILE *f = fopen(path, "r");
	char *line = NULL;
	size_t cap = 0;

	if (!f) {
		perror(path);
		exit(1);
	}
	while (getline(&line, &cap, f) >= 0) {
		char *save, *hash = strchr(line, '#');

		line_no++;
		if (hash)
			*hash = 0;
		n_tok = 0;
		for (char *t = strtok_r(line, " \t\r\n", &save); t;
		     t = strtok_r(NULL, " \t\r\n", &save)) {
			if (n_tok == MAX_TOKENS)
				bad("too many fields");	/* ext */
			tok[n_tok++] = t;
		}
		if (n_tok == 0)
			continue;
		for (int i = n_tok; i < MAX_TOKENS; i++)
			tok[i] = NULL;

		if (!header) {
			if (strcmp(tok[0], "qmem-profile") || !tok[1] || strcmp(tok[1], "2"))
				bad("expected 'qmem-profile 2'");	/* ext */
			header = 1;
		} else {
			parse_line();
		}
	}
	free(line);
	fclose(f);
	if (!header)
		bad("empty profile");	/* ext */
	for (uint32_t i = 0; i < n_epochs; i++)
		if (!epochs[i].dur_ms)
			epochs[i].dur_ms = epoch_ms;
}
