"""Ground truth of page accesses: the test programs and replay log exactly
which pages they touch (common/qmem_gt.h), verify compares the pages."""
import os
import re
import struct
import subprocess
import sys

import pytest

from conftest import HOT_REGION, REPLAY, ROOT

sys.path.insert(0, str(ROOT / 'legacy' / 'verify'))
from gt import compare, gt_metrics, page_sets, read_gt  # noqa: E402

MB = 1 << 20
PAGE = 4096


def write_gt(path, window_ms, start_boot_ns, ranges, records):
    out = b'QGT1' + struct.pack('<IQII', window_ms, start_boot_ns, len(ranges), 0)
    for start, npages in ranges:
        out += struct.pack('<QQ', start, npages)
    for w, r, touches, writes, touched, written in records:
        size = (ranges[r][1] + 7) // 8
        out += struct.pack('<IIQQ', w, r, touches, writes)
        out += touched.to_bytes(size, 'little') + written.to_bytes(size, 'little')
    path.write_bytes(out)
    return path


def bits(first, n):
    return ((1 << n) - 1) << first


def test_workload_logs_exact_pages(tmp_path):
    gt = tmp_path / 'gt.bin'
    p = subprocess.run([str(HOT_REGION), '8', '2', '0.5'], capture_output=True,
                       text=True, timeout=10, env=dict(os.environ, QMEM_GT=str(gt)))
    assert p.returncode == 0, p.stderr
    g = read_gt(gt)
    total0 = int(re.search(r'^total 0x([0-9a-f]+)', p.stdout, re.M)[1], 16)
    assert g['window_ms'] == 100 and g['ranges'] == [(total0, 2048)]
    windows = sorted(r[0] for r in g['records'])
    assert len(windows) >= 4
    # a window in the middle: exactly the 2 MB hot range, written many times
    w, _, touches, writes, touched, written = g['records'][len(g['records']) // 2]
    assert touched == bits(0, 512) and written == touched
    assert touches == writes and touches >= 512


def test_replay_logs_exact_pages(tmp_path):
    prof = tmp_path / 'p.txt'
    lines = ['qmem-profile 1', 'epoch_ms 100', 'region 0 %d' % (4 * MB),
             'map 0 0 0 %d' % (4 * MB), 'populate 0 0 0 %d' % (4 * MB)]
    for t in range(0, 500, 100):
        lines += ['epoch %d' % t, 'access 0 0 %d %d 0' % (MB, 256 * 20)]
    prof.write_text('\n'.join(lines) + '\n')
    gt = tmp_path / 'gt.bin'
    p = subprocess.run([str(REPLAY), str(prof)], capture_output=True, text=True,
                       timeout=20, env=dict(os.environ, QMEM_GT=str(gt)))
    assert p.returncode == 0, p.stderr
    g = read_gt(gt)
    assert g['ranges'][0][1] == 1024
    by_window = {r[0]: r for r in g['records']}
    # window 0: populate wrote all 1024 pages
    assert by_window[0][5] == bits(0, 1024)
    # later windows: exactly the first 1 MB (256 pages), read only
    w2 = by_window[2]
    assert w2[4] == bits(0, 256) and w2[5] == 0


def test_metrics(tmp_path):
    A, B = 0x7f0000000000, 0x7e0000000000
    regions_o = {0: (A, 4 * MB)}
    regions_r = {0: (B, 4 * MB)}
    o = write_gt(tmp_path / 'o.bin', 100, 0, [(A, 1024)],
                 [(w, 0, 1000, 0, bits(0, 100), 0) for w in range(10)])
    same = write_gt(tmp_path / 's.bin', 100, 0, [(B, 1024)],
                    [(w, 0, 10, 0, bits(0, 100), 0) for w in range(10)])
    half = write_gt(tmp_path / 'h.bin', 100, 0, [(B, 1024)],
                    [(w, 0, 10, 0, bits(50, 100), 0) for w in range(10)])
    m = gt_metrics(o, same, regions_o, regions_r, 0, windows=(100,))
    t = m['windows'][0]['touched']
    assert t['recall'] == t['precision'] == t['jaccard'] == 1
    assert m['touches']['ratio'] == pytest.approx(0.01)
    t = gt_metrics(o, half, regions_o, regions_r, 0, windows=(100,))['windows'][0]['touched']
    assert t['recall'] == pytest.approx(0.5) and t['precision'] == pytest.approx(0.5)
    assert t['jaccard'] == pytest.approx(50 / 150)


def test_time_shift_and_longer_windows(tmp_path):
    """The original's time 0 is 300 ms into the profile: its window 0 lines up
    with the replay's window 3. Over 1 s windows the sets are merged."""
    A, B = 0x7f0000000000, 0x7e0000000000
    o = write_gt(tmp_path / 'o.bin', 100, 0, [(A, 64)],
                 [(0, 0, 1, 0, bits(0, 8), 0)])
    r = write_gt(tmp_path / 'r.bin', 100, 0, [(B, 64)],
                 [(3, 0, 1, 0, bits(0, 8), 0), (5, 0, 1, 0, bits(8, 8), 0)])
    m = gt_metrics(o, r, {0: (A, 64 * PAGE)}, {0: (B, 64 * PAGE)}, 300,
                   windows=(100, 1000))
    w100, w1000 = m['windows']
    assert w100['touched']['recall'] == 1 and w100['touched']['precision'] == 0.5
    assert w1000['touched']['recall'] == 1 and w1000['touched']['precision'] == 0.5
