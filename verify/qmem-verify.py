#!/usr/bin/env python3
"""Check how closely a replay reproduces the original.

usage: qmem-verify.py <original record> <replay record>
                      [--profile <profile.txt> --layout <layout.txt>]
                      [--time-bin-ms 500] [--offset-bins 64] [--maps 3]
                      [--json] [--check] [--tolerance 0.2]

The main check compares DAMON access frequency by place: both records are
mapped to region + offset + profile time (the original's region bases come
from the profile, the replay's from qmem-replay --layout) and turned into
heatmaps, compared cell by cell. On top: totals (peak RSS, accessed memory,
application faults, dirty share) and RSS / faults over time.
See docs/modules/verify.md.
"""
import argparse
import bisect
import csv
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'tools'))
from mkprofile import VMA, is_anon, read_access, read_smaps  # noqa: E402

MB = 1 << 20
PAGE = 4096
HOT, WARM = 50.0, 25.0          # access rate (%) of hot and of "warm enough"

# --check thresholds of the maps; totals and the mean deviation of the
# series use --tolerance
LIMITS = {'mae_pp': ('<=', 10.0), 'corr': ('>=', 0.8), 'hot_recall': ('>=', 0.8),
          'hot_precision': ('>=', 0.8)}


# ------------------------------------------------------------------ inputs


def read_profile_regions(path):
    regions = {}
    for line in Path(path).read_text().splitlines():
        f = line.split('#')[0].split()
        if len(f) >= 4 and f[0] == 'region':
            regions[int(f[1])] = (int(f[3], 16), int(f[2]))
    if not regions:
        sys.exit('qmem-verify: %s has no region bases (rebuild it with mkprofile)' % path)
    return regions


def read_layout(path):
    start, regions = None, {}
    for line in Path(path).read_text().splitlines():
        f = line.split()
        if f[:1] == ['start_boottime_ns']:
            start = int(f[1])
        elif f[:1] == ['region']:
            regions[int(f[1])] = (int(f[2], 16), int(f[3]))
    if start is None:
        sys.exit('qmem-verify: %s is not a qmem-replay layout' % path)
    return start, regions


def meta(d):
    path = Path(d) / 'meta.json'
    return json.loads(path.read_text()) if path.exists() else {}


def sd_vmas(d):
    """{ts_ms: [(start, end)]} of anonymous VMAs flagged soft-dirty ('sd')."""
    out = {}
    for f in sorted((Path(d) / 'smaps').glob('*.txt'), key=lambda f: int(f.stem)):
        cur, marked = None, []
        for line in f.read_text().splitlines():
            m = VMA.match(line)
            if m:
                cur = (int(m[1], 16), int(m[2], 16)) if is_anon(m[3], m[4].strip()) else None
            elif cur and line.startswith('VmFlags:') and ' sd' in ' ' + line[8:]:
                marked.append(cur)
        out[int(f.stem)] = marked
    return out


# ------------------------------------------------------------------ totals


def totals(d):
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

    return t0, {
        'rss_peak_mb': peak / MB,
        'hot_mb_mean': sum(hot) / len(hot) if hot else 0.0,
        'hot50_mb_mean': sum(hot50) / len(hot50) if hot50 else 0.0,
        'app_minflt': app_minflt,
        'app_majflt': app_majflt,
        'pgfault': delta('pgfault'),
        'pgmajfault': delta('pgmajfault'),
        'pswpout': delta('pswpout'),
        'dirty_share': dirty_share(d),
    }


def dirty_share(d):
    """Written pages over present pages, without the first sample (start-up
    writes) and without VMAs the kernel marked soft-dirty as a whole."""
    path = d / 'writes.csv'
    if not path.exists():
        return None
    rows = list(csv.DictReader(open(path)))
    if not rows:
        return None
    first = min(int(r['ts_ms']) for r in rows)
    marked = sd_vmas(d)
    stamps = sorted(marked)
    present = dirty = 0
    for r in rows:
        ts, start = int(r['ts_ms']), int(r['start'])
        if ts == first:
            continue
        i = bisect.bisect_right(stamps, ts) - 1
        if i >= 0 and any(s <= start < e for s, e in marked[stamps[i]]):
            continue
        present += int(r['present_pages'])
        dirty += int(r['dirty_pages'])
    return dirty / present if present else None


def diff(orig, replay):
    if orig is None or replay is None:
        return None
    if orig == 0:
        return 0.0 if replay == 0 else None
    return (replay - orig) / orig


# ------------------------------------------------------------------ maps


def bin_layout(size, nbins):
    n = max(1, min(nbins, size // PAGE))
    return n, -(-size // n)


def heatmap(d, regions, shift_ms, time_bin, nbins):
    """{(region, time bin, offset bin): mean access rate} weighted by bytes."""
    acc = {}
    order = sorted(regions.items(), key=lambda kv: kv[1][0])
    for s_ms, e_ms, damon in read_access(d):
        t = (s_ms + e_ms) / 2 - shift_ms
        if t < 0:
            continue
        tb = int(t // time_bin)
        for lo, hi, rate in damon:
            for r, (base, size) in order:
                a, b = max(lo, base), min(hi, base + size)
                if a >= b:
                    continue
                n, width = bin_layout(size, nbins)
                for ob in range((a - base) // width, min(n, -(-(b - base) // width))):
                    c0, c1 = base + ob * width, base + (ob + 1) * width
                    ov = min(b, c1) - max(a, c0)
                    if ov > 0:
                        cell = acc.setdefault((r, tb, ob), [0.0, 0])
                        cell[0] += rate * ov
                        cell[1] += ov
    return {k: v[0] / v[1] for k, v in acc.items() if v[1]}


def pearson(pairs, weights):
    sw = sum(weights)
    if not sw:
        return None
    mo = sum(w * o for (o, _), w in zip(pairs, weights)) / sw
    mr = sum(w * r for (_, r), w in zip(pairs, weights)) / sw
    cov = sum(w * (o - mo) * (r - mr) for (o, r), w in zip(pairs, weights))
    vo = sum(w * (o - mo) ** 2 for (o, _), w in zip(pairs, weights))
    vr = sum(w * (r - mr) ** 2 for (_, r), w in zip(pairs, weights))
    if vo == 0 or vr == 0:
        # a flat map: alike only if both are flat at the same level
        same = all(abs(o - r) <= 1 for o, r in pairs)
        return 1.0 if same else 0.0
    return cov / math.sqrt(vo * vr)


def map_metrics(orig, replay, cell_bytes):
    keys = sorted(set(orig) & set(replay))
    if not keys:
        return None
    pairs = [(orig[k], replay[k]) for k in keys]
    w = [cell_bytes[k[0]] for k in keys]
    active = [(p, wi) for p, wi in zip(pairs, w) if max(p) > 0]
    mae = (sum(wi * abs(o - r) for (o, r), wi in active) / sum(wi for _, wi in active)
           if active else 0.0)

    def share(num, den):
        d = sum(wi for p, wi in zip(pairs, w) if den(p))
        return sum(wi for p, wi in zip(pairs, w) if den(p) and num(p)) / d if d else None

    return {
        'mae_pp': mae,
        'corr': pearson(pairs, w),
        'hot_recall': share(lambda p: p[1] >= WARM, lambda p: p[0] >= HOT),
        'hot_precision': share(lambda p: p[0] >= WARM, lambda p: p[1] >= HOT),
    }


def text_maps(orig, replay, regions, nbins, time_bin, how_many):
    lines = []
    for r, (_, size) in sorted(regions.items(), key=lambda kv: -kv[1][1])[:how_many]:
        n, width = bin_layout(size, nbins)
        tbs = sorted({k[1] for k in list(orig) + list(replay) if k[0] == r})
        if not tbs:
            continue
        lines.append('region %d (%.1f MB): rows %d ms, %d columns of %.2f MB, '
                     'digit = access rate / 10, original | replay'
                     % (r, size / MB, time_bin, n, width / MB))

        def row(m, tb):
            return ''.join('.' if (r, tb, ob) not in m else
                           str(min(9, int(m[(r, tb, ob)] // 10))) for ob in range(n))
        for tb in range(tbs[0], tbs[-1] + 1):
            lines.append('t=%6d %s | %s' % (tb * time_bin, row(orig, tb), row(replay, tb)))
        lines.append('')
    return lines


# ------------------------------------------------------------------ series


def series(d, shift_ms, time_bin):
    """{name: {time bin: value}} for anonymous RSS and application minflt."""
    out = {'rss': {}, 'minflt': {}}
    smaps, _ = read_smaps(d)
    for ts, _, vmas in smaps:
        t = ts - shift_ms
        if t >= 0:
            out['rss'][int(t // time_bin)] = sum(v[2] for v in vmas)
    if (d / 'proc.csv').exists():
        for r in csv.DictReader(open(d / 'proc.csv')):
            t = int(r['ts_ms']) - shift_ms
            if t >= 0:
                out['minflt'][int(t // time_bin)] = int(r['minflt'])
    return out


def series_metrics(o, r):
    """Correlation, mean and largest deviation (relative to the original's
    peak) over the common time span, carrying the last value over empty
    bins. Correlation says little about a flat series, so the check uses
    the mean deviation."""
    if not o or not r:
        return None, None, None
    end = min(max(o), max(r))
    fo, fr, pairs = None, None, []
    for tb in range(0, end + 1):
        fo = o.get(tb, fo)
        fr = r.get(tb, fr)
        if fo is not None and fr is not None:
            pairs.append((fo, fr))
    if not pairs:
        return None, None, None
    peak = max(abs(p[0]) for p in pairs) or 1
    devs = [abs(a - b) / peak for a, b in pairs]
    scale = peak / 100
    corr = pearson([(a / scale, b / scale) for a, b in pairs], [1] * len(pairs))
    return corr, sum(devs) / len(devs), max(devs)


# ------------------------------------------------------------------ main


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('original')
    ap.add_argument('replay')
    ap.add_argument('--profile')
    ap.add_argument('--layout')
    ap.add_argument('--time-bin-ms', type=int, default=500)
    ap.add_argument('--offset-bins', type=int, default=64)
    ap.add_argument('--maps', type=int, default=3, help='regions to print as text maps')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--tolerance', type=float, default=0.2)
    args = ap.parse_args()
    orig_d, rep_d = Path(args.original), Path(args.replay)

    t0_orig, orig = totals(orig_d)
    t0_rep, rep = totals(rep_d)
    metrics = {name: {'orig': orig[name], 'replay': rep[name],
                      'diff': diff(orig[name], rep[name])} for name in orig}

    maps = regions_out = None
    text = []
    if args.profile and args.layout:
        o_regions = read_profile_regions(args.profile)
        start_ns, r_regions = read_layout(args.layout)
        shift_ms = start_ns / 1e6 - meta(rep_d).get('start_uptime', 0) * 1000
        common = sorted(set(o_regions) & set(r_regions))
        o_regions = {r: o_regions[r] for r in common}
        r_regions = {r: r_regions[r] for r in common}
        omap = heatmap(orig_d, o_regions, 0, args.time_bin_ms, args.offset_bins)
        rmap = heatmap(rep_d, r_regions, shift_ms, args.time_bin_ms, args.offset_bins)
        cell_bytes = {r: bin_layout(size, args.offset_bins)[1]
                      for r, (_, size) in o_regions.items()}
        maps = map_metrics(omap, rmap, cell_bytes)
        regions_out = []
        for r in common:
            m = map_metrics({k: v for k, v in omap.items() if k[0] == r},
                            {k: v for k, v in rmap.items() if k[0] == r}, cell_bytes)
            if m:
                regions_out.append(dict(region=r, size=o_regions[r][1], **m))
        text = text_maps(omap, rmap, o_regions, args.offset_bins, args.time_bin_ms,
                         args.maps)
        o_shift = 0
    else:
        print('qmem-verify: no --profile/--layout, comparing totals only '
              '(time aligned by RSS growth)', file=sys.stderr)
        shift_ms, o_shift = t0_rep - t0_orig, 0

    so = series(orig_d, o_shift, args.time_bin_ms)
    sr = series(rep_d, shift_ms, args.time_bin_ms)
    ser = {}
    for name in ('rss', 'minflt'):
        corr, mean_dev, max_dev = series_metrics(so[name], sr[name])
        ser.update({name + '_corr': corr, name + '_mean_dev': mean_dev,
                    name + '_max_dev': max_dev})

    # verdict
    faults = 'app_minflt' if metrics['app_minflt']['diff'] is not None else 'pgfault'
    failed = [n for n in ('rss_peak_mb', 'hot_mb_mean', faults)
              if metrics[n]['diff'] is not None and abs(metrics[n]['diff']) > args.tolerance]
    failed += [n for n in ('rss_mean_dev', 'minflt_mean_dev')
               if ser[n] is not None and ser[n] > args.tolerance]
    for name, (op, limit) in LIMITS.items():
        v = maps.get(name) if maps else None
        if v is not None and (v > limit if op == '<=' else v < limit):
            failed.append(name)

    if args.json:
        print(json.dumps({'shift_ms': shift_ms, 'metrics': metrics, 'maps': maps,
                          'regions': regions_out, 'series': ser, 'failed': failed},
                         indent=2))
    else:
        fmt = lambda v: '-' if v is None else ('%.3f' % v if abs(v) < 10 else '%.1f' % v)
        mark = lambda n: '  FAIL' if n in failed else ''
        if maps:
            print('access frequency by place (region + offset + time):')
            for name, (op, limit) in list(LIMITS.items())[:4]:
                print('  %-14s %8s   (%s %s)%s' % (name, fmt(maps[name]), op, limit,
                                                   mark(name)))
            for r in regions_out:
                print('  region %-3d %8.1f MB  mae %5.1f pp  corr %s  recall %s  '
                      'precision %s' % (r['region'], r['size'] / MB, r['mae_pp'],
                                        fmt(r['corr']), fmt(r['hot_recall']),
                                        fmt(r['hot_precision'])))
            print()
        print('over time:')
        for name in ser:
            print('  %-14s %8s%s' % (name, fmt(ser[name]), mark(name)))
        print()
        print('%-15s %12s %12s %9s' % ('totals', 'original', 'replay', 'diff'))
        for name, m in metrics.items():
            print('%-15s %12s %12s %9s%s' % (
                name, fmt(m['orig']), fmt(m['replay']),
                '-' if m['diff'] is None else '%+.1f%%' % (m['diff'] * 100), mark(name)))
        print('replay starts %+d ms later than the original' % shift_ms)
        if text:
            print()
            print('\n'.join(text))
        if failed:
            print('FAILED: ' + ', '.join(failed))
    if args.check and failed:
        sys.exit(1)


if __name__ == '__main__':
    main()
