#!/usr/bin/env bash
# measure-overhead: wall time of a command with and without qmem-record.
#
# Runs the command -n times plainly and -n times under qmem-record.sh
# (alternating, to spread out drift) and prints median times and the
# overhead. Use a command with a fixed amount of work (workloads/fixed_work);
# the recorded time includes qmem-record's own start and finish.
set -euo pipefail

usage() {
	cat >&2 <<-EOF
	usage: measure-overhead.sh [-n <runs>] [-r "<qmem-record options>"] -- <command> [args...]

	  -n <runs>     runs of each kind (default 5)
	  -r <options>  extra qmem-record.sh options, e.g. "-i 500"
	EOF
	exit 2
}

runs=5
record_options=
while [ $# -gt 0 ]; do
	case $1 in
	-n) [ $# -ge 2 ] || usage; runs=$2; shift 2 ;;
	-r) [ $# -ge 2 ] || usage; record_options=$2; shift 2 ;;
	--) shift; break ;;
	*) usage ;;
	esac
done
[[ $runs =~ ^[1-9][0-9]*$ ]] && [ $# -gt 0 ] || usage

RECORD=${QMEM_RECORD:-$(dirname "$0")/../record/qmem-record.sh}
read -ra record_opts <<< "$record_options"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

elapsed_ns() {
	local start end

	start=$(date +%s%N)
	"$@" > /dev/null 2>> "$tmp/stderr" || {
		cat "$tmp/stderr" >&2
		echo "measure-overhead: failed: $*" >&2
		exit 1
	}
	end=$(date +%s%N)
	echo $(( end - start ))
}

: > "$tmp/baseline"
: > "$tmp/recorded"
for i in $(seq "$runs"); do
	elapsed_ns "$@" >> "$tmp/baseline"
	elapsed_ns bash "$RECORD" -o "$tmp/run$i" "${record_opts[@]}" -- "$@" \
		>> "$tmp/recorded"
done

median_s() {
	sort -n "$1" | awk '
		{ v[NR] = $1 }
		END { m = NR % 2 ? v[(NR + 1) / 2] : (v[NR / 2] + v[NR / 2 + 1]) / 2
		      printf "%.6f", m / 1e9 }'
}

base=$(median_s "$tmp/baseline")
rec=$(median_s "$tmp/recorded")
echo "runs $runs"
awk -v b="$base" -v r="$rec" 'BEGIN {
	printf "baseline_s %.3f\nrecorded_s %.3f\noverhead_pct %.1f\n", b, r, (r - b) / b * 100
}'
