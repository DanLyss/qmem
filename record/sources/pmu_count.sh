# source pmu_count: STUB. Will record total memory accesses of the target (perf_event_open, counting mode).
# For now it only checks whether the processor's counters are there:
# without them it is skipped (unavailable), with them it records nothing
# yet (stub).

src_pmu_count_check() {
	if ! ls /sys/bus/event_source/devices 2>/dev/null | grep -qE '^(cpu|cpu_core|cpu_atom|armv[0-9].*|arm_spe.*)$'; then
		SRC_REASON="no hardware PMU (/sys/bus/event_source/devices has no cpu PMU)"
		return 1
	fi
	SRC_REASON="stub: not implemented yet"
	return 3	# available, but a stub: nothing to record
}

src_pmu_count_start() { :; }
src_pmu_count_tick() { :; }
src_pmu_count_stop() { :; }
src_pmu_count_finish() { :; }
src_pmu_count_manifest() { printf '"version": 0'; }
