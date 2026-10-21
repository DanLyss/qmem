# source damon: DAMON pseudo-frequencies of the target's regions, through
# damo (damo record, then damo report for a normalized access.csv).
#
# In launch mode damo starts the command itself (src_damon_launch), so the
# driver finds the application among damo's children.

# damo command; tests point it elsewhere
read -ra DAMO <<< "${QMEM_DAMO:-damo}"

DAMON_PID=
DAMON_RC=0
DAMON_TIME_BASE=damo

src_damon_check() {
	command -v "${DAMO[0]}" > /dev/null 2>&1 || {
		SRC_REASON="damo not found (${DAMO[0]})"
		return 1
	}
}

_damon_run() {
	mkdir -p "$OUT/damon"
	"${DAMO[@]}" record --do_record access -o "$OUT/damon/damon.data" "$1" &
	DAMON_PID=$!
}

# Launch mode: damo runs <command string>.
src_damon_launch() {
	_damon_run "$1"
}

# Attach mode: damo monitors <pid>; in launch mode it already runs.
src_damon_start() {
	[ -n "$DAMON_PID" ] || _damon_run "$1"
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

# After the clock is closed: normalize into access.csv.
src_damon_finish() {
	src_damon_normalize "$OUT"
}

# <dir>/damon/damon.data -> <dir>/damon/access.csv via `damo report`, so
# nothing else depends on damo's record file format.
#
# damo stamps snapshots with trace-cmd's 'local' clock, which starts with damo
# and may run at another rate than real time (~9 % fast under WSL). With clock
# anchors (<dir>/clock.csv: local clock seconds -> ts_ms) the snapshot times
# are mapped onto the record's ts_ms, piecewise linearly; without them they
# stay relative to damo's first snapshot.
src_damon_normalize() {
	local dir=$1 anchors=/dev/null

	[ -f "$dir/damon/damon.data" ] || die "no $dir/damon/damon.data to normalize"
	[ ! -f "$dir/clock.csv" ] || anchors="$dir/clock.csv"
	{
		echo "snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age"
		"${DAMO[@]}" report access -i "$dir/damon/damon.data" --raw_number \
			--format set record_head "record <abs start time>" \
			--format set snapshot_head "snapshot <start time> <end time>" \
			--format set region "<start address> <end address> <access rate> <access hz> <age>" \
			--format set snapshot_tail "" \
			--format set record_tail "" |
		awk -v anchors="$anchors" '
			BEGIN {
				while ((getline line < anchors) > 0) {
					split(line, f, ",")
					if (f[1] ~ /^[0-9.]+$/) {
						n++
						L[n] = f[1]
						T[n] = f[2]
					}
				}
			}
			# local clock seconds -> record ms, extrapolated at the ends
			function to_ms(sec,    i) {
				for (i = 2; i < n && sec > L[i]; i++)
					;
				return T[i - 1] + (sec - L[i - 1]) * (T[i] - T[i - 1]) / (L[i] - L[i - 1])
			}
			function t(rel) {
				if (n < 2)
					return rel
				return sprintf("%.0f", to_ms((base + rel) / 1e9) * 1e6)
			}
			$1 == "record" { base = $2; next }
			$1 == "snapshot" { start = t($2); end = t($3); next }
			NF == 5 { print start "," end "," $1 "," $2 "," $3 "," $4 "," $5 }'
	} > "$dir/damon/access.csv.tmp" || die "damo report failed"
	mv "$dir/damon/access.csv.tmp" "$dir/damon/access.csv"
	if [ "$anchors" != /dev/null ]; then
		find_pauses "$anchors" > "$dir/pauses.csv"
		[ "$(wc -l < "$anchors")" -lt 3 ] || DAMON_TIME_BASE=record
	fi
}

src_damon_manifest() {
	printf '"version": 1, "files": ["damon/access.csv", "damon/damon.data"], '
	printf '"damo_version": %s, "time_base": "%s"' \
		"$(json_str "$("${DAMO[@]}" version 2>/dev/null)")" "$DAMON_TIME_BASE"
}
