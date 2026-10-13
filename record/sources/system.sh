# source system: memory of the whole system (meminfo, PSI, vmstat).

SYSTEM_PSI=/proc/pressure/memory
[ -r "$SYSTEM_PSI" ] || SYSTEM_PSI=/dev/null

src_system_check() { :; }

src_system_start() {
	mkdir -p "$OUT/system"
	echo "ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout" \
		> "$OUT/system/system.csv"
}

src_system_tick() {
	awk -v ts="$1" '
		FILENAME == "/proc/meminfo" && $1 == "MemFree:"      { mf = $2 }
		FILENAME == "/proc/meminfo" && $1 == "MemAvailable:" { ma = $2 }
		FILENAME == "/proc/meminfo" && $1 == "Cached:"       { ca = $2 }
		FILENAME == "/proc/meminfo" && $1 == "SwapFree:"     { sf = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pgfault"        { pf = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pgmajfault"     { pm = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pswpin"         { si = $2 }
		FILENAME == "/proc/vmstat" && $1 == "pswpout"        { so = $2 }
		$1 == "some" || $1 == "full" {
			split($2, kv, "=")
			psi[$1] = kv[2]
		}
		END {
			print ts "," mf "," ma "," ca "," sf "," \
				(psi["some"] == "" ? 0 : psi["some"]) "," \
				(psi["full"] == "" ? 0 : psi["full"]) "," \
				pf + 0 "," pm + 0 "," si + 0 "," so + 0
		}' /proc/meminfo "$SYSTEM_PSI" /proc/vmstat >> "$OUT/system/system.csv"
}

src_system_stop() { :; }
src_system_finish() { :; }

src_system_manifest() {
	printf '"version": 1, "files": ["system/system.csv"]'
}
