#!/usr/bin/env bash
# qmem-record: record an application's memory load (record v2).
#
# The driver starts or attaches to the target, keeps the time (ts_ms: ms
# since the start on the boot clock, lib.sh: now_ms) and calls the sources (record/sources/<name>.sh). Each
# source writes into its own subdirectory and describes itself in
# record.json. See docs/skeleton.md and docs/formats/record.md.
set -euo pipefail

HERE=$(dirname "$0")

usage() {
	cat >&2 <<-EOF
	usage: qmem-record.sh -o <dir> [options] -- <command> [args...]
	       qmem-record.sh -o <dir> [options] -p <pid>

	Records until the application exits or Ctrl+C.

	  -o <dir>          output directory (must be empty or absent)
	  -i <ms>           snapshot period of smaps, proc, system (default 1000)
	  -p <pid>          attach to a running process instead of starting one
	  --sources <list>  comma-separated sources (default: all of
	                    $ALL_SOURCES)
	EOF
	exit 2
}

# every source, in the order they are started; stopped in reverse
ALL_SOURCES="damon smaps proc system softdirty syscalls pmu_count pmu_sample"
# sources that take snapshots on every tick
TICKING="smaps proc system"

. "$HERE/lib.sh"
for src in $ALL_SOURCES; do
	. "$HERE/sources/$src.sh"
done

OUT=
INTERVAL=1000
attach_pid=
sources=${ALL_SOURCES// /,}
while [ $# -gt 0 ]; do
	case $1 in
	-o) [ $# -ge 2 ] || usage; OUT=$2; shift 2 ;;
	-i) [ $# -ge 2 ] || usage; INTERVAL=$2; shift 2 ;;
	-p) [ $# -ge 2 ] || usage; attach_pid=$2; shift 2 ;;
	--sources) [ $# -ge 2 ] || usage; sources=$2; shift 2 ;;
	--) shift; break ;;
	*) usage ;;
	esac
done
[ -n "$OUT" ] || usage
if [ -n "$attach_pid" ]; then
	[ $# -eq 0 ] && [[ $attach_pid =~ ^[1-9][0-9]*$ ]] || usage
	MODE=attach
else
	[ $# -gt 0 ] || usage
	MODE=launch
fi
[[ $INTERVAL =~ ^[1-9][0-9]*$ ]] || usage

# why recording stopped: exited or interrupted (Ctrl+C)
STOP_REASON=exited
STOPPED=false
stop() {
	STOP_REASON=$1
	STOPPED=true
}
trap 'stop interrupted' INT TERM

# status of every source: ok, off, unavailable or stub (+ reason)
declare -A STATUS REASON
for src in $ALL_SOURCES; do
	STATUS[$src]=off
done
for src in ${sources//,/ }; do
	[[ " $ALL_SOURCES " == *" $src "* ]] || {
		echo "qmem-record: unknown source '$src' (known: $ALL_SOURCES)" >&2
		usage
	}
	STATUS[$src]=on
done
on() { [ "${STATUS[$1]}" = on ] || [ "${STATUS[$1]}" = ok ]; }

if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT")" ]; then
	die "$OUT is not empty"
fi

if [ $MODE = attach ]; then
	[ -d "/proc/$attach_pid" ] || die "no such process $attach_pid"
	command=$(tr '\0' ' ' < "/proc/$attach_pid/cmdline")
	command=${command% }
else
	command=$(printf '%q ' "$@")
	command=${command% }
fi

# ask every enabled source whether it can work here
for src in $ALL_SOURCES; do
	on "$src" || continue
	SRC_REASON=
	rc=0
	"src_${src}_check" || rc=$?
	case $rc in
	0) STATUS[$src]=ok ;;
	3) STATUS[$src]=stub; REASON[$src]=$SRC_REASON ;;
	*)
		STATUS[$src]=unavailable
		REASON[$src]=$SRC_REASON
		warn "$SRC_REASON, continuing without it"
		;;
	esac
done

mkdir -p "$OUT"
start_time=$(date +%s.%N)
now_ms
START_MS=$NOW_MS

cleanup() {
	for src in syscalls softdirty; do
		on "$src" && "src_${src}_stop" || true
	done
	src_damon_restore_clock
}
trap cleanup EXIT

# Ctrl+C before the application started: nothing to record
$STOPPED && die "interrupted before the start"

# the target: started here, or attached to with -p; the sources follow its pid
KERNEL_PID=
if [ $MODE = attach ]; then
	APP_PID=$attach_pid
else
	"$@" &
	APP_PID=$!
fi

# start the sources
for src in $ALL_SOURCES; do
	[ "${STATUS[$src]}" = ok ] || continue
	SRC_REASON=
	if ! "src_${src}_start" "$APP_PID"; then
		"src_${src}_stop" || true
		rm -rf "${OUT:?}/$src"
		STATUS[$src]=unavailable
		REASON[$src]=$SRC_REASON
		warn "$SRC_REASON, continuing without it"
	fi
done

# Check often whether the target is still there (so recording ends right
# after it does), tick the snapshot sources every $INTERVAL ms.
poll_s=$(awk -v ms="$INTERVAL" 'BEGIN { print (ms < 100 ? ms : 100) / 1000 }')
next_ms=$START_MS
while ! $STOPPED && alive "$APP_PID"; do
	now_ms
	now=$NOW_MS
	if [ "$now" -ge "$next_ms" ]; then
		next_ms=$(( now + INTERVAL ))
		ts=$(( now - START_MS ))
		for src in $TICKING; do
			[ "${STATUS[$src]}" = ok ] || continue
			"src_${src}_tick" "$ts"
		done
	fi
	# sleep in the background so a signal is handled right away
	sleep "$poll_s" &
	wait $! || true
done

# a target we started is ours to stop and wait for, an attached one is not
if [ $MODE = launch ]; then
	$STOPPED && { kill -TERM "$APP_PID" 2>/dev/null || true; }
	wait "$APP_PID" 2>/dev/null || true
fi

# stop the sources in reverse order; damon waits for damo to save its record
for src in $(echo $ALL_SOURCES | tr ' ' '\n' | tac); do
	[ "${STATUS[$src]}" = ok ] || continue
	"src_${src}_stop"
done
end_time=$(date +%s.%N)

for src in $ALL_SOURCES; do
	[ "${STATUS[$src]}" = ok ] || continue
	"src_${src}_finish"
done

interrupted=false
[ $STOP_REASON != interrupted ] || interrupted=true

source_json() {
	local src=$1

	printf '    "%s": {"status": "%s"' "$src" "${STATUS[$src]}"
	case ${STATUS[$src]} in
	ok) printf ', '; "src_${src}_manifest" ;;
	stub) printf ', '; "src_${src}_manifest"; printf ', "reason": %s' "$(json_str "${REASON[$src]}")" ;;
	unavailable) printf ', "reason": %s' "$(json_str "${REASON[$src]}")" ;;
	esac
	printf '}'
}

{
	cat <<-EOF
	{
	  "format": "qmem-record",
	  "version": 2,
	  "target": {
	    "mode": "$MODE",
	    "command": $(json_str "$command"),
	    "pid": $APP_PID,
	    "kernel_pid": ${KERNEL_PID:-null}
	  },
	  "time": {
	    "clock": "boot",
	    "start_ms": $START_MS,
	    "start_time": $start_time,
	    "end_time": $end_time
	  },
	  "stop": {"reason": "$STOP_REASON", "interrupted": $interrupted},
	  "kernel": $(json_str "$(uname -r)"),
	  "interval_ms": $INTERVAL,
	  "sources": {
	EOF
	sep=
	for src in $ALL_SOURCES; do
		printf '%s' "$sep"
		source_json "$src"
		sep=$',\n'
	done
	printf '\n  }\n}\n'
} > "$OUT/record.json"
