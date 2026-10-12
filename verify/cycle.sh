#!/usr/bin/env bash
# cycle: record an application, build a profile, record its replay and
# verify the replay against the original, in one go.
#
#   <dir>/orig         record of the original
#   <dir>/profile.txt  profile built from it
#   <dir>/again        record of the replay
#   <dir>/layout.txt   where replay put its regions, when it started
#   <dir>/verify.txt   verify's report; verify.json the same as JSON
#   <dir>/orig2, baseline.json   with --baseline: a second record of the
#                      original and the noise between the two
#
# Exit status: 0 if verify --check passes, 1 if not, 2 on usage errors.
# See docs/modules/verify.md.
set -euo pipefail

usage() {
	cat >&2 <<-EOF
	usage: cycle.sh -o <dir> [-i <ms>] [-w <ms>] [--baseline] [-R "<record options>"]
	                [-V "<verify options>"] -- <command> [args...]

	  -o <dir>   output directory (must be empty or absent)
	  -i <ms>    record snapshot period (default 500)
	  -w <ms>    record soft-dirty period (default off)
	  --baseline record the original twice; verify against its noise
	  -R <opts>  extra qmem-record options, e.g. -D "<damo options>"
	  -V <opts>  extra qmem-verify options
	EOF
	exit 2
}

here=$(cd "$(dirname "$0")" && pwd)
root=$(dirname "$here")

out=
interval=500
writes=
verify_opts=
record_extra=
baseline=false
while [ $# -gt 0 ]; do
	case $1 in
	-o) [ $# -ge 2 ] || usage; out=$2; shift 2 ;;
	-i) [ $# -ge 2 ] || usage; interval=$2; shift 2 ;;
	-w) [ $# -ge 2 ] || usage; writes=$2; shift 2 ;;
	-V) [ $# -ge 2 ] || usage; verify_opts=$2; shift 2 ;;
	-R) [ $# -ge 2 ] || usage; record_extra=$2; shift 2 ;;
	--baseline) baseline=true; shift ;;
	--) shift; break ;;
	*) usage ;;
	esac
done
[ -n "$out" ] && [ $# -gt 0 ] || usage
if [ -e "$out" ] && [ -n "$(ls -A "$out")" ]; then
	echo "cycle: $out is not empty" >&2
	exit 2
fi
mkdir -p "$out"

record_opts=(-i "$interval")
[ -z "$writes" ] || record_opts+=(-w "$writes")
[ -z "$record_extra" ] || eval "record_opts+=($record_extra)"
read -ra extra <<< "$verify_opts"

echo "cycle: recording the original" >&2
bash "$root/record/qmem-record.sh" -o "$out/orig" "${record_opts[@]}" -- "$@"

if $baseline; then
	echo "cycle: recording the original again (baseline)" >&2
	bash "$root/record/qmem-record.sh" -o "$out/orig2" "${record_opts[@]}" -- "$@"
	python3 "$root/verify/qmem-verify.py" --baseline "$out/orig" "$out/orig2" --json \
		> "$out/baseline.json"
	extra+=(--noise "$out/baseline.json")
fi

echo "cycle: building the profile" >&2
python3 "$root/tools/mkprofile.py" "$out/orig" -o "$out/profile.txt"

echo "cycle: recording the replay" >&2
bash "$root/record/qmem-record.sh" -o "$out/again" "${record_opts[@]}" -- \
	"$root/replay/qmem-replay" "$out/profile.txt" --layout "$out/layout.txt"

echo "cycle: verifying" >&2
verify=(python3 "$root/verify/qmem-verify.py" "$out/orig" "$out/again"
	--profile "$out/profile.txt" --layout "$out/layout.txt" "${extra[@]}")
"${verify[@]}" --json > "$out/verify.json" || true
"${verify[@]}" --check > "$out/verify.txt" && rc=0 || rc=$?
cat "$out/verify.txt"
exit $rc
