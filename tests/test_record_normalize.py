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
