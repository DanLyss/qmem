CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra

WORKLOADS := workloads/hot_region workloads/rw_split
TOOLS := $(if $(wildcard record/softdirty.c),record/qmem-softdirty)

all: $(WORKLOADS) $(TOOLS)

workloads/%: workloads/%.c
	$(CC) $(CFLAGS) -o $@ $<

record/qmem-softdirty: record/softdirty.c
	$(CC) $(CFLAGS) -o $@ $<

test: all
	python3 -m pytest -v tests

clean:
	rm -f $(WORKLOADS) $(TOOLS)

.PHONY: all test clean
