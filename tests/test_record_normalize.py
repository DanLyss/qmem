"""R2: DAMON data is normalized into access.csv through `damo report access`."""
import subprocess
import sys

import pytest

from conftest import (ACCESS_HEADER, HOT_REGION, MKDAMON, damo_calls,
                      has_damo, run_record)


def read_csv(path):
    lines = path.read_text().splitlines()
    return lines[0], [line.split(',') for line in lines[1:]]


def test_record_writes_access_csv(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--', HOT_REGION, '8', '2', '0.3'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(out / 'access.csv')
    assert header == ACCESS_HEADER
    # fake damo's canned report: 2 snapshots x 2 regions
    assert rows == [
        ['0', '100000000', '4096', '20480', '100.000000', '200.000000', '300000'],
        ['0', '100000000', '20480', '1048576', '0.000000', '0.000000', '700000'],
        ['100000000', '200000000', '4096', '20480', '50.000000', '100.000000', '400000'],
        ['100000000', '200000000', '20480', '1048576', '0.000000', '0.000000', '800000'],
    ]
    reports = [c for c in damo_calls(fake_env) if c[:2] == ['report', 'access']]
    assert len(reports) == 1
    assert str(out / 'damon.data') in reports[0]
    assert '--raw_number' in reports[0]


def test_normalize_requires_damon_data(tmp_path, fake_env):
    p = run_record(['--normalize', tmp_path], env=fake_env)
    assert p.returncode != 0
    assert 'damon.data' in p.stderr


def test_normalize_overwrites_access_csv(tmp_path, fake_env):
    (tmp_path / 'damon.data').write_text('fake')
    (tmp_path / 'access.csv').write_text('stale\n')
    p = run_record(['--normalize', tmp_path], env=fake_env)
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(tmp_path / 'access.csv')
    assert header == ACCESS_HEADER
    assert len(rows) == 4


@pytest.mark.skipif(not has_damo(), reason='damo is not installed')
def test_normalize_with_real_damo_report(tmp_path):
    """Real `damo report` on a damon.data written by damo's own classes."""
    subprocess.run([sys.executable, MKDAMON, tmp_path / 'damon.data'],
                   check=True)
    p = run_record(['--normalize', tmp_path])
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(tmp_path / 'access.csv')
    assert header == ACCESS_HEADER
    assert len(rows) == 6
    for i in range(3):
        hot, cold = rows[2 * i], rows[2 * i + 1]
        start_ns, end_ns = str(i * 100_000_000), str((i + 1) * 100_000_000)
        assert hot[:4] == [start_ns, end_ns, str(0x1000), str(0x5000)]
        assert cold[:4] == [start_ns, end_ns, str(0x5000), str(0x100000)]
        # 20 of 20 samples -> 100 %, 200 Hz at 5 ms sampling; ages in us
        assert float(hot[4]) == 100.0 and float(hot[5]) == 200.0
        assert float(cold[4]) == 0.0 and float(cold[5]) == 0.0
        assert int(hot[6]) == 300000 and int(cold[6]) == 700000


def test_normalize_maps_damon_time_to_record_time(tmp_path, fake_env):
    """With clock anchors (trace 'local' clock seconds -> ts_ms), DAMON times
    are moved to the record's time base: here the trace clock runs 10 %
    fast and the canned record starts at 5.0 s of it."""
    (tmp_path / 'damon.data').write_text('fake')
    (tmp_path / 'clock.csv').write_text('local_s,ts_ms\n5.0,0\n6.1,1000\n')
    p = run_record(['--normalize', tmp_path], env=fake_env)
    assert p.returncode == 0, p.stderr
    _, rows = read_csv(tmp_path / 'access.csv')
    starts = sorted({(int(r[0]), int(r[1])) for r in rows})
    assert starts == [(0, 90909091), (90909091, 181818182)]


def test_normalize_without_anchors_keeps_damo_time(tmp_path, fake_env):
    (tmp_path / 'damon.data').write_text('fake')
    p = run_record(['--normalize', tmp_path], env=fake_env)
    assert p.returncode == 0, p.stderr
    _, rows = read_csv(tmp_path / 'access.csv')
    assert sorted({int(r[0]) for r in rows}) == [0, 100000000]


def test_normalize_finds_pauses(tmp_path, fake_env):
    """A paused VM: wall-clock ts_ms jumps 3.5 s while the trace clock moves
    0.25 s. normalize writes the pause to pauses.csv (the application and
    replay, on monotonic time, never saw it)."""
    (tmp_path / 'damon.data').write_text('fake')
    rows = ['local_s,ts_ms']
    local, ts = 5.0, 0
    for i in range(20):
        rows.append('%.6f,%d' % (local, ts))
        if i == 9:
            local, ts = local + 0.25 * 1.1, ts + 3500      # paused ~3.25 s
        else:
            local, ts = local + 0.33 * 1.1, ts + 330
    (tmp_path / 'clock.csv').write_text('\n'.join(rows) + '\n')
    p = run_record(['--normalize', tmp_path], env=fake_env)
    assert p.returncode == 0, p.stderr
    lines = (tmp_path / 'pauses.csv').read_text().splitlines()
    assert lines[0] == 'ts_ms,pause_ms'
    assert len(lines) == 2
    ts_ms, pause_ms = (int(x) for x in lines[1].split(','))
    assert ts_ms == 9 * 330
    assert pause_ms == pytest.approx(3250, abs=20)


def test_normalize_no_pauses(tmp_path, fake_env):
    (tmp_path / 'damon.data').write_text('fake')
    (tmp_path / 'clock.csv').write_text('local_s,ts_ms\n5.0,0\n5.363,330\n5.726,660\n')
    assert run_record(['--normalize', tmp_path], env=fake_env).returncode == 0
    assert (tmp_path / 'pauses.csv').read_text().splitlines() == ['ts_ms,pause_ms']
