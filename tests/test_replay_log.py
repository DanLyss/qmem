"""qmem-replay --log: the step log (docs/formats/replay_log.md), its lines and
levels. What the log says about the algorithm is checked in
test_replay_algorithm.py."""
from test_replay import (MB, PAGE, access, epochs, ev, region, run_replay, summary,
                         whole, write_profile)


def parse_log(path):
    """[(type, {key: value})] in file order; numbers stay strings."""
    out = []
    for line in path.read_text().splitlines():
        f = line.split()
        if f:
            out.append((f[0], dict(x.split('=', 1) for x in f[1:])))
    return out


def lines(log, type_, **match):
    return [kv for t, kv in log if t == type_ and all(kv.get(k) == str(v) for k, v in match.items())]


def run_logged(tmp_path, body, level=None, *args, epoch_ms=100):
    log = tmp_path / 'run.log'
    extra = ['--log', log] + (['--log-level', level] if level else [])
    p = run_replay(write_profile(tmp_path / 'p.txt', body, epoch_ms=epoch_ms),
                   *extra, *args)
    assert p.returncode == 0, p.stderr
    return parse_log(log), p


def small(rate=10, size=MB, t1=200):
    head, acc = whole(size, rate)
    return head + epochs(0, t1, 100, acc)


def test_no_log_without_option(tmp_path):
    p = run_replay(write_profile(tmp_path / 'p.txt', small()))
    assert p.returncode == 0, p.stderr
    assert not (tmp_path / 'run.log').exists()


def test_bad_level(tmp_path):
    prof = write_profile(tmp_path / 'p.txt', small())
    assert run_replay(prof, '--log', tmp_path / 'l', '--log-level', 'loud').returncode == 2
    assert run_replay(prof, '--log-level', 'events').returncode == 2      # level without --log


def test_events_level(tmp_path):
    log, p = run_logged(tmp_path, small())
    kinds = [t for t, _ in log]
    assert kinds[0] == 'start' and kinds[-1] == 'end'
    assert 'tick' not in kinds and 'touch' not in kinds
    start = log[0][1]
    assert start == {'regions': '1', 'events': '1', 'epochs': '2',
                     'end_ms': '200', 'level': 'events'}
    reg = lines(log, 'region')
    assert len(reg) == 1 and reg[0]['id'] == '0' and reg[0]['pages'] == str(MB // PAGE)
    assert int(reg[0]['base'], 16) % PAGE == 0
    assert [e['kind'] for e in lines(log, 'event')] == ['map']
    assert len(lines(log, 'epoch', what='start')) == 2
    assert len(lines(log, 'epoch', what='end')) == 2


def test_end_matches_summary(tmp_path):
    log, p = run_logged(tmp_path, small())
    end, s = log[-1][1], summary(p.stderr)
    for k in ('planned', 'done', 'skipped', 'populated', 'lagged_ticks'):
        assert end[k] == s[k], k


def test_ticks_level(tmp_path):
    log, _ = run_logged(tmp_path, small(), 'ticks')
    ticks = lines(log, 'tick')
    assert ticks and 'touch' not in [t for t, _ in log]
    for k in ('tick', 'epoch', 'access', 'n', 'done', 'carry'):
        assert k in ticks[0]


def test_touches_level(tmp_path):
    log, p = run_logged(tmp_path, small(), 'touches')
    touches = lines(log, 'touch')
    assert len(touches) == int(summary(p.stderr)['done'])
    assert {t['op'] for t in touches} <= {'w', 'r', 'skip'}
    ts = [int(t['t_ns']) for t in touches]
    assert ts == sorted(ts) and 0 <= ts[0] < ts[-1] < 1e9


def test_order_is_chronological(tmp_path):
    body = [region(0, MB), ev('map', 0, 0, 0, MB), ev('unmap', 150, 0, 0, MB)] + \
        epochs(0, 200, 100, [access(0, 0, MB, 10)])
    log, _ = run_logged(tmp_path, body)
    seq = [(t, kv.get('kind') or kv.get('what')) for t, kv in log if t in ('event', 'epoch')]
    assert seq == [('event', 'map'), ('epoch', 'start'), ('epoch', 'end'), ('epoch', 'start'),
                   ('event', 'unmap'), ('epoch', 'end')]
