#!/usr/bin/env python3
"""Compare qmem-replay with damoLoad's memtest on the same workload.

usage: damoload_check.py <config.json>... [--duration S] [--window-ms 100]
                         [--epoch-ms 10] [--keep DIR] [--json FILE]

memtest (damoLoad, taken as correct) runs the config and logs every touch
(gt.log). The same config becomes a profile (memtest2profile.py), and
qmem-replay runs it with --log-level touches. Both logs become counts of
touches per region, window and page, which are compared:

  window totals  per region and window: |replay - memtest| <= 4 sqrt(memtest) + 2
  pages          per region over the run: total variation distance of the page
                 distributions <= 0.05 + 3 x its expected sampling noise
  total          |replay - memtest| <= 2 % of memtest + 4 sqrt(memtest)

Exit code 0 if every config matches, 1 if not. See docs/replay_testing.md,
section 2.3.
"""
import argparse
import json
import math
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'builder'))
import memtest2profile as m2p  # noqa: E402
import profile_v2 as pf  # noqa: E402

DAMOLOAD = Path(os.environ.get('QMEM_DAMOLOAD', ROOT / 'third_party' / 'damoLoad'))
MEMTEST = DAMOLOAD / 'memtest' / 'build' / 'memtest'
REPLAY = ROOT / 'replay' / 'qmem-replay'
GT_CAPACITY = 10_000_000          # memtest drops touches beyond this


# ---------------------------------------------------------------- logs


def read_gt(path):
    """memtest's gt.log -> ([pages of each region], [(ts_ns, region, page)])."""
    regions, touches = [], []
    with open(path) as f:
        for line in f:
            if line.startswith('# region'):
                regions.append(int(line.split('pages=')[1]))
            elif line[:1].isdigit():
                ts, r, p = line.split()
                touches.append((int(ts), int(r), int(p)))
    return regions, touches


def read_replay_log(path):
    """qmem-replay --log-level touches -> ([(t_ns, region, page)], end line).
    Touches of unmapped pages (op=skip) touched nothing and are left out."""
    touches, end = [], {}
    with open(path) as f:
        for line in f:
            if line.startswith('touch '):
                kv = dict(x.split('=', 1) for x in line.split()[1:])
                if kv['op'] != 'skip':
                    touches.append((int(kv['t_ns']), int(kv['region']), int(kv['page'])))
            elif line.startswith('end '):
                end = dict(x.split('=', 1) for x in line.split()[1:])
    return touches, end


def count(touches, t0, duration, window_ms):
    """Touches in [t0, t0 + duration) as counts per (region, window) and per
    (region, page)."""
    win, pages, total = Counter(), Counter(), 0
    end = t0 + int(duration * 1e9)
    w_ns = window_ms * 1_000_000
    for ts, r, p in touches:
        if t0 <= ts < end:
            win[(r, (ts - t0) // w_ns)] += 1
            pages[(r, p)] += 1
            total += 1
    return {'windows': win, 'pages': pages, 'total': total,
            'n_windows': math.ceil(duration * 1000 / window_ms)}


# ---------------------------------------------------------------- comparison


def compare(ref, got, tv_base=0.05):
    """ref: memtest's counts, got: replay's. -> report dict with 'ok'."""
    report = {'total_ref': ref['total'], 'total_got': got['total']}
    b, a = ref['total'], got['total']
    report['total_rel'] = (a - b) / b if b else (0.0 if not a else math.inf)
    total_ok = abs(a - b) <= 0.02 * b + 4 * math.sqrt(b)

    bad = []
    regions = sorted({r for r, _ in ref['windows']} | {r for r, _ in got['windows']})
    for r in regions:
        for w in range(ref['n_windows']):
            b, a = ref['windows'].get((r, w), 0), got['windows'].get((r, w), 0)
            if abs(a - b) > 4 * math.sqrt(b) + 2:
                bad.append({'region': r, 'window': w, 'ref': b, 'got': a})
    report['windows_checked'] = len(regions) * ref['n_windows']
    report['bad_windows'] = bad

    per_region = {}
    for r in sorted({r for r, _ in ref['pages']} | {r for r, _ in got['pages']}):
        pr = {p: n for (rr, p), n in ref['pages'].items() if rr == r}
        pg = {p: n for (rr, p), n in got['pages'].items() if rr == r}
        nr, ng = sum(pr.values()), sum(pg.values())
        if not nr or not ng:
            per_region[r] = {'tv': 1.0 if nr != ng else 0.0, 'tv_limit': tv_base,
                             'ref': nr, 'got': ng}
            continue
        keys = set(pr) | set(pg)
        tv = 0.5 * sum(abs(pr.get(k, 0) / nr - pg.get(k, 0) / ng) for k in keys)
        # expected distance of two samples of the same distribution:
        # E|diff_k| = sqrt(2/pi) * sigma_k, sigma_k^2 = p_k (1 - p_k) (1/nr + 1/ng)
        noise = 0.5 * sum(math.sqrt(2 / math.pi * (pr.get(k, 0) / nr) * (1 - pr.get(k, 0) / nr)
                                    * (1 / nr + 1 / ng)) for k in keys)
        per_region[r] = {'tv': tv, 'tv_limit': tv_base + 3 * noise, 'ref': nr, 'got': ng}
    report['regions'] = per_region
    pages_ok = all(x['tv'] <= x['tv_limit'] for x in per_region.values())

    report['ok'] = total_ok and not bad and pages_ok
    return report


# ---------------------------------------------------------------- running


def build_memtest():
    if not MEMTEST.exists():
        subprocess.run(['make', '-C', str(MEMTEST.parent.parent)], check=True,
                       stdout=subprocess.DEVNULL)


def run_memtest(cfg_path, gt_path):
    """Run memtest --auto; returns its time 0 (CLOCK_MONOTONIC ns): the moment
    it was told to start."""
    p = subprocess.Popen([str(MEMTEST), '--auto', str(cfg_path), str(gt_path)],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.startswith('# READY'):
            break
    else:
        raise RuntimeError('memtest did not get ready: ' + p.stderr.read())
    t0 = time.clock_gettime_ns(time.CLOCK_MONOTONIC)
    p.send_signal(signal.SIGUSR1)
    out, err = p.communicate(timeout=3600)
    if p.returncode != 0:
        raise RuntimeError('memtest failed: ' + err)
    return t0


def check(cfg_file, args, work):
    cfg = json.loads(Path(cfg_file).read_text())
    if args.duration:
        cfg = m2p.cut(cfg, args.duration)
    duration = m2p.num(cfg, 'duration_sec', 0)
    cfg_path = work / 'config.json'
    cfg_path.write_text(json.dumps(cfg, indent=1))

    prof = m2p.build(cfg, Path(cfg_file).stem, args.epoch_ms)
    if args.scale_rate != 1.0:                               # self-test of the check
        for ep in prof.epochs:
            for a in ep.accesses:
                a.rate *= args.scale_rate
    prof_path = work / 'profile.txt'
    pf.write(prof_path, prof)

    gt_path = work / 'gt.log'
    t0 = run_memtest(cfg_path, gt_path)
    _, gt = read_gt(gt_path)

    log_path = work / 'replay.log'
    p = subprocess.run([str(REPLAY), str(prof_path), '--log', str(log_path),
                        '--log-level', 'touches'], capture_output=True, text=True,
                       timeout=3600)
    if p.returncode != 0:
        raise RuntimeError('qmem-replay failed: ' + p.stderr)
    rp, end = read_replay_log(log_path)

    ref = count(gt, t0, duration, args.window_ms)
    got = count(rp, 0, duration, args.window_ms)
    report = compare(ref, got)
    write_tables(work / 'comparison.txt', ref, got, args.window_ms)
    report.update(config=Path(cfg_file).name, duration=duration,
                  replay_lagged_ticks=int(end.get('lagged_ticks', 0)),
                  memtest_log_full=len(gt) >= GT_CAPACITY,
                  memtest_after_end=len(gt) - ref['total'])
    return report


def write_tables(path, ref, got, window_ms):
    """What was compared, side by side: touches per page and per window."""
    regions = sorted({r for r, _ in ref['pages']} | {r for r, _ in got['pages']})
    out = ['touches per page over the run', '', 'region  page   memtest   replay']
    for r in regions:
        pages = sorted({p for rr, p in ref['pages'] if rr == r} |
                       {p for rr, p in got['pages'] if rr == r})
        for p in pages:
            out.append('%6d %5d %9d %8d' % (r, p, ref['pages'].get((r, p), 0),
                                            got['pages'].get((r, p), 0)))
    out += ['', 'touches per window of %d ms' % window_ms, '',
            'region  window_ms   memtest   replay']
    for r in regions:
        for w in range(ref['n_windows']):
            out.append('%6d %10d %9d %8d' % (r, w * window_ms, ref['windows'].get((r, w), 0),
                                             got['windows'].get((r, w), 0)))
    Path(path).write_text('\n'.join(out) + '\n')


def describe(r):
    lines = ['%s  %s  memtest=%d replay=%d (%+.1f%%)  windows bad %d/%d' % (
        'OK  ' if r['ok'] else 'FAIL', r['config'], r['total_ref'], r['total_got'],
        100 * r['total_rel'], len(r['bad_windows']), r['windows_checked'])]
    for reg, x in r['regions'].items():
        if x['tv'] > x['tv_limit']:
            lines.append('      region %s: page distribution differs, tv %.3f > %.3f'
                         % (reg, x['tv'], x['tv_limit']))
    for b in r['bad_windows'][:5]:
        lines.append('      region %(region)s window %(window)s: memtest %(ref)s, replay %(got)s' % b)
    if len(r['bad_windows']) > 5:
        lines.append('      … %d more windows' % (len(r['bad_windows']) - 5))
    if r['replay_lagged_ticks']:
        lines.append('      replay fell behind: %d ticks dropped' % r['replay_lagged_ticks'])
    if r['memtest_log_full']:
        lines.append('      memtest log full (%d touches): it dropped the rest' % GT_CAPACITY)
    return '\n'.join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('configs', nargs='+')
    ap.add_argument('--duration', type=float, help='use only the first S seconds')
    ap.add_argument('--window-ms', type=int, default=100)
    ap.add_argument('--epoch-ms', type=int, default=10)
    ap.add_argument('--keep', help='keep the files of a run here (one config only, '
                    'or a subdirectory per config)')
    ap.add_argument('--json', help='write the reports here')
    ap.add_argument('--scale-rate', type=float, default=1.0, help=argparse.SUPPRESS)
    args = ap.parse_args()

    build_memtest()
    reports = []
    for cfg in args.configs:
        if args.keep:
            work = Path(args.keep)
            if len(args.configs) > 1:
                work = work / Path(cfg).stem
            work.mkdir(parents=True, exist_ok=True)
            tmp = None
        else:
            tmp = tempfile.mkdtemp(prefix='damoload-')
            work = Path(tmp)
        try:
            r = check(cfg, args, work)
        except Exception as e:                                   # noqa: BLE001
            r = {'config': Path(cfg).name, 'ok': False, 'error': str(e)}
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)
        reports.append(r)
        print(describe(r) if 'error' not in r else 'FAIL  %s  error: %s' % (r['config'], r['error']),
              flush=True)

    if args.json:
        Path(args.json).write_text(json.dumps(reports, indent=1, default=str))
    ok = sum(r['ok'] for r in reports)
    print('%d of %d configs match' % (ok, len(reports)))
    sys.exit(0 if ok == len(reports) else 1)


if __name__ == '__main__':
    main()
