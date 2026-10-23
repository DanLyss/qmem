"""R1: qmem-record.sh runs damo record and snapshots smaps.

These tests use tests/fake_damo.py instead of damo, so they need neither root
nor DAMON.
"""
import json
import os
import platform
import time

import pytest

from conftest import (record_json, HOT_REGION, damo_calls, kill_matching, process_running,
                      run_record, signal, start_record)


@pytest.fixture
def recorded(tmp_path, fake_env):
    """Record hot_region for ~1.5 s with 100 ms snapshots."""
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--',
                    HOT_REGION, '32', '4', '1.5'], env=fake_env)
    return p, out, fake_env


def test_usage_without_args(fake_env):
    p = run_record([], env=fake_env)
    assert p.returncode == 2
    assert 'usage' in p.stderr.lower()


def test_requires_output_dir(fake_env):
    p = run_record(['-p', '1'], env=fake_env)
    assert p.returncode == 2


def test_requires_pid(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out'], env=fake_env)
    assert p.returncode == 2


def test_rejects_bad_interval(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out', '-i', 'abc', '-p', '1'], env=fake_env)
    assert p.returncode == 2


def test_refuses_nonempty_output_dir(tmp_path, fake_env):
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'keep').write_text('x')
    p = run_record(['-o', out, '-p', '1'], env=fake_env)
    assert p.returncode != 0
    assert (out / 'keep').read_text() == 'x'
    assert not (out / 'damon' / 'damon.data').exists()


def test_runs_damo_record_with_access_only(recorded):
    p, out, env = recorded
    assert p.returncode == 0, p.stderr
    records = [c for c in damo_calls(env) if c[:1] == ['record']]
    assert len(records) == 1
    args = records[0]
    assert args[1:5] == ['--do_record', 'access', '-o', str(out / 'damon' / 'damon.data')]
    # the driver starts the application, damo follows it by pid
    assert args[-1] == str(record_json(out)['target']['pid'])
    assert (out / 'damon' / 'damon.data').is_file()


def test_smaps_snapshots(recorded):
    p, out, _ = recorded
    assert p.returncode == 0, p.stderr
    snaps = sorted((out / 'smaps').iterdir(), key=lambda f: int(f.stem))
    assert len(snaps) >= 5
    stamps = [int(f.stem) for f in snaps]
    assert stamps == sorted(stamps) and stamps[0] >= 0
    texts = [f.read_text() for f in snaps]
    assert all('Rss:' in t for t in texts if t)
    # the 32 MB mapping of hot_region shows up in some snapshot
    sizes = [int(line.split()[1]) for t in texts for line in t.splitlines()
             if line.startswith('Size:')]
    assert max(sizes) >= 32 * 1024


def test_meta_json(recorded):
    p, out, _ = recorded
    assert p.returncode == 0, p.stderr
    meta = record_json(out)
    assert meta['target']['command'].endswith('hot_region 32 4 1.5')
    assert meta['interval_ms'] == 100
    assert meta['sources']['damon']['damo_version'] == '3.3.0'
    assert meta['kernel'] == platform.release()
    assert meta['time']['end_time'] >= meta['time']['start_time'] > 0
    assert meta['stop']['interrupted'] is False
