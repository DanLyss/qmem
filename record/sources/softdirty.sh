# source softdirty: written pages per mapping, sampled every SOFTDIRTY_MS by
# the qmem-softdirty helper (soft-dirty bits of /proc/<pid>/pagemap).

# the helper; tests point it elsewhere
SOFTDIRTY=${QMEM_SOFTDIRTY:-$(dirname "${BASH_SOURCE[0]}")/../qmem-softdirty}
SOFTDIRTY_MS=1000

SD_PID=

src_softdirty_check() {
	[ -x "$SOFTDIRTY" ] || {
		SRC_REASON="$SOFTDIRTY not found (run make)"
		return 1
	}
}

src_softdirty_start() {
	mkdir -p "$OUT/softdirty"
	echo "ts_ms,start,end,present_pages,dirty_pages,rewritten_pages" > "$OUT/softdirty/writes.csv"
	"$SOFTDIRTY" "$APP_PID" "$SOFTDIRTY_MS" "$START_MS" >> "$OUT/softdirty/writes.csv" &
	SD_PID=$!
}

src_softdirty_tick() { :; }

src_softdirty_stop() {
	[ -n "$SD_PID" ] || return 0
	kill -TERM "$SD_PID" 2>/dev/null || true
	wait "$SD_PID" 2>/dev/null || true
	SD_PID=
}

src_softdirty_finish() { :; }

src_softdirty_manifest() {
	printf '"version": 1, "files": ["softdirty/writes.csv"], "period_ms": %s' "$SOFTDIRTY_MS"
}
