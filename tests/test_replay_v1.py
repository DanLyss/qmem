"""qmem-replay still reads profile v1 (docs/formats/profile.md): positional
fields, rate per range."""
import re
import subprocess

import pytest

from conftest import REPLAY

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


# ---------------------------------------------------------------- v1


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


@pytest.mark.parametrize('body,line', [
    (['region 0 4096'], 1),                                   # no header
    (['qmem-profile 3', 'epoch_ms 100'], 1),                  # bad version
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


def test_rate_is_per_range(tmp_path):
    """In v1 rate is touches of the whole range per second."""
    size = 4 * MB
    body = ['region 0 %d' % size, 'map 0 0 0 %d' % size] + \
        epochs(0, 1000, 100, ['0 0 %d 8000 1.0' % size])
    stats = tmp_path / 's.csv'
    p = run_replay(write_profile(tmp_path / 'p.txt', body), '--stats', stats)
    assert p.returncode == 0, p.stderr
    rows = [l.split(',') for l in stats.read_text().splitlines()[1:]]
    assert all(int(r[1]) == 800 for r in rows), rows
