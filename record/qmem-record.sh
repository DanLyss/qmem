#!/usr/bin/env bash
# qmem-record: record an application's memory load (record v2).
#
# The driver starts or attaches to the target, keeps the clock (ts_ms and
# clock anchors) and calls the sources (record/sources/<name>.sh). Each
# source writes into its own subdirectory and describes itself in
# record.json. See docs/skeleton.md and docs/formats/record.md.
set -euo pipefail

HERE=$(dirname "$0")

usage() {
	cat >&2 <<-EOF
	usage: qmem-record.sh -o <dir> [options] -- <command> [args...]
	       qmem-record.sh -o <dir> [options] -p <pid>
	       qmem-record.sh --normalize <dir>

	  -o <dir>          output directory (must be empty or absent)
	  -i <ms>           snapshot period of smaps, proc, system (default 1000)
	  -w <ms>           sample written pages (soft-dirty) every <ms> (default off)
	  -p <pid>          attach to a running process instead of starting one
	  -d <s>            stop after <s> seconds
	  -D "<options>"    extra options for damo record
	  --no-syscalls     do not trace mmap/munmap/brk/mremap/madvise
	  --sources <list>  comma-separated sources (default: all of
	                    $ALL_SOURCES)
	  --normalize       rebuild <dir>/damon/access.csv from <dir>/damon/damon.data
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

# commands and paths; tests point these elsewhere
read -ra DAMO <<< "${QMEM_DAMO:-damo}"
TRACEFS=${QMEM_TRACEFS:-/sys/kernel/tracing}
SOFTDIRTY=${QMEM_SOFTDIRTY:-$HERE/qmem-softdirty}

if [ "${1-}" = --normalize ]; then
	[ $# -eq 2 ] || usage
	src_damon_normalize "$2"
	exit 0
fi

OUT=
INTERVAL=1000
WRITES_MS=0
DAMO_OPTIONS=
attach_pid=
duration=
want_syscalls=true
sources=${ALL_SOURCES// /,}
while [ $# -gt 0 ]; do
	case $1 in
	-o) [ $# -ge 2 ] || usage; OUT=$2; shift 2 ;;
	-i) [ $# -ge 2 ] || usage; INTERVAL=$2; shift 2 ;;
	-w) [ $# -ge 2 ] || usage; WRITES_MS=$2; shift 2 ;;
	-p) [ $# -ge 2 ] || usage; attach_pid=$2; shift 2 ;;
	-d) [ $# -ge 2 ] || usage; duration=$2; shift 2 ;;
	-D) [ $# -ge 2 ] || usage; DAMO_OPTIONS=$2; shift 2 ;;
	--no-syscalls) want_syscalls=false; shift ;;
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
[[ $WRITES_MS =~ ^[0-9]+$ ]] || usage
duration_ms=
if [ -n "$duration" ]; then
	[[ $duration =~ ^[0-9]+(\.[0-9]+)?$ ]] || usage
	duration_ms=$(awk -v s="$duration" 'BEGIN { printf "%d", s * 1000 }')
	[ "$duration_ms" -gt 0 ] || usage
fi

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
$want_syscalls || STATUS[syscalls]=off
[ "$WRITES_MS" -gt 0 ] || STATUS[softdirty]=off
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
		[ "$src" != softdirty ] || die "$SRC_REASON"	# asked for with -w
		STATUS[$src]=unavailable
		REASON[$src]=$SRC_REASON
		warn "$SRC_REASON, continuing without it"
		;;
	esac
done

mkdir -p "$OUT"
start_time=$(date +%s.%N)
START_MS=$(now_ms)
start_uptime=$(cut -d' ' -f1 /proc/uptime)
deadline_ms=
[ -z "$duration_ms" ] || deadline_ms=$(( START_MS + duration_ms ))
start_clock
anchor

# why recording stopped: exited, interrupted or duration
STOP_REASON=exited
STOPPED=false
stop() {
	STOP_REASON=$1
	STOPPED=true
}
trap 'stop interrupted' INT TERM
cleanup() {
	for src in syscalls softdirty; do
		on "$src" && "src_${src}_stop" || true
	done
	stop_clock
}
trap cleanup EXIT

# the target
APP_PID=
KERNEL_PID=
launcher=
if [ $MODE = attach ]; then
	APP_PID=$attach_pid
elif on damon; then
	# damo starts the command itself (through bash, which execs it). It may
	# also start short-lived helpers, so the application is the child whose
	# argv[0] is our command's.
	src_damon_launch "$command"
	for _ in $(seq 100); do
		for child in $(pgrep -P "$DAMON_PID" || true); do
			# the child may be gone already
			argv0=$( (tr '\0' '\n' < "/proc/$child/cmdline") 2>/dev/null |
				head -n1) || true
			if [ "$argv0" = "$1" ]; then
				APP_PID=$child
				break
			fi
		done
		[ -n "$APP_PID" ] && break
		kill -0 "$DAMON_PID" 2>/dev/null || break
		sleep 0.05
	done
else
	"$@" &
	APP_PID=$!
	launcher=$APP_PID
fi

# start the sources
for src in $ALL_SOURCES; do
	[ "${STATUS[$src]}" = ok ] || continue
	[ -n "$APP_PID" ] || [ "$src" = damon ] || continue
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
while [ -n "$APP_PID" ] && ! $STOPPED && alive "$APP_PID"; do
	now=$(now_ms)
	if [ -n "$deadline_ms" ] && [ "$now" -ge "$deadline_ms" ]; then
		stop duration
		break
	fi
	if [ "$now" -ge "$next_ms" ]; then
		next_ms=$(( now + INTERVAL ))
		ts=$(( now - START_MS ))
		gone=false
		for src in $TICKING; do
			[ "${STATUS[$src]}" = ok ] || continue
			"src_${src}_tick" "$ts" || { gone=true; break; }	# just exited
		done
		$gone && break
		anchor
	fi
	# sleep in the background so a signal is handled right away
	sleep "$poll_s" &
	wait $! || true
done

# a target we started is ours to stop (damo leaves it alone when it is
# stopped), an attached one is not
if $STOPPED && [ $MODE = launch ] && [ -n "$APP_PID" ]; then
	kill -TERM "$APP_PID" 2>/dev/null || true
fi
if [ -n "$launcher" ]; then
	wait "$launcher" 2>/dev/null || true
fi

# stop the sources in reverse order; damon waits for damo to save its record
for src in $(echo $ALL_SOURCES | tr ' ' '\n' | tac); do
	[ "${STATUS[$src]}" = ok ] || continue
	"src_${src}_stop"
done
end_time=$(date +%s.%N)
anchor
stop_clock

for src in $ALL_SOURCES; do
	[ "${STATUS[$src]}" = ok ] || continue
	"src_${src}_finish"
done

interrupted=false
[ $STOP_REASON != interrupted ] || interrupted=true
paused_ms=0
[ ! -f "$OUT/pauses.csv" ] ||
	paused_ms=$(awk -F, 'NR > 1 { s += $2 } END { print s + 0 }' "$OUT/pauses.csv")

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
	    "pid": ${APP_PID:-null},
	    "kernel_pid": ${KERNEL_PID:-null}
	  },
	  "time": {
	    "start_time": $start_time,
	    "end_time": $end_time,
	    "start_uptime": $start_uptime,
	    "paused_ms": $paused_ms
	  },
	  "stop": {"reason": "$STOP_REASON", "interrupted": $interrupted},
	  "kernel": $(json_str "$(uname -r)"),
	  "interval_ms": $INTERVAL,
	  "duration_s": ${duration:-null},
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
