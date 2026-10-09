#!/usr/bin/env bash
# qmem-record: record an application's memory load (iteration 1).
#
# The DAMON access pattern is recorded by `damo record`; smaps and meminfo
# snapshots are taken by this script. See docs/modules/record.md.
set -euo pipefail

usage() {
	cat >&2 <<-EOF
	usage: qmem-record.sh -o <dir> [-i <ms>] -- <command> [args...]

	  -o <dir>   output directory (must be empty or absent)
	  -i <ms>    smaps/meminfo snapshot period (default 1000)
	EOF
	exit 2
}

die() {
	echo "qmem-record: $*" >&2
	exit 1
}

# damo command; tests point this at a fake
read -ra DAMO <<< "${QMEM_DAMO:-damo}"

now_ms() { date +%s%3N; }

json_str() {
	local s=${1//\\/\\\\}
	s=${s//\"/\\\"}
	s=${s//$'\t'/\\t}
	s=${s//$'\n'/\\n}
	printf '"%s"' "$s"
}

out=
interval=1000
while [ $# -gt 0 ]; do
	case $1 in
	-o) [ $# -ge 2 ] || usage; out=$2; shift 2 ;;
	-i) [ $# -ge 2 ] || usage; interval=$2; shift 2 ;;
	--) shift; break ;;
	*) usage ;;
	esac
done
[ -n "$out" ] && [ $# -gt 0 ] || usage
[[ $interval =~ ^[1-9][0-9]*$ ]] || usage

if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
	die "$out is not empty"
fi
mkdir -p "$out/smaps"

command=$(printf '%q ' "$@")
command=${command% }
start_time=$(date +%s.%N)
start_ms=$(now_ms)

"${DAMO[@]}" record --do_record access -o "$out/damon.data" "$command" &
damo_pid=$!

interrupted=false
on_signal() {
	interrupted=true
	kill -INT "$damo_pid" 2>/dev/null || true
}
trap on_signal INT TERM

# damo starts the command itself; the application is its first child
app_pid=
for _ in $(seq 100); do
	app_pid=$(pgrep -o -P "$damo_pid" || true)
	[ -n "$app_pid" ] && break
	kill -0 "$damo_pid" 2>/dev/null || break
	sleep 0.05
done

echo "ts_ms,MemFree,MemAvailable,Cached,SwapFree" > "$out/meminfo.csv"
sleep_s=$(awk -v ms="$interval" 'BEGIN { print ms / 1000 }')

while [ -n "$app_pid" ] && ! $interrupted && kill -0 "$app_pid" 2>/dev/null; do
	ts=$(( $(now_ms) - start_ms ))
	snap="$out/smaps/$ts.txt"
	cat "/proc/$app_pid/smaps" > "$snap" 2>/dev/null || true
	if [ ! -s "$snap" ]; then
		# the application exited (or is a zombie waiting for damo)
		rm -f "$snap"
		break
	fi
	awk -v ts="$ts" '
		/^MemFree:/      { f = $2 }
		/^MemAvailable:/ { a = $2 }
		/^Cached:/       { c = $2 }
		/^SwapFree:/     { s = $2 }
		END { print ts "," f "," a "," c "," s }' /proc/meminfo >> "$out/meminfo.csv"
	# sleep in the background so a signal is handled right away
	sleep "$sleep_s" &
	wait $! || true
done

# damo leaves the target alone when it is interrupted
if $interrupted && [ -n "$app_pid" ]; then
	kill -TERM "$app_pid" 2>/dev/null || true
fi

damo_rc=0
while kill -0 "$damo_pid" 2>/dev/null; do
	wait "$damo_pid" || damo_rc=$?
done
end_time=$(date +%s.%N)

if $interrupted; then
	[ -f "$out/damon.data" ] || die "damo did not write $out/damon.data"
	damo_rc=0
fi
[ "$damo_rc" -eq 0 ] || die "damo record failed (exit $damo_rc)"

cat > "$out/meta.json" <<EOF
{
  "command": $(json_str "$command"),
  "damo_version": $(json_str "$("${DAMO[@]}" version)"),
  "kernel": $(json_str "$(uname -r)"),
  "start_time": $start_time,
  "end_time": $end_time,
  "interval_ms": $interval,
  "interrupted": $interrupted
}
EOF
