"""qmem-replay: the compatibility rules of profile v2 and the stubs
(docs/formats/profile.md). Only that the skeleton behaves as described is
checked, not how close the replay is."""
from test_replay import (MB, PAGE, access, epochs, run_replay, stats_rows, summary,
                         whole, write_profile)


def run(tmp_path, body, *args):
    p = run_replay(write_profile(tmp_path / 'p.txt', body), *args)
    return p


def test_unknown_key_is_ignored(tmp_path):
    head, _ = whole(4 * MB, 0)
    body = head + ['epoch t=0 color=blue'] + \
        [access(0, 0, 4 * MB, 10, extra='future=1')]
    p = run(tmp_path, body)
    assert p.returncode == 0, p.stderr
    assert 'future' not in p.stderr and 'color' not in p.stderr


def test_unknown_line_type_warns_once(tmp_path):
    head, acc = whole(4 * MB, 10)
    body = head + ['sparkle t=0 x=1', 'sparkle t=10 x=2', 'glitter'] + epochs(0, 200, 100, acc)
    p = run(tmp_path, body)
    assert p.returncode == 0, p.stderr
    assert p.stderr.count('sparkle') == 1
    assert p.stderr.count('glitter') == 1


def test_mandatory_unknown_key_is_refused(tmp_path):
    head, _ = whole(4 * MB, 0)
    p = run(tmp_path, head + ['epoch t=0', access(0, 0, 4 * MB, 10, extra='!future=1')])
    assert p.returncode == 2
    assert 'profile:6:' in p.stderr and 'future' in p.stderr


def test_mandatory_known_key_is_fine(tmp_path):
    head, _ = whole(4 * MB, 0)
    body = head + ['epoch !t=0', 'access region=0 off=0 len=%d !rate=10 write=0' % (4 * MB)]
    p = run(tmp_path, body)
    assert p.returncode == 0, p.stderr


def test_counter_is_accepted(tmp_path):
    head, acc = whole(4 * MB, 10)
    body = head + ['counter t=0 name=pmu.loads value=12345'] + epochs(0, 200, 100, acc)
    p = run(tmp_path, body)
    assert p.returncode == 0, p.stderr
    assert 'counter' not in p.stderr


def test_meta_seed(tmp_path):
    head, acc = whole(4 * MB, 20, 0.5)
    body = head + epochs(0, 300, 100, acc)
    a = run_replay(write_profile(tmp_path / 'a.txt', body, seed=7))
    b = run_replay(write_profile(tmp_path / 'b.txt', body, seed=8))
    c = run_replay(write_profile(tmp_path / 'c.txt', body, seed=8), '--seed', '7')
    assert summary(a.stderr)['checksum'] != summary(b.stderr)['checksum']
    assert summary(a.stderr)['checksum'] == summary(c.stderr)['checksum']


def test_other_meta_is_ignored(tmp_path):
    head, acc = whole(4 * MB, 10)
    body = ['meta source=rec1 builder=2 intensity=passthrough'] + head + \
        epochs(0, 200, 100, acc)
    p = run(tmp_path, body)
    assert p.returncode == 0, p.stderr


# ---------------------------------------------------------------- stubs


def test_pattern_random(tmp_path):
    size = 16 * MB
    head, acc = whole(size, 5, extra='pattern=random')
    stats = tmp_path / 's.csv'
    p = run(tmp_path, head + epochs(0, 1000, 100, acc), '--stats', stats)
    assert p.returncode == 0, p.stderr
    s = summary(p.stderr)
    assert int(s['done']) >= 0.99 * int(s['planned']) > 0
    # random pages: some pages are hit twice before others once
    assert int(s['maxrss_kb']) >= 0.9 * size / 1024
    assert 'random' not in p.stderr


def test_pattern_random_differs_from_roundrobin(tmp_path):
    size = 4 * MB
    rr, acc_rr = whole(size, 20, 0.5)
    _, acc_rnd = whole(size, 20, 0.5, extra='pattern=random')
    a = run_replay(write_profile(tmp_path / 'a.txt', rr + epochs(0, 300, 100, acc_rr)))
    b = run_replay(write_profile(tmp_path / 'b.txt', rr + epochs(0, 300, 100, acc_rnd)))
    assert summary(a.stderr)['checksum'] != summary(b.stderr)['checksum']


def test_pattern_poisson_is_a_stub(tmp_path):
    head, acc = whole(4 * MB, 10, extra='pattern=poisson')
    p = run(tmp_path, head + epochs(0, 500, 100, acc))
    assert p.returncode == 0, p.stderr
    assert p.stderr.count('poisson') == 1          # one warning, not one per line
    s = summary(p.stderr)
    assert int(s['done']) >= 0.99 * int(s['planned']) > 0


def test_file_regions_are_refused(tmp_path):
    body = ['region id=0 size=%d kind=file file=/usr/lib/libc.so.6' % MB]
    p = run(tmp_path, body)
    assert p.returncode == 2
    assert 'profile:3:' in p.stderr and 'file regions are not supported yet' in p.stderr
