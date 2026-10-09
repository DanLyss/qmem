import re
import subprocess

from conftest import HOT_REGION


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
    for args in ([], ['8', '16', '1'], ['0', '0', '1']):
        p = subprocess.run([str(HOT_REGION)] + args, capture_output=True,
                           timeout=10)
        assert p.returncode == 2
