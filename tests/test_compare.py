"""P5: tools/compare.py compares the record of the original with the record of
the replay by what does not depend on addresses."""
import json
import subprocess
import sys

import pytest

from conftest import COMPARE, EXAMPLE
from fixtures.mkrecord import MB, write_record

A = 0x7f0000000000
B = 0x7e0000000000


def run_compare(*args):
    return subprocess.run([sys.executable, str(COMPARE)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=60)


def make(d, base, shift_ms, peak_mb, hot_mb, pgfault, dirty=None, minflt=None):
    """A record: anon RSS grows to peak_mb at shift_ms+1000, hot_mb accessed
    from shift_ms+1000 to shift_ms+3000, pgfault grows by pgfault."""
    s = shift_ms
    smaps = {s + 0: [(base, base + MB, '', 512)],
             s + 1000: [(base, base + peak_mb * MB, '', peak_mb * 1024)],
             s + 2000: [(base, base + peak_mb * MB, '', peak_mb * 1024)],
             s + 3000: [(base, base + peak_mb * MB, '', peak_mb * 1024)]}
    access = [(t * 10**6, (t + 100) * 10**6,
               [(base, base + hot_mb * MB, 100.0),
                (base + hot_mb * MB, base + peak_mb * MB, 0.0)])
              for t in range(s + 1000, s + 3000, 100)]
    system = [(s + 0, 1000, 10, 0), (s + 3000, 1000 + pgfault, 10, 0)]
    writes = None
    if dirty is not None:
        # the first sample holds the start-up writes and is not compared
        writes = [(s + 1000, base, base + peak_mb * MB, peak_mb * 256, peak_mb * 256),
                  (s + 2000, base, base + peak_mb * MB, peak_mb * 256,
                   int(peak_mb * 256 * dirty))]
    proc = None
    if minflt is not None:
        proc = [(s + 0, minflt // 2, 0, 512), (s + 3000, minflt, 1, peak_mb * 1024)]
    return write_record(d, smaps, access, system=system, writes=writes, proc=proc)


def metrics(p):
    assert p.returncode in (0, 1), p.stderr
    return json.loads(p.stdout)['metrics']


def test_identical_records(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000, dirty=0.25)
    m = metrics(run_compare(a, a, '--json'))
    for name in ('rss_peak_mb', 'hot_mb_mean', 'pgfault', 'dirty_share'):
        assert m[name]['diff'] == 0, name


def test_values_and_differences(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000, dirty=0.25)
    b = make(tmp_path / 'b', B, 500, 48, 20, 25000, dirty=0.5)
    m = metrics(run_compare(a, b, '--json'))
    assert m['rss_peak_mb']['orig'] == pytest.approx(64)
    assert m['rss_peak_mb']['replay'] == pytest.approx(48)
    assert m['rss_peak_mb']['diff'] == pytest.approx(-0.25)
    assert m['hot_mb_mean']['orig'] == pytest.approx(16)
    assert m['hot_mb_mean']['replay'] == pytest.approx(20)
    assert m['pgfault']['diff'] == pytest.approx(0.25)
    assert m['dirty_share']['orig'] == pytest.approx(0.25)
    assert m['dirty_share']['replay'] == pytest.approx(0.5)


def test_app_faults_from_proc_csv(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000, minflt=17000)
    b = make(tmp_path / 'b', B, 0, 64, 16, 90000, minflt=18700)
    m = metrics(run_compare(a, b, '--json'))
    # the whole-process count, not the noisy system-wide one, is compared
    assert m['app_minflt']['orig'] == 17000
    assert m['app_minflt']['diff'] == pytest.approx(0.1)
    assert m['pgfault']['diff'] == pytest.approx(3.5)
    assert run_compare(a, b, '--check').returncode == 0


def test_check_falls_back_to_system_faults(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000)
    b = make(tmp_path / 'b', B, 0, 64, 16, 90000)
    m = metrics(run_compare(a, b, '--json'))
    assert m['app_minflt']['orig'] is None
    assert run_compare(a, b, '--check').returncode == 1


def test_alignment_by_rss_growth(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000)
    b = make(tmp_path / 'b', B, 700, 64, 16, 20000)
    p = run_compare(a, b, '--json')
    out = json.loads(p.stdout)
    assert out['shift_ms'] == 700
    assert out['metrics']['hot_mb_mean']['diff'] == 0


def test_check_tolerance(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000)
    close = make(tmp_path / 'close', B, 0, 60, 15, 21000)
    far = make(tmp_path / 'far', B, 0, 32, 16, 20000)
    assert run_compare(a, close, '--check').returncode == 0
    p = run_compare(a, far, '--check')
    assert p.returncode == 1
    assert 'rss_peak_mb' in p.stdout
    assert run_compare(a, far, '--check', '--tolerance', '0.6').returncode == 0


def test_table_output(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000)
    p = run_compare(a, a)
    assert p.returncode == 0
    assert 'rss_peak_mb' in p.stdout and 'hot_mb_mean' in p.stdout


def test_missing_writes_is_not_an_error(tmp_path):
    a = make(tmp_path / 'a', A, 0, 64, 16, 20000)
    m = metrics(run_compare(a, a, '--json'))
    assert m['dirty_share']['orig'] is None


def test_real_example_against_itself():
    m = metrics(run_compare(EXAMPLE, EXAMPLE, '--json'))
    assert m['rss_peak_mb']['orig'] > 290


def test_usage():
    assert run_compare().returncode == 2
