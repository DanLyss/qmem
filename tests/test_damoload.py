"""tools/damoload_check.py: damoLoad's memtest (taken as correct) and
qmem-replay on the same workload, compared touch by touch. See
docs/replay_testing.md, section 2.3."""
import json
import subprocess
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, str(ROOT / 'tools'))
import damoload_check as dc  # noqa: E402

CHECK = ROOT / 'tools' / 'damoload_check.py'
CONFIGS = ROOT / 'third_party' / 'damoLoad' / 'memtest' / 'configs'
needs_damoload = pytest.mark.skipif(not CONFIGS.is_dir(),
                                    reason='damoLoad submodule not checked out')
MS = 1_000_000


# ---------------------------------------------------------------- logs


def test_read_gt(tmp_path):
    p = tmp_path / 'gt.log'
    p.write_text('# region 0 base=0x7f0000000000 pages=10\n# region 1 base=0x7f1000000000 pages=3\n'
                 '# ts_ns region page\n1000 0 3\n2000 1 2\n')
    regions, touches = dc.read_gt(p)
    assert regions == [10, 3]
    assert touches == [(1000, 0, 3), (2000, 1, 2)]


def test_read_replay_log(tmp_path):
    p = tmp_path / 'run.log'
    p.write_text('start seed=1 regions=1 events=1 epochs=1 end_ms=10 level=touches\n'
                 'region id=0 pages=4 base=0x7f0000000000\n'
                 'touch t_ns=5 region=0 page=1 op=w\ntouch t_ns=6 region=0 page=2 op=skip\n'
                 'touch t_ns=7 region=0 page=3 op=r\nend planned=3 done=2 skipped=1 populated=0 '
                 'lagged_ticks=0\n')
    touches, end = dc.read_replay_log(p)
    assert touches == [(5, 0, 1), (7, 0, 3)]            # skipped touches touched nothing
    assert end['lagged_ticks'] == '0'


# ---------------------------------------------------------------- comparison


def counts(touches, duration=1.0):
    return dc.count(touches, t0=0, duration=duration, window_ms=100)


def uniform(n_per_page_window, pages=10, windows=10, region=0, jitter=0):
    out = []
    for w in range(windows):
        for p in range(pages):
            for k in range(n_per_page_window + (jitter if (w + p) % 2 else 0)):
                out.append(((w * 100 + 1 + k % 90) * MS, region, p))
    return out


def test_same_touches_match():
    t = uniform(20)
    r = dc.compare(counts(t), counts(t))
    assert r['ok'], r


def test_noise_within_poisson_matches():
    r = dc.compare(counts(uniform(20)), counts(uniform(20, jitter=1)))
    assert r['ok'], r


def test_window_total_off_is_caught():
    ref = uniform(20)
    got = [x for x in ref if not (300 * MS <= x[0] < 400 * MS)]       # one window empty
    r = dc.compare(counts(ref), counts(got))
    assert not r['ok']
    assert [(b['region'], b['window']) for b in r['bad_windows']] == [(0, 3)]


def test_page_distribution_off_is_caught():
    ref = uniform(20)
    got = [(t, r, 0 if p < 5 else p) for t, r, p in ref]              # pages 1-4 moved to 0
    r = dc.compare(counts(ref), counts(got))
    assert not r['ok']
    assert r['regions'][0]['tv'] > r['regions'][0]['tv_limit']
    assert not r['bad_windows']                                        # totals still agree


def test_total_off_is_caught():
    r = dc.compare(counts(uniform(200)), counts(uniform(180)))
    assert not r['ok']
    assert abs(r['total_rel'] + 0.1) < 1e-9


def test_touches_outside_the_run_are_dropped():
    c = counts([(-5 * MS, 0, 0), (50 * MS, 0, 1), (1000 * MS, 0, 2)], duration=1.0)
    assert c['total'] == 1


# ---------------------------------------------------------------- end to end


def run_check(tmp_path, cfg, *args):
    out = tmp_path / 'report.json'
    p = subprocess.run([sys.executable, str(CHECK), str(CONFIGS / cfg), '--duration', '3',
                        '--json', str(out), '--keep', str(tmp_path / 'work')] + list(args),
                       capture_output=True, text=True, timeout=300)
    report = json.loads(out.read_text())[0] if out.exists() else None
    return p, report


@needs_damoload
@pytest.mark.parametrize('cfg', [
    '05_ten_pages_sequential.json',       # sequential, const
    '06_all_pages_simultaneously.json',   # all
    '08_hotspot_two_pages.json',          # hotspot
    '23_two_tracks_same_region.json',     # two tracks on one region
    '36_three_regions.json',              # 3 regions; uniform, hotspot, zipf; sine, square
    '48_three_regions_complex.json',      # all, gaussian, hotspot; ramp, sine, square
])
def test_replay_matches_memtest(tmp_path, cfg):
    p, report = run_check(tmp_path, cfg)
    assert p.returncode == 0, p.stdout + p.stderr
    assert report['ok'], json.dumps(report, indent=1)[:3000]
    assert report['total_ref'] > 0
    assert (tmp_path / 'work' / 'gt.log').exists()
    assert (tmp_path / 'work' / 'replay.log').exists()


@needs_damoload
def test_a_wrong_replay_is_caught(tmp_path):
    """The check is not blind: replaying 30 % slower must fail."""
    p, report = run_check(tmp_path, '04_ten_pages_uniform.json', '--scale-rate', '0.7')
    assert p.returncode == 1, p.stdout + p.stderr
    assert not report['ok']
    assert report['total_rel'] < -0.2
