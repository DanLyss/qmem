"""tools/memtest2profile.py: a damoLoad memtest config -> profile v2 with
the same expected touches per page per second. See docs/replay_testing.md,
section 2.2."""
import json
import math
import subprocess
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / 'builder'))
import profile_v2  # noqa: E402

M2P = ROOT / 'tools' / 'memtest2profile.py'
CONFIGS = ROOT / 'third_party' / 'damoLoad' / 'memtest' / 'configs'
PAGE = 4096


def read_profile(path):
    """Profile v2 -> Profile, to check what memtest2profile wrote (replay has
    its own loader)."""
    prof, epoch = profile_v2.Profile(), None
    with open(path) as f:
        lines = [l.split('#')[0].split() for l in f]
    lines = [l for l in lines if l]
    if lines[:1] != [['qmem-profile', '2']]:
        raise ValueError('%s: not a profile v2' % path)
    for f in lines[1:]:
        k = dict(x.lstrip('!').split('=', 1) for x in f[1:])
        if f[0] == 'meta':
            prof.meta.update(k)
        elif f[0] == 'region':
            prof.regions.append(profile_v2.Region(int(k['id']), int(k['size']),
                                                  int(k.get('base', '0'), 16),
                                                  k.get('kind', 'anon')))
        elif f[0] in ('map', 'unmap', 'populate'):
            prof.events.append(profile_v2.Event(f[0], int(k['t']), int(k['region']),
                                                int(k['off']), int(k['len'])))
        elif f[0] == 'epoch':
            epoch = profile_v2.Epoch(int(k['t']),
                                     int(k.get('dur', prof.meta.get('epoch_ms', 100))))
            prof.epochs.append(epoch)
        elif f[0] == 'access':
            epoch.accesses.append(profile_v2.Access(int(k['region']), int(k['off']),
                                                    int(k['len']), float(k['rate']),
                                                    float(k.get('write', 0)),
                                                    k.get('pattern', 'roundrobin'),
                                                    int(k.get('threads', 1))))
        elif f[0] == 'counter':
            prof.counters.append(profile_v2.Counter(int(k['t']), k['name'],
                                                    float(k['value'])))
    return prof


def convert(tmp_path, cfg, *args):
    path = tmp_path / 'cfg.json'
    path.write_text(json.dumps(cfg))
    out = tmp_path / 'p.txt'
    p = subprocess.run([sys.executable, str(M2P), str(path), '-o', str(out)] + list(args),
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    return read_profile(out)


def config(tracks, regions=(10,), duration=2):
    return {'duration_sec': duration, 'regions': [{'pages': n} for n in regions],
            'tracks': tracks}


def track(spatial, temporal, region=0, **kw):
    return dict(region=region, spatial=spatial, temporal=temporal, **kw)


def rates(prof):
    """{(epoch t, dur): {(region, page): rate per page}} and the patterns."""
    out, patterns = {}, set()
    for ep in prof.epochs:
        r = out.setdefault((ep.t, ep.dur), {})
        for a in ep.accesses:
            assert a.off % PAGE == 0 and a.len % PAGE == 0
            for p in range(a.off // PAGE, (a.off + a.len) // PAGE):
                r[(a.region, p)] = r.get((a.region, p), 0) + a.rate
            patterns.add((a.region, a.pattern))
            assert a.write == 1
    return out, patterns


def only_epoch(prof):
    r, _ = rates(prof)
    assert len(r) == 1, sorted(r)
    return next(iter(r.items()))


def close(a, b, rel=1e-4):
    return abs(a - b) <= rel * max(abs(b), 1e-9)


# ---------------------------------------------------------------- memory


def test_regions_are_mapped_and_populated_at_zero(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {'type': 'const', 'hz': 10})],
                                     regions=(10, 3)))
    assert [(r.id, r.size, r.kind) for r in prof.regions] == [(0, 10 * PAGE, 'anon'),
                                                              (1, 3 * PAGE, 'anon')]
    ev = sorted((e.kind, e.t, e.region, e.off, e.len) for e in prof.events)
    assert ev == [('map', 0, 0, 0, 10 * PAGE), ('map', 0, 1, 0, 3 * PAGE),
                  ('populate', 0, 0, 0, 10 * PAGE), ('populate', 0, 1, 0, 3 * PAGE)]


# ---------------------------------------------------------------- spatial


def test_uniform(tmp_path):
    (t, dur), r = only_epoch(convert(tmp_path, config(
        [track({'type': 'uniform'}, {'type': 'const', 'hz': 100})])))
    assert (t, dur) == (0, 2000)
    assert r == {(0, p): 10.0 for p in range(10)}
    _, patterns = rates(convert(tmp_path, config(
        [track({'type': 'uniform'}, {'type': 'const', 'hz': 100})])))
    assert patterns == {(0, 'random')}


def test_sequential_and_all_walk_round_robin(tmp_path):
    prof = convert(tmp_path, config([
        track({'type': 'sequential'}, {'type': 'const', 'hz': 100}, region=0),
        track({'type': 'all'}, {'type': 'const', 'hz': 7}, region=1)], regions=(10, 4)))
    (_, _), r = only_epoch(prof)
    assert r == {**{(0, p): 10.0 for p in range(10)}, **{(1, p): 7.0 for p in range(4)}}
    assert rates(prof)[1] == {(0, 'roundrobin'), (1, 'roundrobin')}


def test_hotspot(tmp_path):
    prof = convert(tmp_path, config([track(
        {'type': 'hotspot', 'hot_pages': [0, 1], 'hot_ratio': 0.9},
        {'type': 'const', 'hz': 100})]))
    (_, _), r = only_epoch(prof)
    assert all(close(r[(0, p)], 45.0) for p in (0, 1))
    assert all(close(r[(0, p)], 100 * 0.1 / 8) for p in range(2, 10))
    # one line for the hot pages, one for the cold ones (the last cold page
    # differs from the others only by rounding)
    assert [(a.off // PAGE, a.len // PAGE) for a in prof.epochs[0].accesses] == [(0, 2), (2, 8)]


def test_zipf(tmp_path):
    (_, _), r = only_epoch(convert(tmp_path, config(
        [track({'type': 'zipf', 's': 1.0}, {'type': 'const', 'hz': 100})], regions=(4,))))
    h = sum(1 / k for k in range(1, 5))
    for p in range(4):
        assert close(r[(0, p)], 100 / (p + 1) / h), p


def test_gaussian(tmp_path):
    (_, _), r = only_epoch(convert(tmp_path, config(
        [track({'type': 'gaussian', 'center': 5.0, 'sigma': 1.5}, {'type': 'const', 'hz': 100})])))

    def cdf(x):
        return 0.5 * (1 + math.erf((x - 5.0) / (1.5 * math.sqrt(2))))
    for p in range(10):
        lo = 0.0 if p == 0 else cdf(p - 0.5)
        hi = 1.0 if p == 9 else cdf(p + 0.5)
        assert close(r.get((0, p), 0), 100 * (hi - lo), rel=1e-3), p
    assert close(sum(r.values()), 100)


def test_unknown_spatial_is_uniform_like_memtest(tmp_path):
    (_, _), r = only_epoch(convert(tmp_path, config(
        [track({'type': 'mystery'}, {'type': 'const', 'hz': 50})], regions=(5,))))
    assert r == {(0, p): 10.0 for p in range(5)}


# ---------------------------------------------------------------- temporal


def test_steps_merge_into_epochs(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {'type': 'steps', 'steps': [
        {'hz': 50, 'duration_sec': 1}, {'hz': 200, 'duration_sec': 0.5}]})]))
    r, _ = rates(prof)
    # the last step holds after its end, as in memtest
    assert r == {(0, 1000): {(0, p): 5.0 for p in range(10)},
                 (1000, 1000): {(0, p): 20.0 for p in range(10)}}


def test_square(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {
        'type': 'square', 'on_hz': 200, 'duty': 0.3, 'period_sec': 1.0})]))
    r, _ = rates(prof)
    assert sorted(r) == [(0, 300), (1000, 300)]          # silent epochs left out
    assert all(v == 20.0 for e in r.values() for v in e.values())


def test_square_phase(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {
        'type': 'square', 'on_hz': 200, 'duty': 0.5, 'period_sec': 1.0,
        'phase_rad': math.pi / 2})]))
    # a quarter period ahead: on in [0, 250) and [750, 1250) ...
    assert sorted(rates(prof)[0]) == [(0, 250), (750, 500), (1750, 250)]


def test_ramp_and_sine_follow_the_curve(tmp_path):
    prof = convert(tmp_path, config([
        track({'type': 'uniform'}, {'type': 'ramp', 'start_hz': 0, 'end_hz': 200}, region=0),
        track({'type': 'uniform'}, {'type': 'sine', 'base_hz': 50, 'amplitude': 80,
                                    'period_sec': 1.0}, region=1)], regions=(10, 10)),
        '--epoch-ms', '10')
    r, _ = rates(prof)
    for (t, dur), pages in r.items():
        mid = (t + dur / 2) / 1000
        if (0, 0) in pages:
            assert abs(pages[(0, 0)] * 10 - 200 * mid / 2) < 2
        if (1, 0) in pages:
            want = max(0.0, 50 + 80 * math.sin(2 * math.pi * mid))
            assert abs(pages[(1, 0)] * 10 - want) < 8      # clamping inside an epoch
    # sine below zero: silent stretches, no epochs for region 1 there
    quiet = [t for (t, dur), p in r.items() if (1, 0) not in p]
    assert quiet


def test_track_window(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {'type': 'const', 'hz': 100},
                                           start_sec=0.5, end_sec=1.25)]))
    assert sorted(rates(prof)[0]) == [(500, 750)]


def test_tracks_add_up(tmp_path):
    (_, _), r = only_epoch(convert(tmp_path, config([
        track({'type': 'uniform'}, {'type': 'const', 'hz': 100}),
        track({'type': 'all'}, {'type': 'const', 'hz': 3})])))
    assert r == {(0, p): 13.0 for p in range(10)}


def test_duration_option_cuts_the_config(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {'type': 'const', 'hz': 100})],
                                    duration=30), '--duration', '1.5')
    assert sorted(rates(prof)[0]) == [(0, 1500)]


def test_meta(tmp_path):
    prof = convert(tmp_path, config([track({'type': 'uniform'}, {'type': 'const', 'hz': 1})]),
                   '--epoch-ms', '20', '--seed', '5')
    assert prof.meta['epoch_ms'] == '20' and prof.meta['seed'] == '5'
    assert prof.meta['source'] == 'memtest:cfg'


# ---------------------------------------------------------------- shipped configs


@pytest.mark.skipif(not CONFIGS.is_dir(), reason='damoLoad submodule not checked out')
def test_every_shipped_config_converts(tmp_path):
    for cfg in sorted(CONFIGS.glob('*.json')):
        out = tmp_path / (cfg.stem + '.txt')
        p = subprocess.run([sys.executable, str(M2P), str(cfg), '-o', str(out),
                            '--duration', '1'], capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, (cfg.name, p.stderr)
        prof = read_profile(out)
        assert len(prof.regions) == len(json.loads(cfg.read_text())['regions']), cfg.name
