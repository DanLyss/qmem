# source smaps: copies of /proc/<pid>/smaps (mappings and their Rss).

src_smaps_check() { :; }

src_smaps_start() {
	mkdir -p "$OUT/smaps"
}

# Returns 1 when the target has gone (its smaps is empty or unreadable).
src_smaps_tick() {
	local snap="$OUT/smaps/$1.txt"

	cat "/proc/$APP_PID/smaps" > "$snap" 2>/dev/null || true
	if [ ! -s "$snap" ]; then
		rm -f "$snap"
		return 1
	fi
}

src_smaps_stop() { :; }
src_smaps_finish() { :; }

src_smaps_manifest() {
	printf '"version": 1, "files": ["smaps/"], "interval_ms": %s' "$INTERVAL"
}
