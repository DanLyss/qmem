"""record v2: one subdirectory per source, record.json, the source interface,
--sources, PMU stubs. See docs/formats/record.md."""
import pytest

from conftest import HOT_REGION, damo_calls, has_tracefs, record_json, run_record

SOURCES = ['damon', 'smaps', 'proc', 'system', 'softdirty', 'syscalls',
           'pmu_count', 'pmu_sample']


@pytest.fixture
def recorded(tmp_path, fake_env):
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--', HOT_REGION, '16', '2', '1'],
                   env=fake_env)
    assert p.returncode == 0, p.stderr
    return out, record_json(out)


def test_manifest(recorded):
    out, r = recorded
    assert r['format'] == 'qmem-record' and r['version'] == 2
    assert r['target']['mode'] == 'launch' and r['target']['pid'] > 0
    assert set(r['sources']) == set(SOURCES)
    for name in ('damon', 'smaps', 'proc', 'system'):
        src = r['sources'][name]
        assert src['status'] == 'ok', name
        assert isinstance(src['version'], int)
        for f in src['files']:
            assert (out / f).exists(), (name, f)
    assert r['sources']['softdirty']['status'] == 'ok'
    assert r['sources']['syscalls']['status'] == 'unavailable'    # fake tracefs
    for name in ('pmu_count', 'pmu_sample'):
        assert r['sources'][name]['status'] in ('unavailable', 'stub')
        assert r['sources'][name].get('reason')


def test_layout(recorded):
    out, _ = recorded
    assert (out / 'damon' / 'access.csv').is_file()
    assert (out / 'damon' / 'damon.data').is_file()
    assert (out / 'proc' / 'proc.csv').is_file()
    assert (out / 'system' / 'system.csv').is_file()
    assert len(list((out / 'smaps').iterdir())) >= 5
    # nothing from the flat v1 layout
    for name in ('meta.json', 'access.csv', 'proc.csv', 'system.csv'):
        assert not (out / name).exists(), name


def test_sources_option_without_damon(tmp_path, fake_env):
    """Without damon the driver launches the application itself."""
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--sources', 'smaps,proc', '--',
                    HOT_REGION, '16', '2', '1'], env=fake_env)
    assert p.returncode == 0, p.stderr
    assert damo_calls(fake_env) == []
    r = record_json(out)
    assert r['sources']['damon']['status'] == 'off'
    assert r['sources']['system']['status'] == 'off'
    assert r['sources']['smaps']['status'] == 'ok'
    assert r['stop']['reason'] == 'exited'
    assert len(list((out / 'smaps').iterdir())) >= 5
    assert not (out / 'damon').exists()


def test_unknown_source(tmp_path, fake_env):
    p = run_record(['-o', tmp_path / 'out', '--sources', 'smaps,nonsense', '--', 'true'],
                   env=fake_env)
    assert p.returncode == 2
    assert 'nonsense' in p.stderr


def test_one_clock(recorded, tmp_path):
    out, r = recorded
    assert r['time']['clock'] == 'boot'
    assert r['time']['start_ms'] > 0
    assert not (out / 'clock.csv').exists() and not (out / 'pauses.csv').exists()
    # the damon source set the trace clock to mono and put the old one back
    # (a real tracefs lists all clocks and brackets the current one)
    assert (tmp_path / 'tracefs' / 'trace_clock').read_text().strip() == 'local'


def test_damon_needs_the_trace_clock(tmp_path, fake_env):
    (tmp_path / 'tracefs' / 'trace_clock').unlink()
    out = tmp_path / 'out'
    p = run_record(['-o', out, '--', HOT_REGION, '8', '2', '0.3'], env=fake_env)
    assert p.returncode == 0, p.stderr
    damon = record_json(out)['sources']['damon']
    assert damon['status'] == 'unavailable' and 'trace clock' in damon['reason']
    assert damo_calls(fake_env) == []


@pytest.mark.skipif(not has_tracefs(), reason='needs root and tracefs')
def test_trace_clock_is_put_back(tmp_path, fake_env):
    env = dict(fake_env)
    del env['QMEM_TRACEFS']
    before = open('/sys/kernel/tracing/trace_clock').read()
    out = tmp_path / 'out'
    p = run_record(['-o', out, '-i', '100', '--', HOT_REGION, '16', '2', '1'], env=env)
    assert p.returncode == 0, p.stderr
    assert open('/sys/kernel/tracing/trace_clock').read() == before
