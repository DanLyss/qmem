# source smaps: copies of /proc/<pid>/smaps (mappings and their Rss).

src_smaps_check() { :; }

src_smaps_start() {
	mkdir -p "$OUT/smaps"
}

src_smaps_tick() {
	local snap="$OUT/smaps/$1.txt"

	cat "/proc/$APP_PID/smaps" > "$snap" 2>/dev/null || true
	[ -s "$snap" ] || rm -f "$snap"		# the target has just exited
}

src_smaps_stop() { :; }
src_smaps_finish() { :; }

src_smaps_manifest() {
	printf '"version": 1, "files": ["smaps/"], "interval_ms": %s' "$INTERVAL"
}
