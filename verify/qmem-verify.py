#!/usr/bin/env python3
"""Compare the record of an original run with the record of its replay.

usage: qmem-verify.py <original record> <replay record> [--json] [--check]
                      [--tolerance 0.2]

Compares what does not depend on addresses: peak anonymous RSS, the amount
of memory DAMON saw accessed, the application's page faults (proc.csv;
system-wide ones as a fallback), swap, the share of written pages (without
the first soft-dirty sample, which holds start-up writes). Time is aligned by
the first growth of anonymous RSS (to 10 % of its peak). --check exits with 1
if peak RSS, accessed memory or page faults differ by more than the
tolerance.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from mkprofile import read_access, read_smaps  # noqa: E402

MB = 1 << 20
CHECKED = ('rss_peak_mb', 'hot_mb_mean')


def measure(d):
    smaps, _ = read_smaps(d)
    rss = [(ts, sum(r[2] for r in vmas)) for ts, _, vmas in smaps]
    peak = max(r for _, r in rss)
    t0 = next(ts for ts, r in rss if r >= 0.1 * peak)

    hot, hot50 = [], []
    for start_ms, _, regions in read_access(d):
        if start_ms < t0:
            continue
        hot.append(sum(e - s for s, e, rate in regions if rate > 0) / MB)
        hot50.append(sum(e - s for s, e, rate in regions if rate > 50) / MB)

    system = list(csv.DictReader(open(d / 'system.csv')))

    def delta(col):
        return int(system[-1][col]) - int(system[0][col]) if system else None

    app_minflt = app_majflt = None
    if (d / 'proc.csv').exists():
        rows = list(csv.DictReader(open(d / 'proc.csv')))
        if rows:
            app_minflt = int(rows[-1]['minflt'])
            app_majflt = int(rows[-1]['majflt'])

    dirty = None
    if (d / 'writes.csv').exists():
        rows = list(csv.DictReader(open(d / 'writes.csv')))
        first = min((int(r['ts_ms']) for r in rows), default=None)
        rows = [r for r in rows if int(r['ts_ms']) != first]
        present = sum(int(r['present_pages']) for r in rows)
        if present:
            dirty = sum(int(r['dirty_pages']) for r in rows) / present

    return t0, {
        'rss_peak_mb': peak / MB,
        'hot_mb_mean': sum(hot) / len(hot) if hot else 0.0,
        'hot50_mb_mean': sum(hot50) / len(hot50) if hot50 else 0.0,
        'app_minflt': app_minflt,
        'app_majflt': app_majflt,
        'pgfault': delta('pgfault'),
        'pgmajfault': delta('pgmajfault'),
        'pswpout': delta('pswpout'),
        'dirty_share': dirty,
    }


def diff(orig, replay):
    if orig is None or replay is None:
        return None
    if orig == 0:
        return 0.0 if replay == 0 else None
    return (replay - orig) / orig


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('original')
    ap.add_argument('replay')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--tolerance', type=float, default=0.2)
    args = ap.parse_args()

    t0_orig, orig = measure(Path(args.original))
    t0_rep, rep = measure(Path(args.replay))
    metrics = {name: {'orig': orig[name], 'replay': rep[name],
                      'diff': diff(orig[name], rep[name])} for name in orig}
    # the application's own faults when both records have them
    faults = 'app_minflt' if metrics['app_minflt']['diff'] is not None else 'pgfault'
    failed = [n for n in CHECKED + (faults,) if metrics[n]['diff'] is not None
              and abs(metrics[n]['diff']) > args.tolerance]

    if args.json:
        print(json.dumps({'shift_ms': t0_rep - t0_orig, 'metrics': metrics},
                         indent=2))
    else:
        fmt = lambda v: '-' if v is None else ('%.3f' % v if v < 10 else '%.1f' % v)
        print('%-15s %12s %12s %9s' % ('metric', 'original', 'replay', 'diff'))
        for name, m in metrics.items():
            print('%-15s %12s %12s %9s%s' % (
                name, fmt(m['orig']), fmt(m['replay']),
                '-' if m['diff'] is None else '%+.1f%%' % (m['diff'] * 100),
                '  FAIL' if name in failed and args.check else ''))
        print('replay starts %+d ms later than the original' % (t0_rep - t0_orig))
    if args.check and failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
