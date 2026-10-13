CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra
CFLAGS += -Icommon

WORKLOADS := workloads/hot_region workloads/rw_split workloads/fixed_work workloads/random_access workloads/moving_window workloads/mixed workloads/randmix
TOOLS := $(if $(wildcard record/softdirty.c),record/qmem-softdirty) \
	 $(if $(wildcard replay/replay.c),replay/qmem-replay)

all: $(WORKLOADS) $(TOOLS)

workloads/%: workloads/%.c common/qmem_gt.h
	$(CC) $(CFLAGS) -o $@ $< -lm

record/qmem-softdirty: record/softdirty.c
	$(CC) $(CFLAGS) -o $@ $<

REPLAY_SRC := $(wildcard replay/*.c)

replay/qmem-replay: $(REPLAY_SRC) replay/profile.h common/qmem_gt.h
	$(CC) $(CFLAGS) -o $@ $(REPLAY_SRC)

test: all
	python3 -m pytest -v tests --ignore=tests/legacy

test-legacy: all
	python3 -m pytest -v tests/legacy

clean:
	rm -f $(WORKLOADS) $(TOOLS)

.PHONY: all test test-legacy clean
