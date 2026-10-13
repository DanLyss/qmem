"""The full cycle with real DAMON through verify/cycle.sh: record -> profile
-> replay -> record -> verify. Needs root."""
import json
import subprocess

import pytest

from conftest import HOT_REGION, ROOT, RW_SPLIT, has_real_damon

CYCLE = ROOT / 'legacy' / 'verify' / 'cycle.sh'

pytestmark = pytest.mark.skipif(not has_real_damon(),
                                reason='needs root, DAMON sysfs and damo')


def run_cycle(out, *args, timeout=300):
    return subprocess.run(['bash', str(CYCLE), '-o', str(out)] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=timeout)


@pytest.mark.parametrize('workload', [
    [HOT_REGION, '256', '16', '8'],
    [RW_SPLIT, '64', '64', '6'],
], ids=['hot_region', 'rw_split'])
def test_cycle_passes_verify(tmp_path, workload):
    out = tmp_path / 'run'
    p = run_cycle(out, '-i', '250', '-w', '2000', '--', *workload)
    print(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr
    for f in ('orig', 'again', 'profile.txt', 'layout.txt', 'verify.txt', 'verify.json'):
        assert (out / f).exists(), f
    v = json.loads((out / 'verify.json').read_text())
    assert v['failed'] == []
    assert v['maps']['corr'] >= 0.9
    assert v['maps']['hot_recall'] >= 0.9


def test_cycle_with_baseline(tmp_path):
    out = tmp_path / 'run'
    p = run_cycle(out, '-i', '250', '-w', '2000', '--baseline', '--',
                  HOT_REGION, '128', '16', '5')
    print(p.stdout)
    assert p.returncode == 0, p.stdout + p.stderr
    b = json.loads((out / 'baseline.json').read_text())
    assert b['baseline'] is True and b['maps']['corr'] > 0.9
    assert 'noise of the original' in p.stdout


def test_cycle_usage(tmp_path):
    p = subprocess.run(['bash', str(CYCLE)], capture_output=True, text=True)
    assert p.returncode == 2
