# qmem-record: helpers shared by the driver and the sources (sourced).

# tracefs mount (damon sets its trace clock); tests point it elsewhere
TRACEFS=${QMEM_TRACEFS:-/sys/kernel/tracing}

die() {
	echo "qmem-record: $*" >&2
	exit 1
}

warn() {
	echo "qmem-record: $*" >&2
}

# The record's one clock: CLOCK_MONOTONIC (mono) in milliseconds, into
# NOW_MS. damo stamps its snapshots on it too (the damon source sets the
# trace clock to mono). bash cannot read it, so python3 does.
now_ms() {
	NOW_MS=$(python3 -c 'import time; print(time.clock_gettime_ns(time.CLOCK_MONOTONIC) // 1000000)')
}

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

	read -r stat 2>/dev/null < "/proc/$1/stat" || return 1
	stat=${stat##*) }
	[ "${stat%% *}" != Z ]
}
