"""P4: tools/mkprofile.py builds a replay profile from a qmem-record directory."""
import subprocess
import sys

import pytest

from conftest import EXAMPLE, MKPROFILE
from fixtures.mkrecord import MB, PAGE, write_record

A = 0x7f0000000000           # 64 MB anonymous mapping
HEAP = 0x555500100000        # 1 MB heap
LIB = 0x555500000000         # a file mapping, must be skipped
SAMPLE_S = 0.005


def run_mkprofile(*args):
    return subprocess.run([sys.executable, str(MKPROFILE)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=60)


def parse(path):
    """Profile -> dict with regions, maps, unmaps and {epoch_t: [access]}."""
    prof = {'regions': {}, 'map': [], 'unmap': [], 'epochs': {}}
    current = None
    for line in path.read_text().splitlines():
        f = line.split('#')[0].split()
        if not f:
            continue
        if f[0] == 'region':
            prof['regions'][int(f[1])] = int(f[2])
        elif f[0] in ('map', 'unmap'):
            prof[f[0]].append(tuple(int(x) for x in f[1:]))
        elif f[0] == 'epoch':
            current = int(f[1])
            prof['epochs'][current] = []
        elif f[0] == 'access':
            prof['epochs'][current].append(
                (int(f[1]), int(f[2]), int(f[3]), int(f[4]), float(f[5])))
        elif f[0] in ('epoch_ms', 'seed', 'qmem-profile'):
            prof[f[0]] = f[1]
    return prof


def snaps(t0, t1, regions):
    """DAMON snapshots of 100 ms from t0 to t1 (ms) with the same regions."""
    return [(t * 10**6, (t + 100) * 10**6, regions) for t in range(t0, t1, 100)]


@pytest.fixture
def record(tmp_path):
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
    return write_record(tmp_path / 'rec', smaps, access, writes=writes,
                        softdirty_ms=1000)


@pytest.fixture
def profile(record, tmp_path):
    out = tmp_path / 'profile.txt'
    p = run_mkprofile(record, '-o', out)
    assert p.returncode == 0, p.stderr
    return parse(out), p


def region_of(prof, size):
    ids = [i for i, s in prof['regions'].items() if s == size]
    assert len(ids) == 1, prof['regions']
    return ids[0]


def test_header(profile):
    prof, _ = profile
    assert prof['qmem-profile'] == '1'
    assert prof['epoch_ms'] == '100'
    assert prof['seed'] == '42'


def test_regions_and_lifetime(profile):
    prof, p = profile
    a = region_of(prof, 64 * MB)
    heap = region_of(prof, MB)
    assert len(prof['regions']) == 2            # the file mapping is skipped
    assert 'file mappings skipped: 1' in p.stderr
    assert (0, heap, 0, MB) in prof['map']
    assert (1000, a, 0, 64 * MB) in prof['map']
    assert prof['unmap'] == [(3000, a, 0, 32 * MB)]


def test_access_rate_and_write_ratio(profile):
    prof, _ = profile
    a = region_of(prof, 64 * MB)
    # 1000-2000: hot first 16 MB at 100 %, half of it written
    for t in range(1000, 2000, 100):
        assert prof['epochs'][t] == [
            (a, 0, 16 * MB, round(1.0 * 16 * MB / PAGE / SAMPLE_S), 0.5)]
    # 2000-3000: 8 MB at 50 %; no writes sample covers it -> 1.0
    for t in range(2000, 3000, 100):
        assert prof['epochs'][t] == [
            (a, 40 * MB, 8 * MB, round(0.5 * 8 * MB / PAGE / SAMPLE_S), 1.0)]


def test_access_clipped_to_mapped_part(profile):
    prof, _ = profile
    a = region_of(prof, 64 * MB)
    # DAMON says [0, 48 MB) at 20 %, but the first 32 MB are unmapped by then
    for t in range(3000, 3500, 100):
        assert prof['epochs'][t] == [
            (a, 32 * MB, 16 * MB, round(0.2 * 16 * MB / PAGE / SAMPLE_S), 1.0)]


def test_quiet_epochs_are_left_out(profile):
    prof, _ = profile
    assert min(prof['epochs']) == 1000
    assert all(prof['epochs'].values())


def test_epoch_ms_and_seed_options(record, tmp_path):
    out = tmp_path / 'p.txt'
    p = run_mkprofile(record, '-o', out, '--epoch-ms', '200', '--seed', '9')
    assert p.returncode == 0, p.stderr
    prof = parse(out)
    assert prof['epoch_ms'] == '200' and prof['seed'] == '9'
    assert sorted(prof['epochs']) == list(range(1000, 3500, 200))


def test_usage(tmp_path):
    assert run_mkprofile().returncode == 2
    p = run_mkprofile(tmp_path / 'nothing', '-o', tmp_path / 'p.txt')
    assert p.returncode != 0


def test_real_example(tmp_path):
    out = tmp_path / 'p.txt'
    p = run_mkprofile(EXAMPLE, '-o', out)
    assert p.returncode == 0, p.stderr
    prof = parse(out)
    # the 300 MB of bytearrays appear by the 1502 ms snapshot ...
    big = [m for m in prof['map'] if m[3] >= 290 * MB]
    assert len(big) == 1 and 900 <= big[0][0] <= 1600
    # ... and about 190 MB of them go away by the 6879 ms snapshot
    freed = sum(u[3] for u in prof['unmap'] if 6300 <= u[0] <= 7000)
    assert 180 * MB <= freed <= 200 * MB
    # while they run, about 30 MB are hot
    hot = [sum(a[2] for a in prof['epochs'].get(t, []) if a[3] > 0)
           for t in range(3000, 5000, 100)]
    assert all(25 * MB <= h <= 45 * MB for h in hot), hot
