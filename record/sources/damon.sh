# source damon: DAMON pseudo-frequencies of the target's regions, through
# damo (damo record <pid>, then damo report for a normalized access.csv).
#
# damo stamps its snapshots with the trace clock. While it records, the trace
# clock is set to mono (with boot, damo's trace-cmd records nothing) and put
# back afterwards. mono differs from the record's boot clock (lib.sh: now_ms)
# by the time the system slept, measured once at the start: no sleep during
# the record, no drift.

# damo command; tests point it elsewhere
read -ra DAMO <<< "${QMEM_DAMO:-damo}"

DAMON_PID=
DAMON_RC=0
DAMON_OLD_CLOCK=
DAMON_BOOT_MINUS_MONO_NS=0

src_damon_check() {
	command -v "${DAMO[0]}" > /dev/null 2>&1 || {
		SRC_REASON="damo not found (${DAMO[0]})"
		return 1
	}
	[ -w "$TRACEFS/trace_clock" ] || {
		SRC_REASON="cannot set the trace clock ($TRACEFS/trace_clock)"
		return 1
	}
}

src_damon_start() {
	local clocks

	read -r clocks < "$TRACEFS/trace_clock" || return 1
	DAMON_OLD_CLOCK=${clocks#*[}
	DAMON_OLD_CLOCK=${DAMON_OLD_CLOCK%%]*}
	echo mono > "$TRACEFS/trace_clock" || return 1
	# python3 is there wherever damo (a python program) is
	DAMON_BOOT_MINUS_MONO_NS=$(python3 -c 'import time
print(time.clock_gettime_ns(time.CLOCK_BOOTTIME) - time.clock_gettime_ns(time.CLOCK_MONOTONIC))') ||
		return 1
	mkdir -p "$OUT/damon"
	"${DAMO[@]}" record --do_record access -o "$OUT/damon/damon.data" "$1" &
	DAMON_PID=$!
}

# Put the trace clock back; also called on any exit of the driver.
src_damon_restore_clock() {
	[ -n "$DAMON_OLD_CLOCK" ] || return 0
	echo "$DAMON_OLD_CLOCK" > "$TRACEFS/trace_clock" 2>/dev/null || true
	DAMON_OLD_CLOCK=
}

src_damon_tick() { :; }

# Stop damo (when recording is stopped rather than the target exiting) and
# wait for it.
src_damon_stop() {
	[ -n "$DAMON_PID" ] || return 0
	if $STOPPED; then
		kill -INT "$DAMON_PID" 2>/dev/null || true
	fi
	# wait at least once: if damo is already gone, kill -0 fails but wait
	# still returns its status; a trapped signal interrupts wait while damo
	# is still running
	while :; do
		DAMON_RC=0
		wait "$DAMON_PID" || DAMON_RC=$?
		kill -0 "$DAMON_PID" 2>/dev/null || break
	done
	src_damon_restore_clock
	if $STOPPED; then
		# damo exits with the signal number after saving the record
		[ -f "$OUT/damon/damon.data" ] || die "damo did not write $OUT/damon/damon.data"
		DAMON_RC=0
	fi
	if [ "$DAMON_RC" -ne 0 ] && [ ! -f "$OUT/damon/damon.data" ] && [ "$STOP_REASON" = exited ]; then
		# damo crashes this way when the target is gone before the first snapshot
		die "the application exited before damo saved a record; it ran too briefly to record"
	fi
	[ "$DAMON_RC" -eq 0 ] || die "damo record failed (exit $DAMON_RC)"
}

src_damon_finish() {
	src_damon_normalize "$OUT"
}

# <dir>/damon/damon.data -> <dir>/damon/access.csv via `damo report`, so
# nothing else depends on damo's record file format. Snapshot times become
# ns since the record's start: damo's abs start time (mono) + the boot - mono
# offset - the record's start (boot).
src_damon_normalize() {
	local dir=$1

	[ -f "$dir/damon/damon.data" ] || die "no $dir/damon/damon.data to normalize"
	{
		echo "snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age"
		"${DAMO[@]}" report access -i "$dir/damon/damon.data" --raw_number \
			--format set record_head "record <abs start time>" \
			--format set snapshot_head "snapshot <start time> <end time>" \
			--format set region "<start address> <end address> <access rate> <access hz> <age>" \
			--format set snapshot_tail "" \
			--format set record_tail "" |
		awk -v start_ms="$START_MS" -v offset="$DAMON_BOOT_MINUS_MONO_NS" '
			$1 == "record" { base = $2 + offset - start_ms * 1000000; next }
			$1 == "snapshot" { start = base + $2; end = base + $3; next }
			NF == 5 { printf "%.0f,%.0f,%s,%s,%s,%s,%s\n", start, end, $1, $2, $3, $4, $5 }'
	} > "$dir/damon/access.csv.tmp" || die "damo report failed"
	mv "$dir/damon/access.csv.tmp" "$dir/damon/access.csv"
}

src_damon_manifest() {
	printf '"version": 1, "files": ["damon/access.csv", "damon/damon.data"], '
	printf '"damo_version": %s' "$(json_str "$("${DAMO[@]}" version 2>/dev/null)")"
}
