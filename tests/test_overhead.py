"""R7: tools/measure-overhead.sh compares a command with and without recording."""
import re
import subprocess

from conftest import HOT_REGION, OVERHEAD


def run_overhead(args, env):
    return subprocess.run(['bash', str(OVERHEAD)] + [str(a) for a in args],
                          env=env, capture_output=True, text=True, timeout=120)


def test_reports_baseline_recorded_and_overhead(fake_env):
    p = run_overhead(['-n', '2', '-r', '-i 100', '--',
                      HOT_REGION, '16', '4', '0.5'], fake_env)
    assert p.returncode == 0, p.stderr
    vals = dict(re.findall(r'^(\w+) (-?[0-9.]+)$', p.stdout, re.M))
    assert set(vals) >= {'runs', 'baseline_s', 'recorded_s', 'overhead_pct'}
    assert int(vals['runs']) == 2
    base, rec = float(vals['baseline_s']), float(vals['recorded_s'])
    assert base >= 0.5 and rec >= 0.5
    assert abs(float(vals['overhead_pct']) - (rec - base) / base * 100) < 0.5


def test_usage(fake_env):
    p = run_overhead([], fake_env)
    assert p.returncode == 2
    p = run_overhead(['-n', 'x', '--', 'true'], fake_env)
    assert p.returncode == 2
