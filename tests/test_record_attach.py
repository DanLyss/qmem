"""R5: attaching to a running process (-p) and recording duration (-d)."""
import json
import os
import subprocess
import time

from conftest import (record_json, HOT_REGION, damo_calls, kill_matching, process_running,
                      run_record)


def unique_seconds():
    # a run length nobody else uses, so pgrep finds only our workload
    return '60.%d%d' % (os.getpid(), time.time_ns() % 100000)


def test_attach_with_duration(tmp_path, fake_env):
    secs = unique_seconds()
    app = subprocess.Popen([str(HOT_REGION), '16', '2', secs],
                           stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        out = tmp_path / 'out'
        started = time.monotonic()
        p = run_record(['-o', out, '-i', '100', '-d', '1.5', '-p', app.pid],
                       env=fake_env, timeout=30)
        elapsed = time.monotonic() - started
        assert p.returncode == 0, p.stderr
        assert 1.0 < elapsed < 10
        rec = [c for c in damo_calls(fake_env) if c[:1] == ['record']][0]
        assert rec[-1] == str(app.pid)
        assert len(list((out / 'smaps').iterdir())) >= 5
        meta = record_json(out)
        assert meta['target']['mode'] == 'attach'
        assert meta['target']['pid'] == app.pid
        assert meta['stop']['reason'] == 'duration'
        assert meta['stop']['interrupted'] is False
        # attach mode leaves the process running
        assert app.poll() is None
    finally:
        app.kill()
        app.wait()


def test_attach_rejects_command(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out', '-p', '1', '--', 'true'],
                   env=fake_env)
    assert p.returncode == 2


def test_attach_to_missing_process(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out', '-p', '999999999'], env=fake_env)
    assert p.returncode != 0
    assert 'no such process' in p.stderr


def test_rejects_bad_duration(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out', '-d', 'soon', '--', 'true'],
                   env=fake_env)
    assert p.returncode == 2


def test_launch_with_duration_stops_application(tmp_path, fake_env):
    secs = unique_seconds()
    out = tmp_path / 'out'
    try:
        started = time.monotonic()
        p = run_record(['-o', out, '-i', '100', '-d', '1', '--',
                        HOT_REGION, '16', '2', secs], env=fake_env, timeout=30)
        assert p.returncode == 0, p.stderr
        assert time.monotonic() - started < 10
        meta = record_json(out)
        assert meta['target']['mode'] == 'launch'
        assert meta['stop']['reason'] == 'duration'
        time.sleep(0.5)
        assert not process_running('hot_region 16 2 ' + secs)
    finally:
        kill_matching('hot_region 16 2 ' + secs)


def test_stop_reason_exited(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '--', HOT_REGION, '8', '2', '0.3'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    meta = record_json(out)
    assert meta['stop']['reason'] == 'exited'
    assert meta['target']['mode'] == 'launch'
