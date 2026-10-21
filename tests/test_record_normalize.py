"""DAMON data is normalized into access.csv through `damo report access`.

damo stamps snapshots with the trace clock, which the damon source sets to
`boot` while it records: the record's only clock. access.csv has them as
ns since the record's start (START_MS)."""
import subprocess
import sys

import pytest

from conftest import (ACCESS_HEADER, HOT_REGION, MKDAMON, ROOT, damo_calls,
                      has_damo, run_record)


def normalize(d, env=None, start_ms=0):
    """src_damon_normalize of the damon source on a record directory whose
    record started at <start_ms>; damo's clock taken as the record's (no
    boot - mono offset)."""
    script = ('HERE=%s; . "$HERE/lib.sh"; . "$HERE/sources/damon.sh"; START_MS=%d; '
              'src_damon_normalize "$1"') % (ROOT / 'record', start_ms)
    return subprocess.run(['bash', '-c', script, 'normalize', str(d)], env=env,
                          capture_output=True, text=True, timeout=60)


def read_csv(path):
    lines = path.read_text().splitlines()
    return lines[0], [line.split(',') for line in lines[1:]]


def test_record_writes_access_csv(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--', HOT_REGION, '8', '2', '0.3'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(out / 'damon' / 'access.csv')
    assert header == ACCESS_HEADER
    # fake damo's report: 2 snapshots x 2 regions, its record starting when
    # damo did, shortly after the record
    assert [r[2:] for r in rows] == [
        ['4096', '20480', '100.000000', '200.000000', '300000'],
        ['20480', '1048576', '0.000000', '0.000000', '700000'],
        ['4096', '20480', '50.000000', '100.000000', '400000'],
        ['20480', '1048576', '0.000000', '0.000000', '800000'],
    ]
    t0 = int(rows[0][0])
    assert 0 <= t0 < 2e9
    assert [(int(r[0]) - t0, int(r[1]) - t0) for r in rows[::2]] == \
        [(0, 100000000), (100000000, 200000000)]
    reports = [c for c in damo_calls(fake_env) if c[:2] == ['report', 'access']]
    assert len(reports) == 1
    assert str(out / 'damon' / 'damon.data') in reports[0]
    assert '--raw_number' in reports[0]


def test_normalize_requires_damon_data(tmp_path, fake_env):
    p = normalize(tmp_path, fake_env)
    assert p.returncode != 0
    assert 'damon.data' in p.stderr


def test_normalize_overwrites_access_csv(tmp_path, fake_env):
    (tmp_path / 'damon').mkdir()
    (tmp_path / 'damon' / 'damon.data').write_text('5000000000\n')
    (tmp_path / 'damon' / 'access.csv').write_text('stale\n')
    p = normalize(tmp_path, fake_env, start_ms=5000)
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(tmp_path / 'damon' / 'access.csv')
    assert header == ACCESS_HEADER
    assert len(rows) == 4


@pytest.mark.skipif(not has_damo(), reason='damo is not installed')
def test_normalize_with_real_damo_report(tmp_path):
    """Real `damo report` on a damon.data written by damo's own classes."""
    (tmp_path / 'damon').mkdir()
    subprocess.run([sys.executable, MKDAMON, tmp_path / 'damon' / 'damon.data'],
                   check=True)
    p = normalize(tmp_path)
    assert p.returncode == 0, p.stderr
    header, rows = read_csv(tmp_path / 'damon' / 'access.csv')
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


def test_normalize_counts_from_the_record_start(tmp_path, fake_env):
    """damo's record starts at 5.0 s of the boot clock, the record at 4.9 s:
    the first snapshot is 100 ms into the record."""
    (tmp_path / 'damon').mkdir()
    (tmp_path / 'damon' / 'damon.data').write_text('5000000000\n')
    p = normalize(tmp_path, fake_env, start_ms=4900)
    assert p.returncode == 0, p.stderr
    _, rows = read_csv(tmp_path / 'damon' / 'access.csv')
    assert sorted({(int(r[0]), int(r[1])) for r in rows}) == \
        [(100000000, 200000000), (200000000, 300000000)]


def test_boot_minus_mono_offset_survives_the_stop(tmp_path, fake_env):
    """damo's times are mono; the boot - mono offset measured at the start is
    still added after the trace clock was put back (which once reset it)."""
    (tmp_path / 'damon').mkdir()
    (tmp_path / 'damon' / 'damon.data').write_text('5000000000\n')
    (tmp_path / 'trace_clock').write_text('[mono] local\n')
    script = ('HERE=%s; . "$HERE/lib.sh"; . "$HERE/sources/damon.sh"; START_MS=5000; '
              'TRACEFS=%s; DAMON_OLD_CLOCK=local; DAMON_BOOT_MINUS_MONO_NS=300000000; '
              'src_damon_restore_clock; src_damon_normalize "$1"') % (ROOT / 'record', tmp_path)
    p = subprocess.run(['bash', '-c', script, 'normalize', str(tmp_path)], env=fake_env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    _, rows = read_csv(tmp_path / 'damon' / 'access.csv')
    assert min(int(r[0]) for r in rows) == 300000000
