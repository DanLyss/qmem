#!/usr/bin/env python3
"""Print a short summary of a qmem-record directory.

usage: summarize-record.py <record dir>
"""
import collections
import csv
import json
import re
import sys
from pathlib import Path

MB = 1 << 20


def section(title):
    print('\n== ' + title)


def summarize_meta(d):
    meta = json.loads((d / 'meta.json').read_text())
    print('mode %s, pid %s (kernel %s), %.1f s, stop: %s' % (
        meta['mode'], meta['pid'], meta.get('kernel_pid'),
        meta['end_time'] - meta['start_time'], meta['stop_reason']))
    print('command: ' + meta['command'])
    return meta


def summarize_system(d):
    section('system.csv: memory over time')
    rows = list(csv.DictReader(open(d / 'system.csv')))
    if not rows:
        return
    pf0 = int(rows[0]['pgfault'])
    for r in rows:
        print('t=%5d ms  MemFree %6.0f MB  pgfault +%-7d pgmajfault %s  psi %s' % (
            int(r['ts_ms']), int(r['MemFree']) / 1024,
            int(r['pgfault']) - pf0, r['pgmajfault'], r['psi_some_avg10']))


def summarize_smaps(d):
    snaps = sorted((d / 'smaps').glob('*.txt'), key=lambda f: int(f.stem))
    section('smaps: RSS by kind (%d snapshots)' % len(snaps))
    for f in snaps:
        rss = collections.Counter()
        kind = None
        for line in f.read_text().splitlines():
            m = re.match(r'^[0-9a-f]+-[0-9a-f]+ \S+ \S+ \S+ \S+\s*(.*)$', line)
            if m:
                name = m[1].strip()
                kind = 'anon' if not name else name if name.startswith('[') else 'file'
            elif line.startswith('Rss:') and kind:
                rss[kind] += int(line.split()[1])
        print('t=%5d ms  ' % int(f.stem) + '  '.join(
            '%s %.1f MB' % (k, v / 1024) for k, v in sorted(rss.items())
            if v >= 100))


def summarize_access(d):
    section('access.csv: DAMON per second')
    rows = list(csv.DictReader(open(d / 'access.csv')))
    by_sec = collections.defaultdict(list)
    for r in rows:
        by_sec[int(r['snapshot_start_ns']) // 10**9].append(r)
    for sec in sorted(by_sec):
        rs = by_sec[sec]
        n = len({r['snapshot_start_ns'] for r in rs})
        size = lambda r: int(r['end']) - int(r['start'])
        total = sum(size(r) for r in rs) / n / MB
        accessed = sum(size(r) for r in rs if float(r['access_rate']) > 0) / n / MB
        top = max(rs, key=lambda r: (float(r['access_rate']), size(r)))
        print('t=%ds  monitored %6.1f MB  accessed %6.1f MB  hottest %5.1f MB at %5.1f%%' % (
            sec, total, accessed, size(top) / MB, float(top['access_rate'])))


def summarize_writes(d):
    path = d / 'writes.csv'
    if not path.exists():
        return
    section('writes.csv: mappings over 1 MB')
    for r in csv.DictReader(open(path)):
        present = int(r['present_pages']) * 4 / 1024
        if present > 1:
            print('t=%5d ms  %x-%x  present %6.1f MB  dirty %6.1f MB' % (
                int(r['ts_ms']), int(r['start']), int(r['end']), present,
                int(r['dirty_pages']) * 4 / 1024))


def summarize_syscalls(d, meta):
    path = d / 'syscalls.txt'
    if not path.exists():
        return
    section('syscalls.txt')
    calls = collections.Counter()
    first = last = None
    for line in path.read_text().splitlines():
        m = re.search(r' (\d+\.\d+): sys_(\w+)\(', line)
        if m:
            calls[m[2]] += 1
            t = float(m[1]) - meta['start_uptime']
            first = t if first is None else first
            last = t
    for name, n in calls.most_common():
        print('%-8s %d' % (name, n))
    if first is not None:
        print('from %.2f s to %.2f s after the start' % (first, last))


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__.strip())
    d = Path(sys.argv[1])
    meta = summarize_meta(d)
    summarize_system(d)
    summarize_smaps(d)
    summarize_access(d)
    summarize_writes(d)
    summarize_syscalls(d, meta)


if __name__ == '__main__':
    main()
