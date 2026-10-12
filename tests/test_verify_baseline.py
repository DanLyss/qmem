"""V5: the noise between two runs of the original (--baseline) and
thresholds relative to it (--noise)."""
import json

import pytest

from test_verify_maps import A, B, SIZE, case, record, run_verify, verify_json


def baseline(tmp_path, rate2=90):
    o1 = record(tmp_path / 'o1', A, 0, 8, 8, 100, 1000, 3000, 0, 100.0)
    o2 = record(tmp_path / 'o2', B, 0, 8, 8, rate2, 1000, 3000, 0, 300.0)
    p = run_verify('--baseline', o1, o2, '--json')
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout)


def test_baseline_compares_two_originals(tmp_path):
    out = baseline(tmp_path)
    assert out['baseline'] is True
    assert out['maps']['mae_pp'] == pytest.approx(10, abs=0.5)
    assert out['maps']['corr'] > 0.99
    assert out['maps']['hot_recall'] == pytest.approx(1)
    assert [r['region'] for r in out['regions']] == [0]


def test_baseline_usage(tmp_path):
    o1 = record(tmp_path / 'o1', A, 0, 8, 8, 100, 1000, 3000, 0, 100.0)
    assert run_verify('--baseline', o1).returncode == 2


def test_noise_relaxes_thresholds(tmp_path):
    orig, again, profile, layout = case(tmp_path / 'c', replay_rate=50)
    args = [orig, again, '--profile', profile, '--layout', layout, '--check']
    assert run_verify(*args).returncode == 1           # mae 50 > 10
    noise = tmp_path / 'noise.json'
    noise.write_text(json.dumps(baseline(tmp_path / 'b', rate2=70)))  # mae 30
    p = run_verify(*args, '--noise', noise)
    assert p.returncode == 0, p.stdout                  # limit 2 x 30 = 60
    assert 'noise' in p.stdout


def test_noise_never_tightens_defaults(tmp_path):
    orig, again, profile, layout = case(tmp_path / 'c', replay_rate=92)
    noise = tmp_path / 'noise.json'
    noise.write_text(json.dumps(baseline(tmp_path / 'b', rate2=100)))  # no noise
    p = run_verify(orig, again, '--profile', profile, '--layout', layout,
                   '--check', '--noise', noise)
    assert p.returncode == 0, p.stdout                  # mae 8 <= default 10
