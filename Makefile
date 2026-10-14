CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra

WORKLOADS := workloads/hot_region workloads/rw_split workloads/fixed_work workloads/random_access workloads/moving_window
TOOLS := $(if $(wildcard record/softdirty.c),record/qmem-softdirty) \
	 $(if $(wildcard replay/replay.c),replay/qmem-replay)

all: $(WORKLOADS) $(TOOLS)

workloads/%: workloads/%.c
	$(CC) $(CFLAGS) -o $@ $< -lm

record/qmem-softdirty: record/softdirty.c
	$(CC) $(CFLAGS) -o $@ $<

REPLAY_SRC := $(wildcard replay/*.c)

replay/qmem-replay: $(REPLAY_SRC) replay/profile.h
	$(CC) $(CFLAGS) -o $@ $(REPLAY_SRC)

test: all
	python3 -m pytest -v tests

MMDC ?= mmdc
diagrams: docs/architecture.png docs/architecture_extension.png

# render in /tmp: mmdc writes broken files straight onto a WSL /mnt drive
docs/%.png: docs/%.mmd
	tmp=$$(mktemp --suffix=.png) && $(MMDC) -i $< -o $$tmp -w 4500 -s 2 -b white && cp $$tmp $@; rm -f $$tmp

clean:
	rm -f $(WORKLOADS) $(TOOLS)

.PHONY: all test diagrams clean
