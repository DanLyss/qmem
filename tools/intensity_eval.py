#!/usr/bin/env python3
"""How well can access intensity be estimated from a record? Checked against
the ground truth of a test program (QMEM_GT with per-chunk touch counts).

usage: intensity_eval.py <record dir> <ground truth file> [--json]
                         [--counter-error 0.0] [--damon measured|ideal]

For every 64 KB chunk and 100 ms window, the true intensity is the program's
touches per page per second. Estimators, from the record's DAMON snapshots:

  now        rho / s                  what mkprofile/replay do today
  A          -ln(1 - rho) / s         the inverse of rho = 1 - exp(-lambda s),
                                      capped at rho_max (DAMON saturates)
  A+B        A, and the touches a process counter would add on top (the
             counter's total minus what A accounts for) spread over the
             saturated chunks by size
  A+Bw       the same remainder spread over all chunks in proportion to what
             DAMON saw of them (rho): regions that mix hot and cold pages do
             not saturate, but still hold most of the missing touches
  A+C        address sampling by the processor (PEBS / SPE), simulated: one
             of every K touches is recorded with its address (binomial
             draws from the ground truth's counts); chunks with at least 3
             samples take samples x K, the rest keep A
  The counter is simulated by the ground truth's total, optionally off by
  --counter-error.

--damon ideal replaces each DAMON region's rate by what DAMON would see
without its TLB blind spot: the region's mean of 1 - exp(-lambda s) over the
ground truth's chunks, with the sampling noise of T / s checks per window.
It separates the estimation method from DAMON missing TLB-resident pages.

Reported, weighted by true touches: the share of touches in cells estimated
within a factor of 2 and within 30 %, the median and the 10th / 90th
percentiles of estimate / truth, and the total estimate / total truth.
See docs/proposals/intensity.md.
"""
import argparse
import bisect
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'verify'))
from gt import read_gt  # noqa: E402
from mkprofile import read_access, sample_seconds  # noqa: E402

PAGE = 4096
RHO_MAX = 0.95


def weighted_quantile(pairs, q):
    """pairs: [(value, weight)]"""
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= q * total:
            return v
    return pairs[-1][0] if pairs else None


def binomial(rnd, n, p):
    """A binomial draw; normal approximation for large n."""
    if n < 50:
        return sum(rnd.random() < p for _ in range(n))
    mean, sd = n * p, math.sqrt(n * p * (1 - p))
    return max(0, round(rnd.gauss(mean, sd)))


def evaluate(record, gt_path, counter_error=0.0, damon='measured', seed=1,
             sample_every=10000):
    record = Path(record)
    gt = read_gt(gt_path)
    if not gt['counts']:
        sys.exit('intensity_eval: %s has no touch counts (QGT1); rerun with a newer build'
                 % gt_path)
    meta = json.loads((record / 'meta.json').read_text())
    s = sample_seconds(record)
    window_s = gt['window_ms'] / 1000
    chunk = gt['chunk_pages']
    shift_ms = gt['start_boot_ns'] / 1e6 - meta['start_uptime'] * 1000
    start, npages = gt['ranges'][0]
    nchunks = -(-npages // chunk)

    # the DAMON snapshot of each ground-truth window (by its middle)
    snaps = read_access(record)
    starts = [a for a, _, _ in snaps]

    rnd = random.Random(seed)
    checks = max(1, round(window_s / s))     # DAMON checks per region per window

    def damon_rho(w, counts):
        t = w * gt['window_ms'] + gt['window_ms'] / 2 + shift_ms
        i = bisect.bisect_right(starts, t) - 1
        if i < 0 or t >= snaps[i][1] + 200:
            return None
        rho = [0.0] * nchunks
        cover = [0] * nchunks
        for lo, hi, rate in snaps[i][2]:
            if damon == 'ideal':
                # what DAMON would see of this region without the TLB blind spot
                a, b = max(lo, start), min(hi, start + npages * PAGE)
                if a < b:
                    cs = range((a - start) // (chunk * PAGE), -(-(b - start) // (chunk * PAGE)))
                    p = sum(1 - math.exp(-counts[c] / (chunk * window_s) * s) for c in cs) / len(cs)
                    hits = sum(rnd.random() < p for _ in range(checks))
                    rate = 100 * hits / checks
            a, b = max(lo, start), min(hi, start + npages * PAGE)
            if a >= b:
                continue
            for c in range((a - start) // (chunk * PAGE), -(-(b - start) // (chunk * PAGE))):
                c0 = start + c * chunk * PAGE
                ov = min(b, c0 + chunk * PAGE) - max(a, c0)
                if ov > 0:
                    rho[c] += rate / 100 * ov
                    cover[c] += ov
        return [r / v if v else 0.0 for r, v in zip(rho, cover)]

    cells = {name: [] for name in ('now', 'A', 'A+B', 'A+Bw', 'A+C')}
    totals = {name: [0.0, 0.0] for name in cells}
    for (w, r), counts in sorted(gt['counts'].items()):
        if w < 2:          # start-up: the program writes its memory once
            continue
        rho = damon_rho(w, counts)
        if rho is None:
            continue
        true = [n / (chunk * window_s) for n in counts]      # touches/page/s
        est = {'now': [x / s for x in rho],
               'A': [-math.log(1 - min(x, RHO_MAX)) / s for x in rho]}
        # A+B: the counter tops up the saturated chunks
        n_true = sum(counts) * (1 + counter_error)
        sat = [c for c, x in enumerate(rho) if x >= RHO_MAX]
        ab = list(est['A'])
        if sat:
            accounted = sum(ab[c] for c in range(nchunks) if c not in set(sat)) * chunk * window_s
            left = n_true - accounted
            if left > 0:
                for c in sat:
                    ab[c] = left / (len(sat) * chunk * window_s)
        est['A+B'] = ab
        # A+Bw: the remainder in proportion to rho, over all chunks
        abw = list(est['A'])
        left = n_true - sum(abw) * chunk * window_s
        weight = sum(rho)
        if left > 0 and weight > 0:
            for c in range(nchunks):
                abw[c] += left * rho[c] / weight / (chunk * window_s)
        est['A+Bw'] = abw
        # A+C: sampled addresses where there are enough of them
        ac = list(est['A'])
        for c in range(nchunks):
            k = binomial(rnd, counts[c], 1 / sample_every)
            if k >= 3:
                ac[c] = k * sample_every / (chunk * window_s)
        est['A+C'] = ac
        for name in cells:
            totals[name][0] += sum(est[name]) * chunk * window_s
            totals[name][1] += sum(counts)
            for c in range(nchunks):
                if counts[c]:
                    cells[name].append((est[name][c] / true[c], counts[c]))

    out = {}
    for name, pairs in cells.items():
        weight = sum(w for _, w in pairs)
        within2 = sum(w for v, w in pairs if 0.5 <= v <= 2) / weight
        within30 = sum(w for v, w in pairs if 0.7 <= v <= 1.3) / weight
        out[name] = {
            'within_x2': within2, 'within_30pct': within30,
            'p10': weighted_quantile(pairs, 0.1), 'median': weighted_quantile(pairs, 0.5),
            'p90': weighted_quantile(pairs, 0.9),
            'total_ratio': totals[name][0] / totals[name][1] if totals[name][1] else None,
        }
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('record')
    ap.add_argument('gt')
    ap.add_argument('--counter-error', type=float, default=0.0)
    ap.add_argument('--damon', choices=('measured', 'ideal'), default='measured')
    ap.add_argument('--json', action='store_true')
    args = ap.parse_args()
    out = evaluate(args.record, args.gt, args.counter_error, args.damon)
    if args.json:
        print(json.dumps(out, indent=2))
        return
    print('%-5s %9s %10s %7s %7s %7s %9s' % ('', 'within x2', 'within 30%', 'p10',
                                           'median', 'p90', 'total'))
    for name, m in out.items():
        print('%-5s %8.1f%% %9.1f%% %7.3f %7.3f %7.3f %9.3f' % (
            name, 100 * m['within_x2'], 100 * m['within_30pct'], abs(m['p10']),
            m['median'], m['p90'], m['total_ratio']))


if __name__ == '__main__':
    main()
