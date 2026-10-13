# source proc: the target's own fault counters since it started (so faults
# before the first snapshot count too) and its RSS, from /proc/<pid>/stat.

PROC_PAGE_KB=$(( $(getconf PAGESIZE) / 1024 ))

src_proc_check() { :; }

src_proc_start() {
	mkdir -p "$OUT/proc"
	echo "ts_ms,minflt,majflt,rss_kb" > "$OUT/proc/proc.csv"
}

src_proc_tick() {
	local stat

	stat=$(cat "/proc/$APP_PID/stat" 2>/dev/null) || return 0
	set -- "$1" ${stat##*) }
	# after the comm: $2 = state (field 3), minflt = field 10,
	# majflt = field 12, rss in pages = field 24
	echo "$1,${9},${11},$(( ${23} * PROC_PAGE_KB ))" >> "$OUT/proc/proc.csv"
}

src_proc_stop() { :; }
src_proc_finish() { :; }

src_proc_manifest() {
	printf '"version": 1, "files": ["proc/proc.csv"]'
}
