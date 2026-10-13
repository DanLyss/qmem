# source syscalls: mmap/munmap/brk/mremap/madvise of the target (and its
# threads and children) through a private tracefs instance, boot clock.

SYSCALLS_LIST="mmap munmap brk mremap madvise"
SC_INST=
SC_READER=
SC_OVERRUN=0

src_syscalls_check() {
	[ -d "$TRACEFS/instances" ] && [ -w "$TRACEFS/instances" ] || {
		SRC_REASON="syscall tracing unavailable (cannot set up $TRACEFS)"
		return 1
	}
}

# The pid tracefs knows the target by. Inside a pid namespace (WSL,
# containers) it differs from ours: send the target a harmless SIGCONT and
# read the target pid from the signal_generate event.
_syscalls_kernel_pid() {
	local ev="$SC_INST/events/signal/signal_generate" comm pid

	KERNEL_PID=$APP_PID
	[ -d "$ev" ] || return 0
	comm=$(cat "/proc/$APP_PID/comm" 2>/dev/null) || return 0
	echo 'sig == 18' > "$ev/filter" || return 0
	echo 1 > "$ev/enable" || return 0
	echo 1 > "$SC_INST/tracing_on"
	kill -CONT "$APP_PID" 2>/dev/null || true
	pid=$(grep -F "comm=$comm pid=" "$SC_INST/trace" |
		sed -n '1s/.* comm=.* pid=\([0-9]*\) .*/\1/p')
	echo 0 > "$SC_INST/tracing_on"
	echo 0 > "$ev/enable"
	echo 0 > "$ev/filter"
	echo > "$SC_INST/trace"
	[ -z "$pid" ] || KERNEL_PID=$pid
}

src_syscalls_start() {
	local sc

	SRC_REASON="syscall tracing unavailable (cannot set up $TRACEFS)"
	mkdir "$TRACEFS/instances/qmem-record-$$" 2>/dev/null || return 1
	SC_INST="$TRACEFS/instances/qmem-record-$$"
	echo boot > "$SC_INST/trace_clock" || return 1
	echo 8192 > "$SC_INST/buffer_size_kb" || return 1
	_syscalls_kernel_pid
	for sc in $SYSCALLS_LIST; do
		echo 1 > "$SC_INST/events/syscalls/sys_enter_$sc/enable" || return 1
		echo 1 > "$SC_INST/events/syscalls/sys_exit_$sc/enable" || return 1
	done
	# follow threads and children; older kernels lack the option
	echo 1 > "$SC_INST/options/event-fork" 2>/dev/null || true
	echo "$KERNEL_PID" > "$SC_INST/set_event_pid" || return 1
	mkdir -p "$OUT/syscalls"
	cat "$SC_INST/trace_pipe" > "$OUT/syscalls/syscalls.txt" &
	SC_READER=$!
	echo 1 > "$SC_INST/tracing_on" || return 1
}

src_syscalls_tick() { :; }

src_syscalls_stop() {
	[ -n "$SC_INST" ] || return 0
	echo 0 > "$SC_INST/tracing_on" 2>/dev/null || true
	SC_OVERRUN=$(cat "$SC_INST"/per_cpu/cpu*/stats 2>/dev/null |
		awk '/^overrun:/ { s += $2 } END { print s + 0 }')
	if [ -n "$SC_READER" ]; then
		sleep 0.2	# let the reader drain the buffer
		kill "$SC_READER" 2>/dev/null || true
		wait "$SC_READER" 2>/dev/null || true
		SC_READER=
	fi
	rmdir "$SC_INST" 2>/dev/null || true
	SC_INST=
}

src_syscalls_finish() { :; }

src_syscalls_manifest() {
	printf '"version": 1, "files": ["syscalls/syscalls.txt"], "overrun": %s' "$SC_OVERRUN"
}
