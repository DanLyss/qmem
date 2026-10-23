import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RECORD = ROOT / 'record' / 'qmem-record.sh'
HOT_REGION = ROOT / 'workloads' / 'hot_region'
FAKE_DAMO = ROOT / 'tests' / 'fake_damo.py'
MKDAMON = ROOT / 'tests' / 'fixtures' / 'mkdamon.py'
RW_SPLIT = ROOT / 'workloads' / 'rw_split'
FIXED_WORK = ROOT / 'workloads' / 'fixed_work'
RANDOM_ACCESS = ROOT / 'workloads' / 'random_access'
OVERHEAD = ROOT / 'tools' / 'measure-overhead.sh'
REPLAY = ROOT / 'replay' / 'qmem-replay'
BUILD = ROOT / 'builder' / 'qmem-build.py'

ACCESS_HEADER = 'snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age'
SYSTEM_HEADER = ('ts_ms,MemFree,MemAvailable,Cached,SwapFree,'
                 'psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout')
PROC_HEADER = 'ts_ms,minflt,majflt,rss_kb'


def has_real_damon():
    return (os.geteuid() == 0
            and Path('/sys/kernel/mm/damon/admin').is_dir()
            and shutil.which('damo') is not None)


def has_tracefs():
    return os.geteuid() == 0 and Path('/sys/kernel/tracing/instances').is_dir()


def has_damo():
    return shutil.which('damo') is not None


@pytest.fixture(scope='session', autouse=True)
def build_workloads():
    subprocess.run(['make', '-C', str(ROOT), 'all'], check=True,
                   stdout=subprocess.DEVNULL)


@pytest.fixture
def fake_env(tmp_path):
    """Environment where qmem-record.sh talks to tests/fake_damo.py."""
    env = dict(os.environ)
    env['QMEM_DAMO'] = '%s %s' % (sys.executable, FAKE_DAMO)
    env['FAKE_DAMO_LOG'] = str(tmp_path / 'damo.log')
    # a tracefs with only the trace clock the damon source sets: no root needed
    tracefs = tmp_path / 'tracefs'
    tracefs.mkdir()
    (tracefs / 'trace_clock').write_text('[local] global mono\n')
    env['QMEM_TRACEFS'] = str(tracefs)
    return env


def run_record(args, env=None, timeout=60):
    """Run qmem-record.sh. With `-- <command>` in args the command is started
    first, as a user would, and recorded with -p <its pid>; its stdout comes
    first in the result's stdout."""
    args = [str(a) for a in args]
    if '--' not in args:
        return subprocess.run(['bash', str(RECORD)] + args, env=env,
                              capture_output=True, text=True, timeout=timeout)
    import threading
    i = args.index('--')
    app = subprocess.Popen(args[i + 1:], stdout=subprocess.PIPE,
                           stderr=subprocess.DEVNULL, text=True)
    # reap the application as soon as it exits, as a user's shell would: a
    # zombie still looks alive to damo
    app_out = []
    reaper = threading.Thread(target=lambda: app_out.append(app.communicate()[0]))
    reaper.start()
    try:
        p = subprocess.run(['bash', str(RECORD)] + args[:i] + ['-p', str(app.pid)],
                           env=env, capture_output=True, text=True, timeout=timeout)
    finally:
        if app.poll() is None:
            app.kill()
        reaper.join()
    return subprocess.CompletedProcess(p.args, p.returncode, app_out[0] + p.stdout, p.stderr)


def start_record(args, env=None):
    return subprocess.Popen(['bash', str(RECORD)] + [str(a) for a in args],
                            env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            start_new_session=True)


def record_json(out):
    import json
    return json.loads((Path(out) / 'record.json').read_text())


def damo_calls(env):
    import json
    path = Path(env['FAKE_DAMO_LOG'])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def process_running(pattern):
    return subprocess.run(['pgrep', '-f', pattern],
                          stdout=subprocess.DEVNULL).returncode == 0


def kill_matching(pattern):
    subprocess.run(['pkill', '-KILL', '-f', pattern])


__all__ = ['ROOT', 'RECORD', 'HOT_REGION', 'MKDAMON', 'RW_SPLIT', 'FIXED_WORK', 'RANDOM_ACCESS',
           'OVERHEAD', 'REPLAY', 'BUILD', 'ACCESS_HEADER', 'SYSTEM_HEADER', 'PROC_HEADER',
           'has_real_damon', 'has_tracefs', 'has_damo', 'run_record',
           'start_record', 'record_json', 'damo_calls', 'process_running', 'kill_matching',
           'signal']
