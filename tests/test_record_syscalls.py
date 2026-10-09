"""R3: allocations are traced through a private tracefs instance."""
import json
import re
from pathlib import Path

import pytest

from conftest import HOT_REGION, has_tracefs, run_record


def test_without_tracefs_warns_and_continues(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--', HOT_REGION, '8', '2', '0.3'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    assert 'syscall tracing unavailable' in p.stderr
    assert not (out / 'syscalls.txt').exists()
    assert json.loads((out / 'meta.json').read_text())['syscalls'] is False


def test_no_syscalls_flag(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '--no-syscalls', '--', 'true'], env=fake_env)
    assert p.returncode == 0, p.stderr
    assert 'syscall tracing unavailable' not in p.stderr
    assert json.loads((out / 'meta.json').read_text())['syscalls'] is False


@pytest.mark.skipif(not has_tracefs(), reason='needs root and tracefs')
def test_traces_mmap_of_workload(tmp_path, fake_env):
    env = dict(fake_env)
    del env['QMEM_TRACEFS']
    out = tmp_path / 'out'
    # 64 MB = 0x4000000; the 1 s delay lets tracing start before the mmap
    p = run_record(['-o', out, '-i', '100', '--',
                    HOT_REGION, '64', '4', '0.5', '1'], env=env)
    assert p.returncode == 0, p.stderr
    text = (out / 'syscalls.txt').read_text()
    assert re.search(r'sys_mmap\(.*len: (0x)?4000000\b', text), text[-2000:]
    meta = json.loads((out / 'meta.json').read_text())
    assert meta['syscalls'] is True
    assert isinstance(meta['syscall_overrun'], int)
    assert meta['start_uptime'] > 0
    leftovers = list(Path('/sys/kernel/tracing/instances').glob('qmem-record-*'))
    assert leftovers == []
