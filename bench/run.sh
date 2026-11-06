#!/bin/sh
# Run every bench profile with qmem-replay and keep what it reports.
#
# usage: sh bench/run.sh <qmem-replay> <out dir> [profiles dir]
#
# Plain POSIX sh, no python: it runs on the phone as well. For each profile
# <name> it writes into <out dir>:
#   <name>.out    the summary line of replay (stderr) and "exit=N"
#   <name>.csv    --stats: t_ms,planned,done,lag_ms per epoch
#   <name>.rss    VmRSS in kB every ~100 ms while replay runs: "<n> <kB>"
# and env.txt: uname, page size, date.

REPLAY=$1
OUT=$2
PROFILES=${3:-$(dirname "$0")/profiles}

if [ -z "$REPLAY" ] || [ -z "$OUT" ] || [ ! -x "$REPLAY" ]; then
	echo "usage: sh $0 <qmem-replay> <out dir> [profiles dir]" >&2
	exit 2
fi
mkdir -p "$OUT" || exit 1
{
	uname -a
	echo "pagesize $(getconf PAGESIZE 2>/dev/null)"
	date
} > "$OUT/env.txt" 2>&1

total=$(ls "$PROFILES"/*.txt | wc -l)
i=0
for p in "$PROFILES"/*.txt; do
	i=$((i + 1))
	name=$(basename "$p" .txt)
	echo "[$i/$total] $name" >&2
	"$REPLAY" "$p" --stats "$OUT/$name.csv" 2> "$OUT/$name.out" &
	pid=$!
	n=0
	: > "$OUT/$name.rss"
	# stop at the zombie too: kill -0 would go on until wait
	while [ -r "/proc/$pid/status" ] && ! grep -q '^State:.*Z' "/proc/$pid/status" 2>/dev/null; do
		kb=$(grep '^VmRSS:' "/proc/$pid/status" 2>/dev/null | sed -n 's/.* \([0-9][0-9]*\) kB/\1/p')
		[ -n "$kb" ] && echo "$n $kb" >> "$OUT/$name.rss"
		n=$((n + 1))
		sleep 0.1
	done
	wait $pid
	echo "exit=$?" >> "$OUT/$name.out"
done
echo "done: $OUT" >&2
