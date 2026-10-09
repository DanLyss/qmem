CC ?= gcc
CFLAGS ?= -O2 -Wall -Wextra

WORKLOADS := workloads/hot_region

all: $(WORKLOADS)

workloads/%: workloads/%.c
	$(CC) $(CFLAGS) -o $@ $<

test: all
	python3 -m pytest -v tests

clean:
	rm -f $(WORKLOADS)

.PHONY: all test clean
