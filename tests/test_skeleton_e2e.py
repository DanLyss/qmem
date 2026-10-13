"""The skeleton end to end: record -> build -> replay (docs/skeleton.md, step
5). Only that every stage runs and leaves its files is checked, not how
close the replay is."""
import re
import subprocess
import sys

import pytest

from conftest import BUILD, HOT_REGION, REPLAY, has_real_damon, record_json, run_record

MB = 1 << 20


def build_and_replay(rec, tmp_path):
    prof = tmp_path / 'profile.txt'
    b = subprocess.run([sys.executable, str(BUILD), str(rec), '-o', str(prof)],
                       capture_output=True, text=True, timeout=120)
    assert b.returncode == 0, b.stderr
    text = prof.read_text()
    assert text.startswith('qmem-profile 2\n')
    stats = tmp_path / 'stats.csv'
    r = subprocess.run([str(REPLAY), str(prof), '--stats', str(stats)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    summary = dict(re.findall(r'(\w+)=(\S+)', r.stderr.splitlines()[-1]))
    assert stats.read_text().startswith('t_ms,planned,done,lag_ms')
    return text, summary


def test_with_fake_damo(tmp_path, fake_env):
    rec = tmp_path / 'rec'
    p = run_record(['-o', rec, '-i', '100', '--', HOT_REGION, '16', '2', '1'], env=fake_env)
    assert p.returncode == 0, p.stderr
    assert record_json(rec)['version'] == 2
    text, summary = build_and_replay(rec, tmp_path)
    # the 16 MB of hot_region are a region of the profile
    sizes = [int(m) for m in re.findall(r'^region .*size=(\d+)', text, re.M)]
    assert any(s >= 16 * MB for s in sizes), sizes
    assert int(summary['populated']) > 0


def test_without_damon(tmp_path, fake_env):
    rec = tmp_path / 'rec'
    p = run_record(['-o', rec, '-i', '100', '--sources', 'smaps,proc', '--',
                    HOT_REGION, '16', '2', '1'], env=fake_env)
    assert p.returncode == 0, p.stderr
    text, summary = build_and_replay(rec, tmp_path)
    assert 'epoch ' not in text
    assert int(summary['planned']) == 0


@pytest.mark.skipif(not has_real_damon(), reason='needs root and DAMON')
def test_with_real_damon(tmp_path):
    rec = tmp_path / 'rec'
    p = run_record(['-o', rec, '-i', '200', '-w', '500', '--', HOT_REGION, '32', '4', '3'],
                   timeout=60)
    assert p.returncode == 0, p.stderr
    sources = record_json(rec)['sources']
    for name in ('damon', 'smaps', 'proc', 'system', 'softdirty', 'syscalls'):
        assert sources[name]['status'] == 'ok', name
    text, summary = build_and_replay(rec, tmp_path)
    assert re.search(r'^epoch t=\d+ dur=100$', text, re.M)
    assert re.search(r'^access .* pattern=roundrobin$', text, re.M)
    assert int(summary['epochs']) > 0
    assert int(summary['done']) > 0
