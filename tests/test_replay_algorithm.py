"""The replay algorithm step by step, checked through its log (--log): events
at their tick, epoch boundaries, how many touches and which pages, the write
share, unmapped pages, dropped work. Nothing here is about how close a
replay is to an original. See docs/replay_testing.md, section 1.2."""
from collections import Counter, defaultdict

from test_replay import MB, PAGE, access, epochs, ev, region, run_replay, write_profile
from test_replay_log import lines, parse_log, run_logged

MS = 1_000_000


def touches(log):
    return [(int(t['t_ns']), int(t['region']), int(t['page']), t['op'])
            for t in lines(log, 'touch')]


def ticks(log):
    return [{k: (float(v) if k == 'carry' else int(v)) for k, v in t.items()}
            for t in lines(log, 'tick')]


# ---------------------------------------------------------------- events


def test_events_at_their_tick_in_order(tmp_path):
    body = [region(0, MB), region(1, MB),
            ev('map', 0, 0, 0, MB), ev('map', 0, 1, 0, MB // 2),
            ev('unmap', 137, 0, 0, MB // 2), ev('map', 250, 0, 0, MB // 2),
            ev('unmap', 400, 1, 0, MB // 2)]
    log, _ = run_logged(tmp_path, body)
    got = [(e['kind'], int(e['t']), int(e['tick']), int(e['region'])) for e in lines(log, 'event')]
    assert got == [('map', 0, 0, 0), ('map', 0, 0, 1), ('unmap', 137, 137, 0),
                   ('map', 250, 250, 0), ('unmap', 400, 400, 1)]


def test_no_touch_lands_before_its_map(tmp_path):
    pages = 64
    body = [region(0, pages * PAGE), ev('map', 50, 0, 0, pages * PAGE)] + \
        epochs(0, 200, 100, [access(0, 0, pages * PAGE, 50, write=0)])
    log, _ = run_logged(tmp_path, body, 'touches')
    tt = touches(log)
    assert all(op == 'skip' for t, _, _, op in tt if t < 50 * MS)
    mapped = [x for x in tt if x[0] >= 50 * MS]
    assert mapped and all(op != 'skip' for *_, op in mapped)
    # the first touch of every page after its map writes (a read would map
    # the shared zero page)
    first = {}
    for _, _, p, op in mapped:
        first.setdefault(p, op)
    assert set(first.values()) == {'w'}


def test_populate_writes_mapped_untouched_pages_once(tmp_path):
    pages = 256
    size = pages * PAGE
    body = [region(0, size), ev('map', 0, 0, 0, size // 2),
            ev('populate', 10, 0, 0, size),                 # only the mapped half
            ev('populate', 20, 0, 0, size),                 # nothing left to write
            ev('map', 30, 0, size // 2, size // 2),
            ev('populate', 40, 0, 0, size),                 # the newly mapped half
            ev('unmap', 50, 0, 0, size), ev('map', 60, 0, 0, size),
            ev('populate', 70, 0, 0, size)]                 # everything again
    log, _ = run_logged(tmp_path, body)
    pop = [int(e['pages']) for e in lines(log, 'event', kind='populate')]
    assert pop == [pages // 2, 0, pages // 2, pages]


# ---------------------------------------------------------------- epochs


def test_touches_stay_inside_epochs(tmp_path):
    pages = 64
    acc = [access(0, 0, pages * PAGE, 100)]
    body = [region(0, pages * PAGE), ev('map', 0, 0, 0, pages * PAGE)] + \
        ['epoch t=0 dur=50'] + acc + ['epoch t=100 dur=100'] + acc + ['epoch t=300 dur=20'] + acc
    log, _ = run_logged(tmp_path, body, 'touches')
    active = [range(0, 50), range(100, 200), range(300, 320)]
    tk = [t['tick'] for t in ticks(log)]
    assert tk and all(any(x in r for r in active) for x in tk)
    for t, *_ in touches(log):
        ms = t / MS
        assert any(r.start <= ms < r.stop + 2 for r in active), ms
    starts = [(int(e['idx']), int(e['tick'])) for e in lines(log, 'epoch', what='start')]
    ends = [(int(e['idx']), int(e['tick'])) for e in lines(log, 'epoch', what='end')]
    assert starts == [(0, 0), (1, 100), (2, 300)]
    assert ends == [(0, 50), (1, 200), (2, 320)]


def test_touch_count_and_carry(tmp_path):
    """256 pages at 5.859375 touches per page per second: 1500 per second, so
    1.5 touches per 1 ms tick: n alternates 1 and 2 through the carry."""
    pages = 256
    body = [region(0, pages * PAGE), ev('map', 0, 0, 0, pages * PAGE)] + \
        epochs(0, 300, 100, [access(0, 0, pages * PAGE, 5.859375)])
    log, _ = run_logged(tmp_path, body, 'ticks')
    by_epoch = defaultdict(list)
    for t in ticks(log):
        by_epoch[t['epoch']].append(t)
    assert sorted(by_epoch) == [0, 1, 2]
    for e, tt in by_epoch.items():
        assert len(tt) == 100                         # every tick has work
        assert sum(t['n'] for t in tt) == 150         # rate x pages x dur
        carry = 0.0                                   # reset at the epoch start
        for t in tt:
            want = carry + 1.5
            assert t['n'] == int(want)
            assert abs(t['carry'] - (want - int(want))) < 1e-3
            assert t['done'] == t['n']
            carry = t['carry']
    for e in lines(log, 'epoch', what='end'):
        assert e['planned'] == e['done'] == '150'


def test_several_accesses_in_an_epoch(tmp_path):
    size = 4 * MB
    a, b = (0, 64 * PAGE), (512 * PAGE, 64 * PAGE)
    body = [region(0, size), ev('map', 0, 0, 0, size)] + \
        epochs(0, 200, 100, [access(0, a[0], a[1], 10), access(0, b[0], b[1], 30)])
    log, _ = run_logged(tmp_path, body, 'touches')
    sums = Counter()
    for t in ticks(log):
        sums[t['access']] += t['done']
    assert sums == {0: 2 * 64, 1: 6 * 64}             # rate x pages x 0.2 s
    pages = Counter('a' if p < 64 else 'b' if 512 <= p < 576 else '?'
                    for _, _, p, _ in touches(log))
    assert pages == {'a': 2 * 64, 'b': 6 * 64}


# ---------------------------------------------------------------- generators


def page_runs(log):
    return [p for _, _, p, _ in touches(log)]


def test_roundrobin_walks_in_a_circle_and_continues(tmp_path):
    pages, off = 100, 7
    body = [region(0, 128 * PAGE), ev('map', 0, 0, 0, 128 * PAGE)] + \
        epochs(0, 300, 100, [access(0, off * PAGE, pages * PAGE, 13)])
    log, _ = run_logged(tmp_path, body, 'touches')
    seq = page_runs(log)
    assert len(seq) == 3 * 130
    assert all(off <= p < off + pages for p in seq)
    # one circle across all three epochs: the next epoch goes on from the
    # page after the last one
    for x, y in zip(seq, seq[1:]):
        assert y - off == (x - off + 1) % pages


def start_pages(tmp_path, seed, name):
    body = [region(0, 4 * MB), ev('map', 0, 0, 0, 4 * MB)] + \
        epochs(0, 100, 100, [access(0, 0, 2 * MB, 5), access(0, 2 * MB, 2 * MB, 5)])
    d = tmp_path / name
    d.mkdir()
    log, _ = run_logged(d, body, 'touches', seed=seed)
    first = {}
    for _, _, p, _ in touches(log):
        first.setdefault(p >= 512, p)
    return first[False], first[True]


def test_roundrobin_start_depends_on_seed(tmp_path):
    a, b, c = start_pages(tmp_path, 1, 'a'), start_pages(tmp_path, 1, 'b'), \
        start_pages(tmp_path, 2, 'c')
    assert a == b
    assert a != c


def test_random_pages_are_uniform_in_range(tmp_path):
    pages, off = 64, 32
    body = [region(0, 128 * PAGE), ev('map', 0, 0, 0, 128 * PAGE)] + \
        epochs(0, 500, 100, [access(0, off * PAGE, pages * PAGE, 500, extra='pattern=random')])
    log, _ = run_logged(tmp_path, body, 'touches')
    seq = page_runs(log)
    n = len(seq)
    assert n == 500 * pages // 2
    assert all(off <= p < off + pages for p in seq)
    counts = Counter(seq)
    expected = n / pages
    chi2 = sum((counts[p] - expected) ** 2 / expected for p in range(off, off + pages))
    assert chi2 < 150                                # 63 degrees of freedom
    consecutive = sum(y == x + 1 for x, y in zip(seq, seq[1:]))
    assert consecutive < n * 0.1                     # not a walk


# ---------------------------------------------------------------- writes


def test_write_share_is_a_fixed_set_of_pages(tmp_path):
    pages = 1024
    size = pages * PAGE
    body = [region(0, size), ev('map', 0, 0, 0, size)] + \
        epochs(0, 1000, 100, [access(0, 0, size, 20, write=0.25)])
    log, _ = run_logged(tmp_path, body, 'touches')
    ops = defaultdict(list)
    for _, _, p, op in touches(log):
        ops[p].append(op)
    assert len(ops) == pages
    assert all(o[0] == 'w' for o in ops.values())   # first touch writes
    written = {p for p, o in ops.items() if 'w' in o[1:]}
    assert abs(len(written) / pages - 0.25) < 0.05
    # the same pages every time: a written page is always written, the
    # others are always read
    for p, o in ops.items():
        assert set(o[1:]) == ({'w'} if p in written else {'r'})


def test_unmapped_pages_are_skipped_and_remap_writes_again(tmp_path):
    pages = 64
    size = pages * PAGE
    body = [region(0, size), ev('map', 0, 0, 0, size), ev('unmap', 100, 0, 0, size),
            ev('map', 200, 0, 0, size)] + \
        epochs(0, 300, 100, [access(0, 0, size, 40, write=0)])
    log, p = run_logged(tmp_path, body, 'touches')
    tt = touches(log)
    before = [x for x in tt if x[0] < 100 * MS]
    gap = [x for x in tt if 100 * MS <= x[0] < 200 * MS]
    after = [x for x in tt if x[0] >= 200 * MS]
    assert before and gap and after
    assert all(op != 'skip' for *_, op in before)
    assert all(op == 'skip' for *_, op in gap)
    assert all(op != 'skip' for *_, op in after)
    first = {}
    for _, _, pg, op in after:
        first.setdefault(pg, op)
    assert set(first.values()) == {'w'}
    assert lines(log, 'end')[0]['skipped'] == str(len(gap))


# ---------------------------------------------------------------- overload


def test_dropped_work_is_not_done_later(tmp_path):
    size = 64 * MB
    body = [region(0, size), ev('map', 0, 0, 0, size)] + \
        epochs(0, 300, 100, [access(0, 0, size, 20000)])
    log, _ = run_logged(tmp_path, body, 'ticks')
    drops = [(int(d['tick']), int(d['skipped'])) for d in lines(log, 'drop')]
    assert drops
    skipped = set()
    for t, k in drops:
        skipped.update(range(t + 1, t + 1 + k))
    tk = ticks(log)
    assert not skipped & {t['tick'] for t in tk}     # no work in a skipped tick
    assert any(t['done'] < t['n'] for t in tk)       # the late tick stopped short
    for e in lines(log, 'epoch', what='end'):
        assert int(e['done']) < int(e['planned'])
    end = lines(log, 'end')[0]
    assert int(end['lagged_ticks']) >= len(drops)
