"""Build small qmem-record directories with a known content for tests."""
import json

MB = 1 << 20
PAGE = 4096


def write_record(d, smaps, access, system=None, sample_us='5 ms', proc=None):
    """Write a record v2 directory: a subdirectory per source and record.json.

    smaps:  {ts_ms: [(start, end, name, rss_kb[, vmflags]), ...]}; name '' is
            anonymous; vmflags defaults to 'rd wr mr mw me ac'
    access: [(start_ns, end_ns, [(start, end, access_rate), ...]), ...];
            None: no damon source
    system: [(ts_ms, pgfault, pgmajfault, pswpout), ...]
    proc:   [(ts_ms, minflt, majflt, rss_kb), ...]
    """
    def at(source, name):
        (d / source).mkdir(exist_ok=True)
        return d / source / name

    d.mkdir(parents=True, exist_ok=True)
    (d / 'smaps').mkdir(exist_ok=True)
    for ts, vmas in smaps.items():
        lines = []
        for vma in vmas:
            start, end, name, rss = vma[:4]
            flags = vma[4] if len(vma) > 4 else 'rd wr mr mw me ac'
            lines.append('%x-%x rw-p 00000000 %s %s' % (
                start, end, '08:01 1234' if name and not name.startswith('[')
                else '00:00 0', name))
            lines += ['Size: %d kB' % ((end - start) // 1024),
                      'Rss: %d kB' % rss,
                      'Anonymous: %d kB' % (0 if name and not name.startswith('[')
                                            else rss),
                      'VmFlags: ' + flags]
        (d / 'smaps' / ('%d.txt' % ts)).write_text('\n'.join(lines) + '\n')

    if access is not None:
        rows = ['snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age']
        for s_ns, e_ns, regions in access:
            for start, end, rate in regions:
                rows.append('%d,%d,%d,%d,%f,%f,0' % (s_ns, e_ns, start, end, rate,
                                                     rate * 2))
        at('damon', 'access.csv').write_text('\n'.join(rows) + '\n')
        at('damon', 'damon.data.kdamonds').write_text(json.dumps(
            [{'contexts': [{'intervals': {'sample_us': sample_us,
                                          'aggr_us': '100 ms'}}]}]))

    rows = ['ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,'
            'psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout']
    for ts, pf, pm, so in system or []:
        rows.append('%d,1000000,1000000,1000,0,0.00,0.00,%d,%d,0,%d' % (ts, pf, pm, so))
    at('system', 'system.csv').write_text('\n'.join(rows) + '\n')

    if proc is not None:
        rows = ['ts_ms,minflt,majflt,rss_kb'] + ['%d,%d,%d,%d' % r for r in proc]
        at('proc', 'proc.csv').write_text('\n'.join(rows) + '\n')

    def ok(files, **params):
        return dict(status='ok', version=1, files=files, **params)
    sources = {name: {'status': 'off'} for name in
               ('damon', 'smaps', 'proc', 'system')}
    sources['smaps'] = ok(['smaps/'], interval_ms=1000)
    sources['system'] = ok(['system/system.csv'])
    if access is not None:
        sources['damon'] = ok(['damon/access.csv'], damo_version='test')
    if proc is not None:
        sources['proc'] = ok(['proc/proc.csv'])
    (d / 'record.json').write_text(json.dumps({
        'format': 'qmem-record', 'version': 2,
        'target': {'command': 'test', 'pid': 1},
        'time': {'clock': 'boot', 'start_ms': 100000, 'start_time': 1.0, 'end_time': 5.0},
        'stop': {'reason': 'exited', 'interrupted': False},
        'kernel': 'test', 'interval_ms': 1000, 'duration_s': None,
        'sources': sources}, indent=2))
    return d
