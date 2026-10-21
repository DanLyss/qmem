"""Attaching to a running process (-p); Ctrl+C stops the recording."""
import os
import subprocess
import time

from conftest import (record_json, HOT_REGION, damo_calls, run_record, signal, start_record)


def unique_seconds():
    # a run length nobody else uses, so pgrep finds only our workload
    return '60.%d%d' % (os.getpid(), time.time_ns() % 100000)


def test_attach_until_interrupted(tmp_path, fake_env):
    secs = unique_seconds()
    app = subprocess.Popen([str(HOT_REGION), '16', '2', secs],
                           stdout=subprocess.DEVNULL)
    try:
        time.sleep(0.3)
        out = tmp_path / 'out'
        rec = start_record(['-o', out, '-i', '100', '-p', app.pid], env=fake_env)
        time.sleep(1.5)
        os.kill(rec.pid, signal.SIGINT)
        rec.wait(timeout=30)
        assert rec.returncode == 0, rec.stderr.read()
        call = [c for c in damo_calls(fake_env) if c[:1] == ['record']][0]
        assert call[-1] == str(app.pid)
        assert len(list((out / 'smaps').iterdir())) >= 5
        meta = record_json(out)
        assert meta['target']['mode'] == 'attach'
        assert meta['target']['pid'] == app.pid
        assert meta['stop']['reason'] == 'interrupted'
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


def test_stop_reason_exited(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '--', HOT_REGION, '8', '2', '0.3'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    meta = record_json(out)
    assert meta['stop']['reason'] == 'exited'
    assert meta['target']['mode'] == 'launch'
