"""builder: builder/qmem-build.py turns a record into a profile v2. See docs/skeleton.md (section 3) and docs/formats/profile.md.

Only the skeleton is checked: readers, estimators, the writer and the
format. The rates are DAMON's pseudo-frequencies passed through
(intensity=passthrough), not an estimate of real accesses."""
import subprocess
import sys

import pytest

from conftest import BUILD
from fixtures.mkrecord import MB, write_record

A = 0x7f0000000000           # 64 MB anonymous mapping
HEAP = 0x555500100000        # 1 MB heap
LIB = 0x555500000000         # a file mapping, must be skipped
SAMPLE_S = 0.005


def run_build(*args):
    return subprocess.run([sys.executable, str(BUILD)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=60)


def parse(path):
    """Profile v2 -> dict: version, meta, regions {id: {key: value}},
    map/unmap/populate [(t, region, off, len)], epochs {t: dur} and
    accesses {epoch t: [{key: value}]}."""
    prof = {'meta': {}, 'regions': {}, 'map': [], 'unmap': [], 'populate': [],
            'epochs': {}, 'access': {}}
    lines = [l.split('#')[0].split() for l in path.read_text().splitlines()]
    lines = [l for l in lines if l]
    assert lines[0][0] == 'qmem-profile'
    prof['version'] = lines[0][1]
    current = None
    for f in lines[1:]:
        kv = dict(x.lstrip('!').split('=', 1) for x in f[1:])
        if f[0] == 'meta':
            prof['meta'].update(kv)
        elif f[0] == 'region':
            prof['regions'][int(kv['id'])] = kv
        elif f[0] in ('map', 'unmap', 'populate'):
            prof[f[0]].append(tuple(int(kv[k]) for k in ('t', 'region', 'off', 'len')))
        elif f[0] == 'epoch':
            current = int(kv['t'])
            prof['epochs'][current] = int(kv['dur'])
            prof['access'][current] = []
        elif f[0] == 'access':
            prof['access'][current].append(kv)
    return prof


def acc(prof, t):
    """Accesses of epoch t as (region, off, len, rate, write)."""
    return [(int(a['region']), int(a['off']), int(a['len']), round(float(a['rate']), 3),
             float(a.get('write', 0))) for a in prof['access'][t]]


def snaps(t0, t1, regions):
    """DAMON snapshots of 100 ms from t0 to t1 (ms) with the same regions."""
    return [(t * 10**6, (t + 100) * 10**6, regions) for t in range(t0, t1, 100)]


def example_record(d):
    heap = (HEAP, HEAP + MB, '[heap]', 1024)
    lib = (LIB, LIB + 0x1000, '/usr/bin/app', 4)
    full = (A, A + 64 * MB, '', 65536)
    back_half = (A + 32 * MB, A + 64 * MB, '', 32768)
    smaps = {0: [lib, heap], 1000: [lib, heap, full], 2000: [lib, heap, full],
             3000: [lib, heap, back_half]}
    access = (snaps(1000, 2000, [(A, A + 16 * MB, 100.0),
                                 (A + 16 * MB, A + 64 * MB, 0.0)])
              + snaps(2000, 3000, [(A, A + 40 * MB, 0.0),
                                   (A + 40 * MB, A + 48 * MB, 50.0)])
              + snaps(3000, 3500, [(A, A + 48 * MB, 20.0),
                                   (A + 48 * MB, A + 64 * MB, 0.0)]))
    # 8 MB written in (1000, 2000] where 16 MB were accessed -> 0.5
    writes = [(2000, A, A + 64 * MB, 16384, 2048)]
    return write_record(d, smaps, access, writes=writes, softdirty_ms=1000)


@pytest.fixture
def record(tmp_path):
    return example_record(tmp_path / 'rec')


@pytest.fixture
def profile(record, tmp_path):
    out = tmp_path / 'profile.txt'
    p = run_build(record, '-o', out)
    assert p.returncode == 0, p.stderr
    return parse(out), p


def region_of(prof, size):
    ids = [i for i, r in prof['regions'].items() if int(r['size']) == size]
    assert len(ids) == 1, prof['regions']
    return ids[0]


# ------------------------------------------------------------------ format


def test_header_and_meta(profile):
    prof, _ = profile
    assert prof['version'] == '2'
    meta = prof['meta']
    assert meta['epoch_ms'] == '100' and meta['seed'] == '42'
    # which estimators made the profile
    assert meta['intensity'] == 'passthrough'
    assert meta['write'] == 'softdirty'
    assert meta['timing'] == 'roundrobin'


def test_regions_are_anonymous_with_bases(profile):
    prof, _ = profile
    assert sorted(prof['regions']) == list(range(len(prof['regions'])))
    for r in prof['regions'].values():
        assert r['kind'] == 'anon'
    assert int(prof['regions'][region_of(prof, 64 * MB)]['base'], 16) == A
    assert int(prof['regions'][region_of(prof, MB)]['base'], 16) == HEAP


def test_epochs_have_dur_and_pattern(profile):
    prof, _ = profile
    assert prof['epochs'] and set(prof['epochs'].values()) == {100}
    for accesses in prof['access'].values():
        for a in accesses:
            assert a['pattern'] == 'roundrobin'


# ------------------------------------------------------------------ estimators


def test_lifecycle(profile):
    prof, p = profile
    a = region_of(prof, 64 * MB)
    heap = region_of(prof, MB)
    assert len(prof['regions']) == 2            # the file mapping is skipped
    assert 'file mappings skipped: 1' in p.stderr
    # what the first snapshot shows existed from the start; a change between
    # two snapshots is put halfway between them
    assert (0, heap, 0, MB) in prof['map']
    assert (500, a, 0, 64 * MB) in prof['map']
    assert prof['unmap'] == [(2500, a, 0, 32 * MB)]


def test_resident(profile):
    prof, _ = profile
    a = region_of(prof, 64 * MB)
    heap = region_of(prof, MB)
    assert prof['populate'] == [(0, heap, 0, MB), (500, a, 0, 64 * MB)]


def test_resident_follows_rss_growth(tmp_path):
    base = 0x7f0000000000
    smaps = {0: [(base, base + 8 * MB, '', 4096)],          # half resident
             1000: [(base, base + 8 * MB, '', 8192)]}       # then all of it
    rec = write_record(tmp_path / 'rec', smaps,
                       snaps(0, 2000, [(base, base + 8 * MB, 0.0)]))
    out = tmp_path / 'p.txt'
    p = run_build(rec, '-o', out)
    assert p.returncode == 0, p.stderr
    assert parse(out)['populate'] == [(0, 0, 0, 4 * MB), (500, 0, 4 * MB, 4 * MB)]


def test_intensity_passthrough_is_damon_pseudofrequency(profile):
    """rate = touches per page per second = DAMON's access rate over its
    sampling interval, without any conversion (the stub)."""
    prof, _ = profile
    a = region_of(prof, 64 * MB)
    for t in range(1000, 2000, 100):
        assert acc(prof, t) == [(a, 0, 16 * MB, round(1.0 / SAMPLE_S, 3), 0.5)]
    # 2000-3000: no writes sample covers it, the nearest one gives 8 / 8 = 1.0
    for t in range(2000, 3000, 100):
        assert acc(prof, t) == [(a, 40 * MB, 8 * MB, round(0.5 / SAMPLE_S, 3), 1.0)]


def test_access_clipped_to_mapped_part(profile):
    prof, _ = profile
    a = region_of(prof, 64 * MB)
    for t in range(3000, 3500, 100):
        assert acc(prof, t) == [(a, 32 * MB, 16 * MB, round(0.2 / SAMPLE_S, 3), 0.5)]


def test_quiet_epochs_are_left_out(profile):
    prof, _ = profile
    assert min(prof['epochs']) == 1000
    assert all(prof['access'].values())


def test_damon_gap_holds_last_pattern(tmp_path):
    base = 0x7f0000000000
    smaps = {0: [(base, base + 8 * MB, '', 8192)], 3000: [(base, base + 8 * MB, '', 8192)]}
    hot = [(base, base + 4 * MB, 100.0), (base + 4 * MB, base + 8 * MB, 0.0)]
    rec = write_record(tmp_path / 'rec', smaps, snaps(0, 1000, hot) + snaps(2000, 3000, hot))
    out = tmp_path / 'p.txt'
    p = run_build(rec, '-o', out)
    assert p.returncode == 0, p.stderr
    prof = parse(out)
    assert sorted(prof['epochs']) == list(range(0, 3000, 100))
    assert prof['access'][1500] == prof['access'][500]
    assert 'gap' in p.stderr


def test_write_without_softdirty_is_all_writes(tmp_path):
    base = 0x7f0000000000
    smaps = {0: [(base, base + 8 * MB, '', 8192)]}
    rec = write_record(tmp_path / 'rec', smaps,
                       snaps(0, 500, [(base, base + 8 * MB, 100.0)]))
    out = tmp_path / 'p.txt'
    assert run_build(rec, '-o', out).returncode == 0
    prof = parse(out)
    assert all(float(a['write']) == 1.0 for t in prof['access'] for a in prof['access'][t])


# ------------------------------------------------------------------ options


def test_epoch_ms_and_seed_options(record, tmp_path):
    out = tmp_path / 'p.txt'
    p = run_build(record, '-o', out, '--epoch-ms', '200', '--seed', '9')
    assert p.returncode == 0, p.stderr
    prof = parse(out)
    assert prof['meta']['epoch_ms'] == '200' and prof['meta']['seed'] == '9'
    assert sorted(prof['epochs']) == list(range(1000, 3500, 200))
    assert set(prof['epochs'].values()) == {200}


def test_unknown_estimator(record, tmp_path):
    for opt in ('--intensity', '--write', '--timing'):
        p = run_build(record, '-o', tmp_path / 'p.txt', opt, 'nonsense')
        assert p.returncode == 2, opt
        assert 'nonsense' in p.stderr


def test_record_without_damon(tmp_path):
    """No damon source: regions, lifetime and resident memory, no epochs."""
    base = 0x7f0000000000
    rec = write_record(tmp_path / 'rec', {0: [(base, base + 8 * MB, '', 8192)]}, None)
    out = tmp_path / 'p.txt'
    p = run_build(rec, '-o', out)
    assert p.returncode == 0, p.stderr
    prof = parse(out)
    assert len(prof['regions']) == 1 and prof['populate']
    assert prof['epochs'] == {}
    assert 'damon' in p.stderr


def test_usage(tmp_path):
    assert run_build().returncode == 2
    p = run_build(tmp_path / 'nothing', '-o', tmp_path / 'p.txt')
    assert p.returncode != 0
