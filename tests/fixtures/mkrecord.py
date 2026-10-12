"""Build small qmem-record directories with a known content for tests."""
import json

MB = 1 << 20
PAGE = 4096


def write_record(d, smaps, access, system=None, writes=None, sample_us='5 ms',
                 softdirty_ms=0, proc=None, start_uptime=100.0):
    """Write a record directory.

    smaps:  {ts_ms: [(start, end, name, rss_kb[, vmflags]), ...]}; name '' is
            anonymous; vmflags defaults to 'rd wr mr mw me ac'
    access: [(start_ns, end_ns, [(start, end, access_rate), ...]), ...]
    system: [(ts_ms, pgfault, pgmajfault, pswpout), ...]
    writes: [(ts_ms, start, end, present_pages, dirty_pages), ...]
    proc:   [(ts_ms, minflt, majflt, rss_kb), ...]
    """
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

    rows = ['snapshot_start_ns,snapshot_end_ns,start,end,access_rate,access_hz,age']
    for s_ns, e_ns, regions in access:
        for start, end, rate in regions:
            rows.append('%d,%d,%d,%d,%f,%f,0' % (s_ns, e_ns, start, end, rate,
                                                 rate * 2))
    (d / 'access.csv').write_text('\n'.join(rows) + '\n')

    rows = ['ts_ms,MemFree,MemAvailable,Cached,SwapFree,psi_some_avg10,'
            'psi_full_avg10,pgfault,pgmajfault,pswpin,pswpout']
    for ts, pf, pm, so in system or []:
        rows.append('%d,1000000,1000000,1000,0,0.00,0.00,%d,%d,0,%d' % (ts, pf, pm, so))
    (d / 'system.csv').write_text('\n'.join(rows) + '\n')

    if proc is not None:
        rows = ['ts_ms,minflt,majflt,rss_kb'] + ['%d,%d,%d,%d' % r for r in proc]
        (d / 'proc.csv').write_text('\n'.join(rows) + '\n')

    if writes is not None:
        rows = ['ts_ms,start,end,present_pages,dirty_pages']
        rows += ['%d,%d,%d,%d,%d' % w for w in writes]
        (d / 'writes.csv').write_text('\n'.join(rows) + '\n')

    (d / 'damon.data.kdamonds').write_text(json.dumps(
        [{'contexts': [{'intervals': {'sample_us': sample_us,
                                      'aggr_us': '100 ms'}}]}]))
    (d / 'meta.json').write_text(json.dumps({
        'mode': 'launch', 'command': 'test', 'pid': 1, 'kernel_pid': 1,
        'start_time': 1.0, 'end_time': 5.0, 'start_uptime': start_uptime,
        'interval_ms': 1000, 'softdirty_ms': softdirty_ms,
        'stop_reason': 'exited'}))
    return d
