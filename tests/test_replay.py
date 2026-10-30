"""qmem-replay: core, timing and stats, populate,
region addresses. The compatibility rules and the stubs are in test_replay_v2.py.
"""
import re
import subprocess
import time

import pytest

from conftest import REPLAY

PAGE = 4096
MB = 1 << 20


def write_profile(path, body, epoch_ms=100):
    meta = 'meta epoch_ms=%d' % epoch_ms
    path.write_text('\n'.join(['qmem-profile 2', meta] + body) + '\n')
    return path


def region(i, size, extra=''):
    return ('region id=%d size=%d %s' % (i, size, extra)).rstrip()


def ev(kind, t, r, off, length):
    return '%s t=%d region=%d off=%d len=%d' % (kind, t, r, off, length)


def access(r, off, length, rate, write=1.0, extra=''):
    """rate: touches per page per second."""
    return ('access region=%d off=%d len=%d rate=%s write=%s %s' % (
        r, off, length, rate, write, extra)).rstrip()


def epochs(t0, t1, step, accesses, dur=None):
    """Lines for epochs t0, t0+step, ... < t1, each with the same accesses."""
    out = []
    for t in range(t0, t1, step):
        out.append('epoch t=%d' % t + ('' if dur is None else ' dur=%d' % dur))
        out.extend(accesses)
    return out


def run_replay(*args, timeout=60):
    return subprocess.run([str(REPLAY)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=timeout)


def summary(stderr):
    line = [l for l in stderr.splitlines() if l.startswith('replay:')][-1]
    return dict(re.findall(r'(\w+)=(\S+)', line))


def stats_rows(path):
    return [[int(x) for x in l.split(',')] for l in path.read_text().splitlines()[1:]]


def rss_kb(pid):
    with open('/proc/%d/status' % pid) as f:
        return int(re.search(r'^VmRSS:\s+(\d+)', f.read(), re.M)[1])


def whole(size, rate, write=1.0, extra=''):
    return [region(0, size), ev('map', 0, 0, 0, size)], [access(0, 0, size, rate, write, extra)]


# ---------------------------------------------------------------- core


def test_touches_whole_region(tmp_path):
    size = 64 * MB
    pages = size // PAGE
    # 5 touches per page over 2 s: more than 99 % of pages hit
    head, acc = whole(size, 2.5)
    p = run_replay(write_profile(tmp_path / 'p.txt', head + epochs(0, 2000, 100, acc)))
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert int(s['epochs']) == 20
    assert int(s['maxrss_kb']) >= 0.95 * size / 1024
    assert int(s['minflt']) >= 0.95 * pages
    assert int(s['done']) >= 0.99 * int(s['planned'])
    assert int(s['skipped']) == 0


def test_rate_is_per_page(tmp_path):
    """planned touches of an epoch = rate * pages * dur."""
    size = 4 * MB
    pages = size // PAGE
    head, acc = whole(size, 2)
    stats = tmp_path / 's.csv'
    p = run_replay(write_profile(tmp_path / 'p.txt', head + epochs(0, 1000, 100, acc)),
                   '--stats', stats)
    assert p.returncode == 0, p.stderr
    assert all(r[1] == 2 * pages // 10 for r in stats_rows(stats))


def test_unmap_frees_memory(tmp_path):
    size = 64 * MB
    head, acc = whole(size, 10)
    body = head + [ev('unmap', 1500, 0, 0, size // 2)] + epochs(0, 2500, 100, acc)
    proc = subprocess.Popen([str(REPLAY), str(write_profile(tmp_path / 'p.txt', body))],
                            stderr=subprocess.PIPE, text=True)
    time.sleep(1.2)
    before = rss_kb(proc.pid)
    time.sleep(1.0)
    after = rss_kb(proc.pid)
    _, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    assert before >= 0.9 * size / 1024
    assert 0.35 * before <= after <= 0.65 * before
    assert int(summary(err)['skipped']) > 0   # touches of the unmapped half


def test_partial_map(tmp_path):
    size = 32 * MB
    half = size // 2
    body = [region(0, size), ev('map', 0, 0, 0, half), ev('map', 1000, 0, half, half)] + \
        epochs(0, 2000, 100, [access(0, 0, size, 10)])
    p = run_replay(write_profile(tmp_path / 'p.txt', body))
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert int(s['skipped']) > 0              # second half before 1000 ms
    assert int(s['maxrss_kb']) >= 0.9 * size / 1024


H = ['qmem-profile 2', 'meta epoch_ms=100']
R = H + ['region id=0 size=4096']
E = R + ['epoch t=0']


@pytest.mark.parametrize('body,line', [
    (['region id=0 size=4096'], 1),                            # no header
    (['qmem-profile 3'], 1),                                   # unknown version
    (['qmem-profile 2', 'meta epoch_ms=0'], 2),
    (H + ['region id=0 size=x'], 3),
    (H + ['region id=1 size=4096'], 3),                        # ids from 0
    (H + ['region id=0'], 3),                                  # no size
    (H + ['region id=0 size=4096 kind=bogus'], 3),
    (H + ['region id=0 size=4096 base=zz'], 3),
    (R + ['access region=0 off=0 len=4096 rate=10'], 4),       # access before epoch
    (E + ['access region=1 off=0 len=4096 rate=10'], 5),       # unknown region
    (E + ['access region=0 off=0 len=8192 rate=10'], 5),       # past the end
    (E + ['access region=0 off=0 len=4096 rate=10 write=1.5'], 5),
    (E + ['access region=0 off=0 len=4096'], 5),               # no rate
    (E + ['access region=0 off=0 len=4096 rate=10 pattern=bogus'], 5),
    (R + ['epoch t=100', 'epoch t=0'], 5),                     # time goes back
    (R + ['epoch t=0 dur=0'], 4),
    (R + ['map t=100 region=0 off=0 len=4096', 'map t=0 region=0 off=0 len=4096'], 5),
    (R + ['map t=0 region=0 off=1 len=4095'], 4),              # not page aligned
    (R + ['populate t=0 region=0 off=0 len=8192'], 4),
])
def test_rejects_bad_profile(tmp_path, body, line):
    prof = tmp_path / 'p.txt'
    prof.write_text('\n'.join(body) + '\n')
    p = run_replay(prof)
    assert p.returncode == 2, p.stderr
    assert 'profile:%d:' % line in p.stderr


def test_comments_and_blank_lines(tmp_path):
    prof = tmp_path / 'p.txt'
    prof.write_text('qmem-profile 2   # header\n\nmeta epoch_ms=100\n'
                    'region id=0 size=4096\nmap t=0 region=0 off=0 len=4096\n# nothing\n'
                    'epoch t=0\naccess region=0 off=0 len=4096 rate=100  # one page\n')
    p = run_replay(prof)
    assert p.returncode == 0, p.stderr


def test_defaults(tmp_path):
    """No meta at all: epoch_ms 100; write 0; pattern roundrobin."""
    prof = tmp_path / 'p.txt'
    prof.write_text('qmem-profile 2\nregion id=0 size=4096\nmap t=0 region=0 off=0 len=4096\n'
                    'epoch t=0\naccess region=0 off=0 len=4096 rate=100\n')
    stats = tmp_path / 's.csv'
    p = run_replay(prof, '--stats', stats)
    assert p.returncode == 0, p.stderr
    assert stats_rows(stats)[0][1] == 10


def test_usage(tmp_path):
    assert run_replay().returncode == 2
    assert run_replay(tmp_path / 'missing.txt').returncode == 1


# ---------------------------------------------------------------- timing and stats


def test_stats_and_timing(tmp_path):
    size = 16 * MB
    pages = size // PAGE
    head, acc = whole(size, 2)
    stats = tmp_path / 'stats.csv'
    started = time.monotonic()
    p = run_replay(write_profile(tmp_path / 'p.txt', head + epochs(0, 4000, 100, acc)),
                   '--stats', stats)
    wall = time.monotonic() - started
    assert p.returncode == 0, p.stderr
    assert 3.9 <= wall <= 4.6
    assert stats.read_text().splitlines()[0] == 't_ms,planned,done,lag_ms'
    rows = stats_rows(stats)
    assert [r[0] for r in rows] == list(range(0, 4000, 100))
    planned, done = sum(r[1] for r in rows), sum(r[2] for r in rows)
    assert planned == pytest.approx(2 * pages * 4, rel=0.01)
    assert done >= 0.99 * planned
    assert max(r[3] for r in rows) < 50


def test_epoch_dur(tmp_path):
    """An epoch lasts dur ms: half the touches with dur=50, then a pause."""
    size = 4 * MB
    pages = size // PAGE
    head, acc = whole(size, 10)
    body = head + ['epoch t=0 dur=50'] + acc + ['epoch t=100'] + acc
    stats = tmp_path / 's.csv'
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--stats', stats)
    assert p.returncode == 0, p.stderr
    rows = stats_rows(stats)
    assert rows[0][1] == 10 * pages // 20 and rows[1][1] == 10 * pages // 10
    assert rows[0][2] == pytest.approx(rows[0][1], rel=0.02)


def test_overload_does_not_drift(tmp_path):
    size = 64 * MB
    head, acc = whole(size, 200000)
    started = time.monotonic()
    p = run_replay(write_profile(tmp_path / 'p.txt', head + epochs(0, 1000, 100, acc)))
    wall = time.monotonic() - started
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert wall < 1.6                       # missed work is dropped, not queued
    assert int(s['done']) < int(s['planned'])
    assert int(s['lagged_ticks']) > 0


def test_lag_counts_a_tick_that_overran(tmp_path):
    """A populate keeps tick 50 busy for many ticks; the ticks after the drop
    start on time, so only the overrun itself can show the lag."""
    big = 128 * MB
    body = [region(0, big), region(1, 2 * PAGE), ev('map', 0, 0, 0, big),
            ev('map', 0, 1, 0, 2 * PAGE), ev('populate', 50, 0, 0, big),
            'epoch t=0 dur=500', access(1, 0, 2 * PAGE, 5000000)]
    stats, log = tmp_path / 's.csv', tmp_path / 'log.txt'
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--stats', stats, '--log', log)
    assert p.returncode == 0, p.stderr
    # the longest drop; lagged_ticks would also count the small ones after it
    skipped = max(int(m) for m in re.findall(r'^drop .* skipped=(\d+)$', log.read_text(), re.M))
    assert skipped > 10                     # the populate did overrun
    assert float(summary(p.stderr)['max_lag_ms']) >= skipped
    assert stats_rows(stats)[0][3] >= skipped


# ---------------------------------------------------------------- populate


def test_populate_makes_memory_resident(tmp_path):
    size = 32 * MB
    body = [region(0, size), ev('map', 0, 0, 0, size // 2), ev('populate', 100, 0, 0, size),
            ev('unmap', 600, 0, 0, size // 2)]
    p = run_replay(write_profile(tmp_path / 'p.txt', body))
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    # only the mapped half is populated, without accesses
    assert int(s['populated']) == size // 2 // PAGE
    assert int(s['maxrss_kb']) >= 0.95 * size / 2 / 1024
    assert int(s['maxrss_kb']) < 0.75 * size / 1024
    assert int(s['planned']) == 0


# ---------------------------------------------------------------- addresses


def log_bases(path):
    return [int(re.search(r'base=0x([0-9a-f]+)', l)[1], 16)
            for l in path.read_text().splitlines() if l.startswith('region ')]


FREE = 0x200000000000           # far from the heap, the libraries and the stack


def test_region_goes_to_its_base(tmp_path):
    body = [region(0, 4 * MB, 'base=0x%x' % FREE), region(1, 2 * MB),
            ev('map', 0, 0, 0, 4 * MB), ev('map', 0, 1, 0, 2 * MB)] + \
        epochs(0, 200, 100, [access(0, 0, 4 * MB, 1)])
    log = tmp_path / 'log.txt'
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--log', log)
    assert p.returncode == 0, p.stderr
    assert 'reserved elsewhere' not in p.stderr
    assert log_bases(log)[0] == FREE


def test_taken_base_warns_and_goes_anywhere(tmp_path):
    body = [region(0, 4 * MB, 'base=0x%x' % FREE), region(1, 2 * MB, 'base=0x%x' % FREE),
            ev('map', 0, 0, 0, 4 * MB), ev('map', 0, 1, 0, 2 * MB)] + \
        epochs(0, 200, 100, [access(1, 0, 2 * MB, 1)])
    log = tmp_path / 'log.txt'
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--log', log)
    assert p.returncode == 0, p.stderr
    assert p.stderr.count('reserved elsewhere') == 1
    bases = log_bases(log)
    assert bases[0] == FREE and bases[1] != FREE and bases[1] % PAGE == 0
    s = summary(p.stderr)
    assert int(s['done']) == int(s['planned']) > 0      # the moved region still works


def test_regions_stay_separate_mappings(tmp_path):
    """Regions are separate mappings in the original; adjacent reservations
    must not let the kernel merge them into one VMA."""
    body = [region(i, 8 * MB) for i in range(3)] + \
        [ev('map', 0, i, 0, 8 * MB) for i in range(3)] + \
        [ev('populate', 0, i, 0, 8 * MB) for i in range(3)] + \
        epochs(0, 1000, 100, [access(i, 0, 8 * MB, 1) for i in range(3)])
    proc = subprocess.Popen([str(REPLAY), str(write_profile(tmp_path / 'p.txt', body))],
                            stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(0.5)
        maps = open('/proc/%d/maps' % proc.pid).read().splitlines()
    finally:
        proc.communicate(timeout=30)
    rw = [l for l in maps if ' rw-p ' in l and l.split()[-1] == '0'
          or (' rw-p ' in l and len(l.split()) == 5)]
    sizes = sorted(int(l.split()[0].split('-')[1], 16) - int(l.split()[0].split('-')[0], 16)
                   for l in rw)
    assert sizes.count(8 * MB) == 3, sizes
