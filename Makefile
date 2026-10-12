CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra

WORKLOADS := workloads/hot_region workloads/rw_split workloads/fixed_work workloads/random_access
TOOLS := $(if $(wildcard record/softdirty.c),record/qmem-softdirty) \
	 $(if $(wildcard replay/replay.c),replay/qmem-replay)

all: $(WORKLOADS) $(TOOLS)

workloads/%: workloads/%.c
	$(CC) $(CFLAGS) -o $@ $<

record/qmem-softdirty: record/softdirty.c
	$(CC) $(CFLAGS) -o $@ $<

replay/qmem-replay: replay/replay.c
	$(CC) $(CFLAGS) -o $@ $<

test: all
	python3 -m pytest -v tests

clean:
	rm -f $(WORKLOADS) $(TOOLS)

.PHONY: all test clean
