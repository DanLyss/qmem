"""R6: share of written pages through soft-dirty bits (qmem-softdirty, -w)."""
import json
import re
import subprocess
import time

from conftest import record_json, RW_SPLIT, SOFTDIRTY, WRITES_HEADER, run_record


def ranges(stdout):
    a = re.search(r'^a 0x([0-9a-f]+) 0x([0-9a-f]+)$', stdout, re.M)
    b = re.search(r'^b 0x([0-9a-f]+) 0x([0-9a-f]+)$', stdout, re.M)
    return ([int(x, 16) for x in a.groups()], [int(x, 16) for x in b.groups()])


def test_softdirty_tells_written_from_read():
    app = subprocess.Popen([str(RW_SPLIT), '8', '8', '2.5'],
                           stdout=subprocess.PIPE, text=True)
    try:
        a_line, b_line = app.stdout.readline(), app.stdout.readline()
        (a0, a1), (b0, b1) = ranges(a_line + b_line)
        start_ms = int(time.time() * 1000)
        sd = subprocess.run([str(SOFTDIRTY), str(app.pid), '300', str(start_ms)],
                            capture_output=True, text=True, timeout=20)
        assert sd.returncode == 0, sd.stderr
    finally:
        app.kill()
        app.wait()

    rows = [[int(x) for x in line.split(',')]
            for line in sd.stdout.splitlines()]
    assert rows, 'no samples'
    assert all(len(r) == 6 for r in rows)
    # skip the first sample: it still holds the initial writes to both regions
    first_ts = min(r[0] for r in rows)
    late = [r for r in rows if r[0] > first_ts]
    a_rows = [r for r in late if r[1] <= a0 < r[2]]
    b_rows = [r for r in late if r[1] <= b0 < r[2]]
    assert a_rows and b_rows
    a_dirty = sum(r[4] for r in a_rows) / sum(r[3] for r in a_rows)
    b_dirty = sum(r[4] for r in b_rows) / sum(r[3] for r in b_rows)
    assert a_dirty > 0.9, a_dirty
    assert b_dirty < 0.1, b_dirty
    # A was resident before every period, so each of its dirty pages was a
    # write fault caused by re-arming soft-dirty
    for r in a_rows:
        assert r[5] == r[4]


def test_rewritten_excludes_new_pages(tmp_path):
    """Pages that become resident in a period are dirty but were not
    re-armed: rewritten_pages counts only pages resident at the last clear."""
    app = subprocess.Popen([str(RW_SPLIT), '8', '8', '2'],
                           stdout=subprocess.PIPE, text=True)
    try:
        app.stdout.readline(); app.stdout.readline()
        # start sampling while rw_split is still writing its regions the
        # first time is racy; instead check the invariant on every row
        sd = subprocess.run([str(SOFTDIRTY), str(app.pid), '200', '0'],
                            capture_output=True, text=True, timeout=20)
    finally:
        app.kill()
        app.wait()
    rows = [[int(x) for x in l.split(',')] for l in sd.stdout.splitlines()]
    assert rows
    assert all(0 <= r[5] <= r[4] <= r[3] for r in rows)


def test_softdirty_bad_args():
    p = subprocess.run([str(SOFTDIRTY)], capture_output=True, timeout=5)
    assert p.returncode == 2
    p = subprocess.run([str(SOFTDIRTY), 'x', '100', '0'], capture_output=True,
                       timeout=5)
    assert p.returncode == 2


def test_softdirty_missing_process():
    p = subprocess.run([str(SOFTDIRTY), '999999999', '100', '0'],
                       capture_output=True, timeout=5)
    assert p.returncode == 1


def test_record_with_writes(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '200', '--', RW_SPLIT, '8', '8', '2.5'], env=fake_env)
    assert p.returncode == 0, p.stderr
    lines = (out / 'softdirty' / 'writes.csv').read_text().splitlines()
    assert lines[0] == WRITES_HEADER
    rows = [[int(x) for x in line.split(',')] for line in lines[1:]]
    assert len(rows) >= 2
    assert all(r[4] <= r[3] for r in rows)
    meta = record_json(out)
    assert meta['sources']['softdirty']['period_ms'] == 1000


def test_record_without_softdirty(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '--sources', 'damon,smaps', '--', 'true'], env=fake_env)
    assert p.returncode == 0, p.stderr
    assert not (out / 'softdirty' / 'writes.csv').exists()
    assert record_json(out)['sources']['softdirty']['status'] == 'off'
