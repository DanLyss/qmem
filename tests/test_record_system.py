"""R4: system.csv with meminfo, PSI and vmstat; damo options via -D."""
import json

from conftest import HOT_REGION, PROC_HEADER, SYSTEM_HEADER, damo_calls, run_record


def test_system_csv(tmp_path, fake_env):
    out = tmp_path / 'out'
    # 0.5 s delay: the first snapshots come before the 64 MB of first touches
    p = run_record(['-o', out, '-i', '100', '--',
                    HOT_REGION, '64', '4', '1', '0.5'], env=fake_env)
    assert p.returncode == 0, p.stderr
    assert not (out / 'meminfo.csv').exists()
    lines = (out / 'system.csv').read_text().splitlines()
    assert lines[0] == SYSTEM_HEADER
    rows = [line.split(',') for line in lines[1:]]
    assert len(rows) >= 5
    assert all(len(r) == 11 for r in rows)
    for r in rows:
        ints = [int(x) for x in r[:5] + r[7:]]
        psi = [float(x) for x in r[5:7]]
        assert ints[1] > 0 and ints[2] > 0
        assert all(0 <= x <= 100 for x in psi)
    pgfault = [int(r[7]) for r in rows]
    assert pgfault == sorted(pgfault)
    # 64 MB of first-touch writes is 16384 page faults at 4 KB pages
    assert pgfault[-1] - pgfault[0] >= 16000


def test_damo_options_are_passed(tmp_path, fake_env):
    out = tmp_path / 'out'
    opts = '--monitoring_intervals 5ms 100ms 1s --monitoring_nr_regions_range 10 1000'
    p = run_record(['-o', out, '-D', opts, '--', 'true'], env=fake_env)
    assert p.returncode == 0, p.stderr
    rec = [c for c in damo_calls(fake_env) if c[:1] == ['record']][0]
    assert rec[1:3] == ['--do_record', 'access']
    joined = ' '.join(rec[3:-1])
    assert opts in joined
    assert rec[-1] == 'true'
    assert json.loads((out / 'meta.json').read_text())['damo_options'] == opts


def test_proc_csv_counts_faults_since_start(tmp_path, fake_env):
    """The application's own fault counters, including the faults before the
    first snapshot (system.csv misses those)."""
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--',
                    HOT_REGION, '64', '4', '1'], env=fake_env)
    assert p.returncode == 0, p.stderr
    lines = (out / 'proc.csv').read_text().splitlines()
    assert lines[0] == PROC_HEADER
    rows = [[int(x) for x in l.split(',')] for l in lines[1:]]
    assert len(rows) >= 5
    minflt = [r[1] for r in rows]
    assert minflt == sorted(minflt)
    # 64 MB written at once, right at the start: counted although most of it
    # may happen before the first snapshot
    assert minflt[-1] >= 16000
    assert max(r[3] for r in rows) >= 60 * 1024
