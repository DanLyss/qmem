"""End-to-end: real damo and DAMON on hot_region. Needs root."""
import re

import pytest

from conftest import record_json, HOT_REGION, has_real_damon, run_record

pytestmark = pytest.mark.skipif(not has_real_damon(),
                                reason='needs root, DAMON sysfs and damo')


def anon_mapping_containing(smaps_text, addr):
    for line in smaps_text.splitlines():
        m = re.match(r'^([0-9a-f]+)-([0-9a-f]+) \S+ \S+ \S+ \S+\s*(.*)$', line)
        if m and int(m[1], 16) <= addr < int(m[2], 16):
            return m[3].strip() in ('', '[heap]')
    return False


def test_record_hot_region(tmp_path):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '500', '--',
                    HOT_REGION, '256', '16', '8'], timeout=120)
    assert p.returncode == 0, p.stderr
    hot0, hot1 = (int(x, 16) for x in
                  re.search(r'^hot 0x([0-9a-f]+) 0x([0-9a-f]+)$',
                            p.stdout, re.M).groups())
    total0, total1 = (int(x, 16) for x in
                      re.search(r'^total 0x([0-9a-f]+) 0x([0-9a-f]+)$',
                                p.stdout, re.M).groups())

    # the hot range lives in an anonymous mapping
    snaps = sorted((out / 'smaps').iterdir(), key=lambda f: int(f.stem))
    assert any(anon_mapping_containing(f.read_text(), hot0) for f in snaps)

    # in the second half of the run DAMON sees the hot range as hotter than
    # the rest of the mapping
    rows = [line.split(',') for line in
            (out / 'damon' / 'access.csv').read_text().splitlines()[1:]]
    assert rows
    last_ns = max(int(r[1]) for r in rows)
    hot_rates, cold_rates = [], []
    for r in rows:
        start, end, rate = int(r[2]), int(r[3]), float(r[4])
        if int(r[0]) < last_ns / 2:
            continue
        if start < hot1 and end > hot0:
            hot_rates.append(rate)
        elif start >= hot1 and end <= total1:
            cold_rates.append(rate)
    assert hot_rates, 'no DAMON region covers the hot range'
    assert max(hot_rates) > 0
    if cold_rates:
        assert (sum(hot_rates) / len(hot_rates)
                > sum(cold_rates) / len(cold_rates))


def test_too_short_command_fails_clearly(tmp_path):
    # real damo crashes without writing a record if the target exits at once
    p = run_record(['-o', tmp_path / 'out', '--', 'true'], timeout=60)
    assert p.returncode != 0
    assert 'exited before damo saved a record' in p.stderr


def test_damon_time_matches_record_time(tmp_path):
    """DAMON snapshots are on the record's one clock (boot): they start soon
    after the record, end before it and last one 100 ms aggregation."""
    import json
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '200', '--', HOT_REGION, '64', '4', '8'],
                   timeout=120)
    assert p.returncode == 0, p.stderr
    meta = record_json(out)
    rows = [l.split(',') for l in (out / 'damon' / 'access.csv').read_text().splitlines()[1:]]
    last_end_ms = max(int(r[1]) for r in rows) / 1e6
    run_ms = (meta['time']['end_time'] - meta['time']['start_time']) * 1000
    assert last_end_ms <= run_ms
    assert 0 <= min(int(r[0]) for r in rows) / 1e6 < 2000
    durations = sorted({(int(r[1]) - int(r[0])) / 1e6 for r in rows})
    median = durations[len(durations) // 2]
    # one 100 ms aggregation; kdamond runs it a little longer under load
    assert 95 <= median <= 115, median
