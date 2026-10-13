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
SOFTDIRTY = ROOT / 'record' / 'qmem-softdirty'
OVERHEAD = ROOT / 'tools' / 'measure-overhead.sh'
REPLAY = ROOT / 'replay' / 'qmem-replay'
MKPROFILE = ROOT / 'tools' / 'mkprofile.py'
VERIFY = ROOT / 'legacy' / 'verify' / 'qmem-verify.py'
LEGACY_MKPROFILE = ROOT / 'legacy' / 'tools' / 'mkprofile.py'
EXAMPLE = ROOT / 'examples' / 'python_phases' / 'record'

ACCESS_HEADER = 'snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age'
SYSTEM_HEADER = ('ts_ms,MemFree,MemAvailable,Cached,SwapFree,'
                 'psi_some_avg10,psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout')
WRITES_HEADER = 'ts_ms,start,end,present_pages,dirty_pages,rewritten_pages'
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
    # no tracefs unless a test asks for it, so results do not depend on root
    env['QMEM_TRACEFS'] = str(tmp_path / 'no-tracefs')
    return env


def run_record(args, env=None, timeout=60):
    return subprocess.run(['bash', str(RECORD)] + [str(a) for a in args],
                          env=env, capture_output=True, text=True,
                          timeout=timeout)


def start_record(args, env=None):
    return subprocess.Popen(['bash', str(RECORD)] + [str(a) for a in args],
                            env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True,
                            start_new_session=True)


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


__all__ = ['ROOT', 'RECORD', 'HOT_REGION', 'MKDAMON', 'RW_SPLIT', 'FIXED_WORK', 'RANDOM_ACCESS', 'SOFTDIRTY',
           'OVERHEAD', 'REPLAY', 'MKPROFILE', 'LEGACY_MKPROFILE', 'VERIFY', 'EXAMPLE', 'ACCESS_HEADER', 'SYSTEM_HEADER', 'WRITES_HEADER', 'PROC_HEADER',
           'has_real_damon', 'has_tracefs', 'has_damo', 'run_record',
           'start_record', 'damo_calls', 'process_running', 'kill_matching',
           'signal']
