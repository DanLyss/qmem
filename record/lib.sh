# qmem-record: helpers shared by the driver and the sources (sourced).

# tracefs mount (damon sets its trace clock, syscalls traces); tests point it elsewhere
TRACEFS=${QMEM_TRACEFS:-/sys/kernel/tracing}

die() {
	echo "qmem-record: $*" >&2
	exit 1
}

warn() {
	echo "qmem-record: $*" >&2
}

# The record's one clock: milliseconds since boot (CLOCK_BOOTTIME, 10 ms
# steps) into NOW_MS. softdirty and syscalls count on it too; the damon source
# converts damo's (mono) times to it. No subshell: a
# signal arriving during a command substitution can confuse bash's traps.
now_ms() {
	local up

	read -r up _ < /proc/uptime
	NOW_MS=$(( 10#${up/./} * 10 ))
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
