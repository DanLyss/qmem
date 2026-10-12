"""qmem-replay: P1 core, P2 timing and stats, P3 write ratio."""
import re
import subprocess
import time

import pytest

from conftest import REPLAY, SOFTDIRTY

PAGE = 4096
MB = 1 << 20


def write_profile(path, body, epoch_ms=100, seed=None):
    lines = ['qmem-profile 1', 'epoch_ms %d' % epoch_ms]
    if seed is not None:
        lines.append('seed %d' % seed)
    path.write_text('\n'.join(lines + body) + '\n')
    return path


def epochs(t0, t1, step, accesses):
    """Lines for epochs t0, t0+step, ... < t1, each with the same accesses."""
    out = []
    for t in range(t0, t1, step):
        out.append('epoch %d' % t)
        out.extend('access ' + a for a in accesses)
    return out


def run_replay(*args, timeout=60):
    return subprocess.run([str(REPLAY)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=timeout)


def summary(stderr):
    line = [l for l in stderr.splitlines() if l.startswith('replay:')][-1]
    return dict(re.findall(r'(\w+)=(\S+)', line))


def rss_kb(pid):
    with open('/proc/%d/status' % pid) as f:
        return int(re.search(r'^VmRSS:\s+(\d+)', f.read(), re.M)[1])


# ---------------------------------------------------------------- P1


def test_touches_whole_region(tmp_path):
    size = 64 * MB
    pages = size // PAGE
    # 5 touches per page on average over 2 s: more than 99 % of pages hit
    rate = 5 * pages // 2
    prof = write_profile(tmp_path / 'p.txt', ['region 0 %d' % size,
                                              'map 0 0 0 %d' % size]
                         + epochs(0, 2000, 100, ['0 0 %d %d 1.0' % (size, rate)]))
    p = run_replay(prof)
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert int(s['epochs']) == 20
    assert int(s['maxrss_kb']) >= 0.95 * size / 1024
    assert int(s['minflt']) >= 0.95 * pages
    assert int(s['done']) >= 0.99 * int(s['planned'])
    assert int(s['skipped']) == 0


def test_same_seed_same_sequence(tmp_path):
    size = 4 * MB
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 300, 100, ['0 0 %d 20000 0.5' % size])
    prof = write_profile(tmp_path / 'p.txt', body, seed=7)
    a, b = run_replay(prof), run_replay(prof)
    c = run_replay(prof, '--seed', '8')
    assert a.returncode == b.returncode == c.returncode == 0
    assert summary(a.stderr)['checksum'] == summary(b.stderr)['checksum']
    assert summary(a.stderr)['checksum'] != summary(c.stderr)['checksum']


def test_unmap_frees_memory(tmp_path):
    size = 64 * MB
    pages = size // PAGE
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size,
            'unmap 1500 0 0 %d' % (size // 2)] + \
        epochs(0, 2500, 100, ['0 0 %d %d 1.0' % (size, 10 * pages)])
    prof = write_profile(tmp_path / 'p.txt', body)
    proc = subprocess.Popen([str(REPLAY), str(prof)], stderr=subprocess.PIPE,
                            text=True)
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
    pages = size // PAGE
    half = size // 2
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % half,
            'map 1000 0 %d %d' % (half, half)] + \
        epochs(0, 2000, 100, ['0 0 %d %d 1.0' % (size, 10 * pages)])
    p = run_replay(write_profile(tmp_path / 'p.txt', body))
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert int(s['skipped']) > 0              # second half before 1000 ms
    assert int(s['maxrss_kb']) >= 0.9 * size / 1024


@pytest.mark.parametrize('body,line', [
    (['region 0 4096'], 1),                                   # no header
    (['qmem-profile 2', 'epoch_ms 100'], 1),                  # bad version
    (['qmem-profile 1', 'epoch_ms 0'], 2),
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 x'], 3),
    (['qmem-profile 1', 'epoch_ms 100', 'region 1 4096'], 3),  # ids from 0
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096',
      'access 0 0 4096 10 1.0'], 4),                          # access before epoch
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'epoch 0',
      'access 1 0 4096 10 1.0'], 5),                          # unknown region
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'epoch 0',
      'access 0 0 8192 10 1.0'], 5),                          # past the end
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'epoch 0',
      'access 0 0 4096 10 1.5'], 5),                          # write ratio > 1
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'epoch 100',
      'epoch 0'], 5),                                         # time goes back
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'map 100 0 0 4096',
      'map 0 0 0 4096'], 5),
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'map 0 0 1 4095'], 4),
    (['qmem-profile 1', 'epoch_ms 100', 'region 0 4096', 'bogus 1'], 4),
])
def test_rejects_bad_profile(tmp_path, body, line):
    prof = tmp_path / 'p.txt'
    prof.write_text('\n'.join(body) + '\n')
    p = run_replay(prof)
    assert p.returncode == 2
    assert 'profile:%d:' % line in p.stderr


def test_comments_and_blank_lines(tmp_path):
    prof = tmp_path / 'p.txt'
    prof.write_text('qmem-profile 1   # header\n\nepoch_ms 100\n'
                    'region 0 4096\nmap 0 0 0 4096\n# nothing\n'
                    'epoch 0\naccess 0 0 4096 100 1.0  # one page\n')
    p = run_replay(prof)
    assert p.returncode == 0, p.stderr


def test_usage(tmp_path):
    assert run_replay().returncode == 2
    assert run_replay(tmp_path / 'missing.txt').returncode == 1


# ---------------------------------------------------------------- P2


def test_stats_and_timing(tmp_path):
    size = 16 * MB
    pages = size // PAGE
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 4000, 100, ['0 0 %d %d 1.0' % (size, 2 * pages)])
    stats = tmp_path / 'stats.csv'
    started = time.monotonic()
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--stats', stats)
    wall = time.monotonic() - started
    assert p.returncode == 0, p.stderr
    assert 3.9 <= wall <= 4.6
    lines = stats.read_text().splitlines()
    assert lines[0] == 't_ms,planned,done,lag_ms'
    rows = [[int(x) for x in l.split(',')] for l in lines[1:]]
    assert [r[0] for r in rows] == list(range(0, 4000, 100))
    planned, done = sum(r[1] for r in rows), sum(r[2] for r in rows)
    assert planned == pytest.approx(2 * pages * 4, rel=0.01)
    assert done >= 0.99 * planned
    assert max(r[3] for r in rows) < 50


def test_overload_does_not_drift(tmp_path):
    size = 64 * MB
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 1000, 100, ['0 0 %d 2000000000 1.0' % size])
    started = time.monotonic()
    p = run_replay(write_profile(tmp_path / 'p.txt', body))
    wall = time.monotonic() - started
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert wall < 1.6                       # missed work is dropped, not queued
    assert int(s['done']) < int(s['planned'])
    assert int(s['lagged_ticks']) > 0


# ---------------------------------------------------------------- P3


def test_write_ratio_matches_softdirty(tmp_path):
    size = 32 * MB
    pages = size // PAGE
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 500, 100, ['0 0 %d %d 1.0' % (size, 20 * pages)]) + \
        epochs(500, 4500, 100, ['0 0 %d %d 0.25' % (size, 10 * pages)])
    proc = subprocess.Popen([str(REPLAY), str(write_profile(tmp_path / 'p.txt', body))],
                            stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(0.8)
        sd = subprocess.run([str(SOFTDIRTY), str(proc.pid), '1000',
                             str(int(time.time() * 1000))],
                            capture_output=True, text=True, timeout=30)
        assert sd.returncode == 0, sd.stderr
    finally:
        _, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    rows = [[int(x) for x in l.split(',')] for l in sd.stdout.splitlines()]
    big = [r for r in rows if r[3] >= 0.9 * pages]
    assert len(big) >= 2, rows
    for r in big:
        assert r[4] / r[3] == pytest.approx(0.25, abs=0.05)


# ---------------------------------------------------------------- populate


def test_populate_makes_memory_resident(tmp_path):
    size = 32 * MB
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % (size // 2),
            'populate 100 0 0 %d' % size, 'unmap 600 0 0 %d' % (size // 2)]
    p = run_replay(write_profile(tmp_path / 'p.txt', body))
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    # only the mapped half is populated, without accesses
    assert int(s['populated']) == size // 2 // PAGE
    assert int(s['maxrss_kb']) >= 0.95 * size / 2 / 1024
    assert int(s['maxrss_kb']) < 0.75 * size / 1024
    assert int(s['planned']) == 0


def test_populate_is_validated(tmp_path):
    prof = tmp_path / 'p.txt'
    prof.write_text('qmem-profile 1\nepoch_ms 100\nregion 0 4096\n'
                    'populate 0 0 0 8192\n')
    p = run_replay(prof)
    assert p.returncode == 2 and 'profile:4:' in p.stderr


def test_every_page_hit_at_the_planned_rate(tmp_path):
    """rate = pages per second must touch every page once a second (a random
    choice would miss about 1/e of them), so DAMON sees the planned rate."""
    size = 32 * MB
    pages = size // PAGE
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 4000, 100, ['0 0 %d %d 1.0' % (size, pages)])
    proc = subprocess.Popen([str(REPLAY), str(write_profile(tmp_path / 'p.txt', body))],
                            stderr=subprocess.PIPE, text=True)
    try:
        time.sleep(1.2)
        sd = subprocess.run([str(SOFTDIRTY), str(proc.pid), '1000',
                             str(int(time.time() * 1000))],
                            capture_output=True, text=True, timeout=30)
    finally:
        _, err = proc.communicate(timeout=30)
    assert proc.returncode == 0, err
    rows = [[int(x) for x in l.split(',')] for l in sd.stdout.splitlines()]
    big = [r for r in rows if r[3] >= 0.9 * pages]
    assert len(big) >= 2, rows
    for r in big:
        assert r[4] / r[3] >= 0.97, r
