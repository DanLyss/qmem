#!/usr/bin/env bash
# qmem-record: record an application's memory load (iteration 1).
#
# The DAMON access pattern is recorded by `damo record`; smaps and system
# memory snapshots, allocation tracing (tracefs) and write sampling
# (qmem-softdirty) are done by this script.
# See docs/modules/record.md.
set -euo pipefail

usage() {
	cat >&2 <<-EOF
	usage: qmem-record.sh -o <dir> [options] -- <command> [args...]
	       qmem-record.sh -o <dir> [options] -p <pid>
	       qmem-record.sh --normalize <dir>

	  -o <dir>        output directory (must be empty or absent)
	  -i <ms>         smaps/system.csv snapshot period (default 1000)
	  -w <ms>         sample written pages (soft-dirty) every <ms> (default off)
	  -p <pid>        attach to a running process instead of starting one
	  -d <s>          stop after <s> seconds
	  -D "<options>"  extra options for damo record
	  --no-syscalls   do not trace mmap/munmap/brk/mremap/madvise
	  --normalize     rebuild <dir>/access.csv from <dir>/damon.data
	EOF
	exit 2
}

die() {
	echo "qmem-record: $*" >&2
	exit 1
}

# damo command and tracefs mount; tests point these elsewhere
read -ra DAMO <<< "${QMEM_DAMO:-damo}"
TRACEFS=${QMEM_TRACEFS:-/sys/kernel/tracing}
SOFTDIRTY=${QMEM_SOFTDIRTY:-$(dirname "$0")/qmem-softdirty}
SYSCALLS="mmap munmap brk mremap madvise"

now_ms() { date +%s%3N; }

json_str() {
	local s=${1//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\t'/\\t}
	s=${s//$'\n'/\\n}
	printf '"%s"' "$s"
}

# Write <dir>/access.csv from <dir>/damon.data via `damo report`, so nothing
# else depends on damo's record file format.
normalize() {
	local dir=$1

	[ -f "$dir/damon.data" ] || die "no $dir/damon.data to normalize"
	{
		echo "snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age"
		"${DAMO[@]}" report access -i "$dir/damon.data" --raw_number \
			--format set record_head "" \
			--format set snapshot_head "snapshot <start time> <end time>" \
			--format set region "<start address> <end address> <access rate> <access hz> <age>" \
			--format set snapshot_tail "" \
			--format set record_tail "" |
		awk '
			$1 == "snapshot" { start = $2; end = $3; next }
			NF == 5 { print start "," end "," $1 "," $2 "," $3 "," $4 "," $5 }'
	} > "$dir/access.csv.tmp" || die "damo report failed"
	mv "$dir/access.csv.tmp" "$dir/access.csv"
}

PSI=/proc/pressure/memory
[ -r "$PSI" ] || PSI=/dev/null

# Append one system.csv row: meminfo (kB), memory PSI avg10, vmstat counters.
system_row() {
	awk -v ts="$1" '
		FILENAME == "/proc/meminfo" && $1 == "MemFree:"      { mf = $2 }
		FILENAME == "/proc/meminfo" && $1 == "MemAvailable:" { ma = $2 }
		FILENAME == "/proc/meminfo" && $1 == "Cached:"       { ca = $2 }
		FILENAME == "/proc/meminfo" && $1 == "SwapFree:"     { sf = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pgfault"        { pf = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pgmajfault"     { pm = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pswpin"         { si = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pswpout"        { so = $2 }
		$1 == "some" || $1 == "full" {
			split($2, kv, "=")
			psi[$1] = kv[2]
		}
		END {
			print ts "," mf "," ma "," ca "," sf "," \
				(psi["some"] == "" ? 0 : psi["some"]) "," \
				(psi["full"] == "" ? 0 : psi["full"]) "," \
				pf + 0 "," pm + 0 "," si + 0 "," so + 0
		}' /proc/meminfo "$PSI" /proc/vmstat
}

# Trace the application's allocation syscalls into $out/syscalls.txt through
# a private tracefs instance. Returns non-zero if tracefs cannot be set up.
inst=
reader_pid=
overrun=0
kernel_pid=

# The pid tracefs knows the application by. Inside a pid namespace (WSL,
# containers) it differs from ours: send the application a harmless SIGCONT
# and read the target pid from the signal_generate event.
find_kernel_pid() {
	local ev="$inst/events/signal/signal_generate" comm pid

	kernel_pid=$app_pid
	[ -d "$ev" ] || return 0
	comm=$(cat "/proc/$app_pid/comm" 2>/dev/null) || return 0
	echo 'sig == 18' > "$ev/filter" || return 0
	echo 1 > "$ev/enable" || return 0
	echo 1 > "$inst/tracing_on"
	kill -CONT "$app_pid" 2>/dev/null || true
	pid=$(grep -F "comm=$comm pid=" "$inst/trace" |
		sed -n '1s/.* comm=.* pid=\([0-9]*\) .*/\1/p')
	echo 0 > "$inst/tracing_on"
	echo 0 > "$ev/enable"
	echo 0 > "$ev/filter"
	echo > "$inst/trace"
	[ -z "$pid" ] || kernel_pid=$pid
}

start_tracing() {
	local sc

	mkdir "$TRACEFS/instances/qmem-record-$$" 2>/dev/null || return 1
	inst="$TRACEFS/instances/qmem-record-$$"
	echo boot > "$inst/trace_clock" || return 1
	echo 8192 > "$inst/buffer_size_kb" || return 1
	find_kernel_pid
	for sc in $SYSCALLS; do
		echo 1 > "$inst/events/syscalls/sys_enter_$sc/enable" || return 1
		echo 1 > "$inst/events/syscalls/sys_exit_$sc/enable" || return 1
	done
	# follow threads and children; older kernels lack the option
	echo 1 > "$inst/options/event-fork" 2>/dev/null || true
	echo "$kernel_pid" > "$inst/set_event_pid" || return 1
	cat "$inst/trace_pipe" > "$out/syscalls.txt" &
	reader_pid=$!
	echo 1 > "$inst/tracing_on" || return 1
}

stop_tracing() {
	[ -n "$inst" ] || return 0
	echo 0 > "$inst/tracing_on" 2>/dev/null || true
	overrun=$(cat "$inst"/per_cpu/cpu*/stats 2>/dev/null |
		awk '/^overrun:/ { s += $2 } END { print s + 0 }')
	if [ -n "$reader_pid" ]; then
		sleep 0.2	# let the reader drain the buffer
		kill "$reader_pid" 2>/dev/null || true
		wait "$reader_pid" 2>/dev/null || true
		reader_pid=
	fi
	rmdir "$inst" 2>/dev/null || true
	inst=
}

# True while <pid> exists and is not a zombie (damo reaps its child late).
alive() {
	local stat

	stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
	stat=${stat##*) }
	[ "${stat%% *}" != Z ]
}

# Sample written pages of the application into $out/writes.csv.
sd_pid=
start_softdirty() {
	echo "ts_ms,start,end,present_pages,dirty_pages" > "$out/writes.csv"
	"$SOFTDIRTY" "$app_pid" "$writes_ms" "$start_ms" >> "$out/writes.csv" &
	sd_pid=$!
}

stop_softdirty() {
	[ -n "$sd_pid" ] || return 0
	kill -TERM "$sd_pid" 2>/dev/null || true
	wait "$sd_pid" 2>/dev/null || true
	sd_pid=
}

if [ "${1-}" = --normalize ]; then
	[ $# -eq 2 ] || usage
	normalize "$2"
	exit 0
fi

out=
interval=1000
want_syscalls=true
damo_options=
attach_pid=
duration=
writes_ms=0
while [ $# -gt 0 ]; do
	case $1 in
	-o) [ $# -ge 2 ] || usage; out=$2; shift 2 ;;
	-i) [ $# -ge 2 ] || usage; interval=$2; shift 2 ;;
	-w) [ $# -ge 2 ] || usage; writes_ms=$2; shift 2 ;;
	-p) [ $# -ge 2 ] || usage; attach_pid=$2; shift 2 ;;
	-d) [ $# -ge 2 ] || usage; duration=$2; shift 2 ;;
	-D) [ $# -ge 2 ] || usage; damo_options=$2; shift 2 ;;
	--no-syscalls) want_syscalls=false; shift ;;
	--) shift; break ;;
	*) usage ;;
	esac
done
[ -n "$out" ] || usage
if [ -n "$attach_pid" ]; then
	[ $# -eq 0 ] && [[ $attach_pid =~ ^[1-9][0-9]*$ ]] || usage
	mode=attach
else
	[ $# -gt 0 ] || usage
	mode=launch
fi
[[ $interval =~ ^[1-9][0-9]*$ ]] || usage
[[ $writes_ms =~ ^[0-9]+$ ]] || usage
if [ "$writes_ms" -gt 0 ] && [ ! -x "$SOFTDIRTY" ]; then
	die "$SOFTDIRTY not found (run make)"
fi
deadline_ms=
if [ -n "$duration" ]; then
	[[ $duration =~ ^[0-9]+(\.[0-9]+)?$ ]] || usage
	duration_ms=$(awk -v s="$duration" 'BEGIN { printf "%d", s * 1000 }')
	[ "$duration_ms" -gt 0 ] || usage
fi

if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
	die "$out is not empty"
fi

if [ $mode = attach ]; then
	[ -d "/proc/$attach_pid" ] || die "no such process $attach_pid"
	command=$(tr '\0' ' ' < "/proc/$attach_pid/cmdline")
	command=${command% }
	target=$attach_pid
else
	command=$(printf '%q ' "$@")
	command=${command% }
	target=$command
fi

mkdir -p "$out/smaps"
start_time=$(date +%s.%N)
start_ms=$(now_ms)
start_uptime=$(cut -d' ' -f1 /proc/uptime)
[ -z "$duration" ] || deadline_ms=$(( start_ms + duration_ms ))

read -ra damo_opts <<< "$damo_options"
"${DAMO[@]}" record --do_record access "${damo_opts[@]}" \
	-o "$out/damon.data" "$target" &
damo_pid=$!

# why recording stopped: exited, interrupted or duration
stop_reason=exited
stopped=false
stop_damo() {
	stop_reason=$1
	stopped=true
	kill -INT "$damo_pid" 2>/dev/null || true
}
trap 'stop_damo interrupted' INT TERM
trap 'stop_softdirty; stop_tracing' EXIT

if [ $mode = attach ]; then
	app_pid=$attach_pid
else
	# damo starts the command itself; the application is its first child
	app_pid=
	for _ in $(seq 100); do
		app_pid=$(pgrep -o -P "$damo_pid" || true)
		[ -n "$app_pid" ] && break
		kill -0 "$damo_pid" 2>/dev/null || break
		sleep 0.05
	done
fi

syscalls=false
if $want_syscalls && [ -n "$app_pid" ]; then
	if start_tracing; then
		syscalls=true
	else
		stop_tracing
		rm -f "$out/syscalls.txt"
		echo "qmem-record: syscall tracing unavailable (cannot set up $TRACEFS)," \
			"continuing without it" >&2
	fi
fi

if [ "$writes_ms" -gt 0 ] && [ -n "$app_pid" ]; then
	start_softdirty
fi

echo "ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout" \
	> "$out/system.csv"

# Check often whether the application is still there (so recording ends
# right after it does), take snapshots every $interval ms.
poll_s=$(awk -v ms="$interval" 'BEGIN { print (ms < 100 ? ms : 100) / 1000 }')
next_snap_ms=$start_ms
while [ -n "$app_pid" ] && ! $stopped && alive "$app_pid"; do
	now=$(now_ms)
	if [ -n "$deadline_ms" ] && [ "$now" -ge "$deadline_ms" ]; then
		stop_damo duration
		break
	fi
	if [ "$now" -ge "$next_snap_ms" ]; then
		next_snap_ms=$(( now + interval ))
		ts=$(( now - start_ms ))
		snap="$out/smaps/$ts.txt"
		cat "/proc/$app_pid/smaps" > "$snap" 2>/dev/null || true
		if [ ! -s "$snap" ]; then
			rm -f "$snap"	# the application has just exited
			break
		fi
		system_row "$ts" >> "$out/system.csv"
	fi
	# sleep in the background so a signal is handled right away
	sleep "$poll_s" &
	wait $! || true
done

stop_softdirty

# damo leaves the target alone when it is stopped; an application we started
# is ours to stop, an attached one is not
if $stopped && [ $mode = launch ] && [ -n "$app_pid" ]; then
	kill -TERM "$app_pid" 2>/dev/null || true
fi

damo_rc=0
while kill -0 "$damo_pid" 2>/dev/null; do
	wait "$damo_pid" || damo_rc=$?
done
end_time=$(date +%s.%N)
stop_tracing

if $stopped; then
	# damo exits with the signal number after saving the record
	[ -f "$out/damon.data" ] || die "damo did not write $out/damon.data"
	damo_rc=0
fi
if [ "$damo_rc" -ne 0 ] && [ ! -f "$out/damon.data" ] && [ $stop_reason = exited ]; then
	# damo crashes this way when the target is gone before the first snapshot
	die "the application exited before damo saved a record; it ran too briefly to record"
fi
[ "$damo_rc" -eq 0 ] || die "damo record failed (exit $damo_rc)"

normalize "$out"

interrupted=false
[ $stop_reason != interrupted ] || interrupted=true

cat > "$out/meta.json" <<EOF
{
  "mode": "$mode",
  "command": $(json_str "$command"),
  "pid": ${app_pid:-null},
  "kernel_pid": ${kernel_pid:-null},
  "damo_version": $(json_str "$("${DAMO[@]}" version)"),
  "damo_options": $(json_str "$damo_options"),
  "kernel": $(json_str "$(uname -r)"),
  "start_time": $start_time,
  "start_uptime": $start_uptime,
  "end_time": $end_time,
  "interval_ms": $interval,
  "duration_s": ${duration:-null},
  "softdirty_ms": $writes_ms,
  "stop_reason": "$stop_reason",
  "interrupted": $interrupted,
  "syscalls": $syscalls,
  "syscall_overrun": $overrun
}
EOF
