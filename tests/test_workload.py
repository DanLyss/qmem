import re
import subprocess

from conftest import FIXED_WORK, HOT_REGION, RANDOM_ACCESS, ROOT, RW_SPLIT


def test_hot_region_prints_ranges_and_exits():
    p = subprocess.run([str(HOT_REGION), '8', '2', '0.2'],
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    total = re.search(r'^total 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M)
    hot = re.search(r'^hot 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M)
    assert total and hot
    t0, t1 = (int(x, 16) for x in total.groups())
    h0, h1 = (int(x, 16) for x in hot.groups())
    assert t1 - t0 == 8 << 20
    assert h1 - h0 == 2 << 20
    assert t0 <= h0 < h1 <= t1


def test_hot_region_rejects_bad_args():
    for args in ([], ['8', '16', '1'], ['0', '0', '1'], ['8', '2', '1', '-1']):
        p = subprocess.run([str(HOT_REGION)] + args, capture_output=True,
                           timeout=10)
        assert p.returncode == 2


def test_hot_region_delay():
    p = subprocess.run([str(HOT_REGION), '8', '2', '0', '0.3'],
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0


def test_rw_split_prints_regions():
    p = subprocess.run([str(RW_SPLIT), '4', '2', '0.2'],
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    a = re.search(r'^a 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M)
    b = re.search(r'^b 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M)
    assert a and b
    a0, a1 = (int(x, 16) for x in a.groups())
    b0, b1 = (int(x, 16) for x in b.groups())
    assert a1 - a0 == 4 << 20 and b1 - b0 == 2 << 20
    assert a1 <= b0 or b1 <= a0


def test_fixed_work_runs_and_rejects_bad_args():
    p = subprocess.run([str(FIXED_WORK), '8', '2'], capture_output=True,
                       timeout=10)
    assert p.returncode == 0
    for args in ([], ['0', '1'], ['8', '0']):
        p = subprocess.run([str(FIXED_WORK)] + args, capture_output=True,
                           timeout=10)
        assert p.returncode == 2


def test_random_access_prints_core_and_rejects_bad_args():
    p = subprocess.run([str(RANDOM_ACCESS), '8', '2', '0.2', '10000'],
                       capture_output=True, text=True, timeout=10)
    assert p.returncode == 0, p.stderr
    t0, t1 = (int(x, 16) for x in re.search(
        r'^total 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M).groups())
    c0, c1 = (int(x, 16) for x in re.search(
        r'^core 0x([0-9a-f]+) 0x([0-9a-f]+)$', p.stdout, re.M).groups())
    assert t1 - t0 == 8 << 20 and c1 - c0 == 2 << 20 and t0 < c0 < c1 < t1
    for args in ([], ['8', '16', '1', '1'], ['8', '2', '1', '-5']):
        assert subprocess.run([str(RANDOM_ACCESS)] + args,
                              capture_output=True, timeout=10).returncode == 2


def test_moving_window_runs_and_rejects_bad_args():
    mw = ROOT / 'workloads' / 'moving_window'
    p = subprocess.run([str(mw), '8', '2', '0.2'], capture_output=True, text=True,
                       timeout=10)
    assert p.returncode == 0, p.stderr
    assert re.search(r'^total 0x[0-9a-f]+ 0x[0-9a-f]+$', p.stdout, re.M)
    for args in ([], ['8', '16', '1'], ['8', '2', '0']):
        assert subprocess.run([str(mw)] + args, capture_output=True,
                              timeout=10).returncode == 2


def test_mixed_runs_and_rejects_bad_args():
    mx = ROOT / 'workloads' / 'mixed'
    p = subprocess.run([str(mx), '8', '0.3', '10'], capture_output=True, text=True,
                       timeout=10)
    assert p.returncode == 0, p.stderr
    assert re.search(r'^zones A 0-\d+ B', p.stdout, re.M)
    for args in ([], ['8', '0', '10'], ['8', '1', '0']):
        assert subprocess.run([str(mx)] + args, capture_output=True,
                              timeout=10).returncode == 2
