"""V2-V4: qmem-verify compares access frequency by place (region + offset +
time) and memory over time, on synthetic records with a known answer."""
import json
import subprocess
import sys

import pytest

from conftest import VERIFY
from fixtures.mkrecord import MB, write_record

A = 0x7f0000000000          # region base in the original
B = 0x7e1000000000          # region base in the replay
SIZE = 64 * MB


def run_verify(*args):
    return subprocess.run([sys.executable, str(VERIFY)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=60)


def record(d, base, delay_ms, hot_off_mb, hot_mb, rate, hot_from_ms, end_ms,
           rss_from_ms, start_uptime, dirty=None):
    """A record whose profile time t happens at record time delay_ms + t.
    The region is mapped (and fully resident from rss_from_ms) from t=0;
    hot_mb at hot_off_mb is accessed at `rate` % from hot_from_ms to end_ms."""
    d0 = delay_ms
    smaps = {}
    for t in range(0, end_ms + 1, 250):
        rss = SIZE // 1024 if t >= rss_from_ms else 1024
        smaps[d0 + t] = [(base, base + SIZE, '', rss)]
    access = []
    for t in range(0, end_ms, 100):
        hot = t >= hot_from_ms
        lo, hi = base + hot_off_mb * MB, base + (hot_off_mb + hot_mb) * MB
        regions = ([(base, lo, 0.0), (lo, hi, float(rate)), (hi, base + SIZE, 0.0)]
                   if hot else [(base, base + SIZE, 0.0)])
        access.append(((d0 + t) * 10**6, (d0 + t + 100) * 10**6, regions))
    proc = [(d0 + t, 1000 + (16384 if t >= rss_from_ms else 0), 0,
             SIZE // 1024 if t >= rss_from_ms else 1024)
            for t in range(0, end_ms + 1, 250)]
    system = [(d0, 1000, 0, 0), (d0 + end_ms, 20000, 0, 0)]
    return write_record(d, smaps, access, system=system, proc=proc,
                        writes=dirty, start_uptime=start_uptime)


def case(tmp_path, *, replay_off=8, replay_rate=100, replay_hot_from=1000,
         replay_rss_from=0, delay=700):
    orig = record(tmp_path / 'orig', A, 0, 8, 8, 100, 1000, 3000, 0, 100.0)
    again = record(tmp_path / 'again', B, delay, replay_off, 8, replay_rate,
                   replay_hot_from, 3000, replay_rss_from, 200.0)
    profile = tmp_path / 'profile.txt'
    profile.write_text('qmem-profile 1\nepoch_ms 100\nregion 0 %d 0x%x\n' % (SIZE, A))
    layout = tmp_path / 'layout.txt'
    layout.write_text('qmem-replay-layout 1\nstart_boottime_ns %d\nregion 0 0x%x %d\n'
                      % (int((200.0 + delay / 1000) * 1e9), B, SIZE))
    return orig, again, profile, layout


def verify_json(orig, again, profile, layout, *extra):
    p = run_verify(orig, again, '--profile', profile, '--layout', layout,
                   '--json', *extra)
    assert p.returncode in (0, 1), p.stderr
    return json.loads(p.stdout)


def test_identical_replay(tmp_path):
    out = verify_json(*case(tmp_path))
    assert out['shift_ms'] == pytest.approx(700, abs=1)
    m = out['maps']
    assert m['mae_pp'] == pytest.approx(0, abs=0.01)
    assert m['corr'] == pytest.approx(1, abs=0.001)
    assert m['hot_recall'] == pytest.approx(1)
    assert m['hot_precision'] == pytest.approx(1)
    assert out['series']['rss_corr'] == pytest.approx(1, abs=0.001)
    assert out['series']['rss_mean_dev'] == pytest.approx(0, abs=0.001)
    orig, again, profile, layout = case(tmp_path / 'again')
    assert run_verify(orig, again, '--profile', profile, '--layout', layout,
                      '--check').returncode == 0


def test_moved_hot_spot_is_caught(tmp_path):
    """Same amount of hot memory, other place: the totals agree, the maps
    do not."""
    orig, again, profile, layout = case(tmp_path, replay_off=40)
    out = verify_json(orig, again, profile, layout)
    assert out['metrics']['hot_mb_mean']['diff'] == pytest.approx(0, abs=0.01)
    m = out['maps']
    assert m['hot_recall'] == pytest.approx(0)
    assert m['hot_precision'] == pytest.approx(0)
    assert m['corr'] < 0.2
    p = run_verify(orig, again, '--profile', profile, '--layout', layout, '--check')
    assert p.returncode == 1
    assert 'hot_recall' in p.stdout


def test_hot_spot_at_another_time(tmp_path):
    out = verify_json(*case(tmp_path, replay_hot_from=2000))
    m = out['maps']
    assert m['corr'] < 0.8
    assert m['hot_recall'] < 0.6


def test_half_the_rate(tmp_path):
    orig, again, profile, layout = case(tmp_path, replay_rate=50)
    out = verify_json(orig, again, profile, layout)
    m = out['maps']
    assert m['mae_pp'] == pytest.approx(50, abs=1)
    assert m['hot_recall'] == pytest.approx(1)     # still warm where it should be
    assert m['corr'] > 0.95                        # same shape
    p = run_verify(orig, again, '--profile', profile, '--layout', layout, '--check')
    assert p.returncode == 1 and 'mae_pp' in p.stdout


def test_text_maps(tmp_path):
    orig, again, profile, layout = case(tmp_path)
    p = run_verify(orig, again, '--profile', profile, '--layout', layout)
    assert p.returncode == 0, p.stderr
    assert 'region 0' in p.stdout
    # 64 one-megabyte columns; the hot 8 MB at 8 MB show as 9s
    assert '0' * 8 + '9' * 8 + '0' * 48 in p.stdout


def test_per_region_results(tmp_path):
    out = verify_json(*case(tmp_path, replay_off=40))
    assert [r['region'] for r in out['regions']] == [0]
    assert out['regions'][0]['hot_recall'] == pytest.approx(0)


def test_without_layout_only_totals(tmp_path):
    orig, again, _, _ = case(tmp_path)
    p = run_verify(orig, again, '--json')
    assert p.returncode == 0, p.stderr
    out = json.loads(p.stdout)
    assert out['maps'] is None
    assert 'no --profile/--layout' in p.stderr


def test_rss_growing_at_another_time(tmp_path):
    """Same peak RSS, reached 1.5 s later: the series catch it."""
    orig, again, profile, layout = case(tmp_path, replay_rss_from=1500)
    out = verify_json(orig, again, profile, layout)
    assert out['metrics']['rss_peak_mb']['diff'] == pytest.approx(0)
    assert out['series']['rss_mean_dev'] > 0.3
    p = run_verify(orig, again, '--profile', profile, '--layout', layout, '--check')
    assert p.returncode == 1 and 'rss_mean_dev' in p.stdout


def test_one_late_bin_is_tolerated(tmp_path):
    """A flat original and a replay that is one time bin late: the mean
    deviation stays small, so the check passes (correlation is meaningless
    for a flat series and is not checked)."""
    orig, again, profile, layout = case(tmp_path, replay_rss_from=250)
    out = verify_json(orig, again, profile, layout)
    assert out['series']['rss_mean_dev'] < 0.1
    assert run_verify(orig, again, '--profile', profile, '--layout', layout,
                      '--check').returncode == 0


def test_dirty_share_skips_vmas_marked_sd(tmp_path):
    """After munmap/mremap the kernel marks a VMA soft-dirty as a whole; such
    rows must not count."""
    base = A
    smaps = {0: [(base, base + SIZE, '', 65536)],
             1000: [(base, base + SIZE, '', 65536)],
             2000: [(base, base + SIZE, '', 65536, 'rd wr mr mw me ac sd')],
             3000: [(base, base + SIZE, '', 65536)]}
    access = [(t * 10**6, (t + 100) * 10**6, [(base, base + SIZE, 10.0)])
              for t in range(0, 3000, 100)]
    pages = SIZE // 4096
    writes = [(1000, base, base + SIZE, pages, pages),          # start-up
              (2000, base, base + SIZE, pages, pages),          # sd: whole VMA
              (3000, base, base + SIZE, pages, pages // 4)]
    a = write_record(tmp_path / 'a', smaps, access, writes=writes)
    out = json.loads(run_verify(a, a, '--json').stdout)
    assert out['metrics']['dirty_share']['orig'] == pytest.approx(0.25)


def test_faults_net_of_soft_dirty(tmp_path):
    """Soft-dirty sampling adds a write fault per rewritten page; records with
    a different number of samples compare by faults net of those."""
    base = A
    smaps = {0: [(base, base + SIZE, '', 65536)], 3000: [(base, base + SIZE, '', 65536)]}
    access = [(t * 10**6, (t + 100) * 10**6, [(base, base + SIZE, 10.0)])
              for t in range(0, 3000, 100)]
    pages = SIZE // 4096
    a = write_record(tmp_path / 'a', smaps, access,
                     proc=[(0, 1000, 0, 1024), (3000, 1000 + pages + 4096, 0, 65536)],
                     writes=[(1000, base, base + SIZE, pages, 4096, 4096)])
    b = write_record(tmp_path / 'b', smaps, access,
                     proc=[(0, 1000, 0, 1024), (3000, 1000 + pages + 3 * 4096, 0, 65536)],
                     writes=[(1000, base, base + SIZE, pages, 4096, 4096),
                             (2000, base, base + SIZE, pages, 4096, 4096),
                             (3000, base, base + SIZE, pages, 4096, 4096)])
    out = json.loads(run_verify(a, b, '--json').stdout)
    m = out['metrics']
    assert m['app_minflt']['diff'] > 0.3
    assert m['app_minflt_net']['orig'] == 1000 + pages
    assert m['app_minflt_net']['diff'] == pytest.approx(0)
    assert run_verify(a, b, '--check').returncode == 0


def test_gaps_are_reported(tmp_path):
    orig, again, profile, layout = case(tmp_path)
    rows = (orig / 'access.csv').read_text().splitlines()
    keep = [rows[0]] + [r for r in rows[1:]
                        if not 1500e6 <= int(r.split(',')[0]) < 2500e6]
    (orig / 'access.csv').write_text('\n'.join(keep) + '\n')
    out = verify_json(orig, again, profile, layout)
    assert out['gaps']['orig_ms'] == pytest.approx(1000, abs=150)
    assert out['gaps']['replay_ms'] == 0
