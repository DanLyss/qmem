/*
 * generators: which page of a range is touched next, chosen by pattern=.
 * A new generator is a new gen_<name>.c and a line in the table below.
 */
#include <string.h>

#include "profile.h"

extern const struct generator_ops gen_roundrobin, gen_random, gen_poisson;

static const struct generator_ops *const table[] = {
	&gen_roundrobin,
	&gen_random,
	&gen_poisson,
};

const struct generator_ops *generator(const char *name)
{
	for (size_t i = 0; i < sizeof(table) / sizeof(table[0]); i++)
		if (!strcmp(table[i]->name, name))
			return table[i];
	return NULL;
}
