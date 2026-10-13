# qmem-record: helpers shared by the driver and the sources (sourced).

die() {
	echo "qmem-record: $*" >&2
	exit 1
}

warn() {
	echo "qmem-record: $*" >&2
}

now_ms() { date +%s%3N; }

json_str() {
	local s=${1//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\t'/\\t}
	s=${s//$'\n'/\\n}
	printf '"%s"' "$s"
}

# True while <pid> exists and is not a zombie.
alive() {
	local stat

	stat=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
	stat=${stat##*) }
	[ "${stat%% *}" != Z ]
}

# ---------------------------------------------------------------- clock

# Clock anchors: markers in a tracefs instance with the 'local' clock (the
# one damo's timestamps come from), each carrying the record's ts_ms. They
# let readers map other clocks onto ts_ms and find VM pauses. The clock is
# the driver's, not a source's: every source shares ts_ms.
CLK=
start_clock() {
	mkdir "$TRACEFS/instances/qmem-clock-$$" 2>/dev/null || return 0
	CLK="$TRACEFS/instances/qmem-clock-$$"
	echo local > "$CLK/trace_clock" && echo 1 > "$CLK/tracing_on" ||
		stop_clock
}

anchor() {
	[ -n "$CLK" ] || return 0
	echo "qmem-anchor $(( $(now_ms) - START_MS ))" > "$CLK/trace_marker" 2>/dev/null || true
}

# Write clock.csv (when there is a directory to write it to) and remove the
# instance.
stop_clock() {
	[ -n "$CLK" ] || return 0
	if [ -n "${OUT-}" ] && [ -d "$OUT" ]; then
		{
			echo "local_s,ts_ms"
			sed -n 's/.* \([0-9][0-9]*\.[0-9]*\): tracing_mark_write: qmem-anchor \([0-9]*\).*/\1,\2/p' \
				"$CLK/trace"
		} > "$OUT/clock.csv"
	fi
	rmdir "$CLK" 2>/dev/null || true
	CLK=
}

# VM pauses (seen under WSL): between two anchors the wall clock (ts_ms)
# moves much more than the kernel's clock. The application and replay run
# on monotonic time and do not see the pause; every ts_ms after it is late
# by its length.
find_pauses() {
	awk -F, '
		NR > 1 && $1 ~ /^[0-9.]+$/ {
			n++
			L[n] = $1
			T[n] = $2
		}
		END {
			print "ts_ms,pause_ms"
			# the usual rate of the trace clock against the wall clock
			for (i = 2; i <= n; i++)
				if (T[i] > T[i - 1])
					r[++m] = (L[i] - L[i - 1]) * 1000 / (T[i] - T[i - 1])
			for (i = 2; i <= m; i++)
				for (j = i; j > 1 && r[j - 1] > r[j]; j--) {
					x = r[j]; r[j] = r[j - 1]; r[j - 1] = x
				}
			rate = m ? r[int((m + 1) / 2)] : 1
			for (i = 2; i <= n; i++) {
				pause = (T[i] - T[i - 1]) - (L[i] - L[i - 1]) * 1000 / rate
				if (pause > 300)
					printf "%d,%.0f\n", T[i - 1], pause
			}
		}' "$1"
}
