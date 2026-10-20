#!/usr/bin/env python3
"""Turn a damoLoad memtest workload config into a qmem profile v2.

usage: memtest2profile.py <config.json> -o <profile> [--epoch-ms 10]
                          [--duration S] [--seed 42]

Every memtest spatial selector picks pages with known probabilities, so the
expected touches of each page per second are known exactly:

    rate(page, t) = sum over the region's tracks of  hz_track(t) * P_track(page)
                    (spatial=all: hz_track(t) for every page)

The profile gives each page that rate, averaged over each epoch. Pages next
to each other with the same rate share one access line; epochs in a row
with the same rates are merged. memtest writes every touch, so write=1;
random selectors replay with pattern=random, sequential and all with
roundrobin. The formulas follow memtest's own code (damoLoad memtest/src).
See docs/replay_testing.md, section 2.2.
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'builder'))
import profile_v2 as pf  # noqa: E402

PAGE = 4096
SUBSTEPS = 20           # samples of hz(t) per epoch for the epoch's average
RANDOM = {'uniform', 'hotspot', 'zipf', 'gaussian'}


def num(obj, key, default):
    v = obj.get(key, default) if isinstance(obj, dict) else default
    return float(v) if v is not None else default


# ---------------------------------------------------------------- temporal


def temporal(spec, track_dur):
    """hz as a function of the time since the track's start (memtest's
    build_temporal: same defaults)."""
    spec = spec or {}
    kind = spec.get('type')
    if kind == 'sine':
        base, amp = num(spec, 'base_hz', 50), num(spec, 'amplitude', 30)
        period, phase = num(spec, 'period_sec', 2.0), num(spec, 'phase_rad', 0.0)
        return lambda t: max(0.0, base + amp * math.sin(2 * math.pi * t / period + phase))
    if kind == 'square':
        on, duty = num(spec, 'on_hz', 200), num(spec, 'duty', 0.5)
        period, phase = num(spec, 'period_sec', 1.0), num(spec, 'phase_rad', 0.0)
        shift = phase / (2 * math.pi) * period
        return lambda t: on if math.fmod(t + shift, period) / period < duty else 0.0
    if kind == 'ramp':
        a, b = num(spec, 'start_hz', 0), num(spec, 'end_hz', 200)
        total = num(spec, 'total_sec', track_dur)
        return lambda t: a + (b - a) * min(1.0, max(0.0, t / total))
    if kind == 'steps':
        steps = [(num(s, 'hz', 0), num(s, 'duration_sec', 1.0)) for s in spec.get('steps') or []]
        if not steps:
            return lambda t: 0.0

        def f(t):
            elapsed = 0.0
            for hz, d in steps:
                elapsed += d
                if t < elapsed:
                    return hz
            return steps[-1][0]
        return f
    return lambda t, hz=num(spec, 'hz', 100): hz            # const and unknown types


# ---------------------------------------------------------------- spatial


def from_cdf(cdf):
    """Page probabilities of memtest's cdf walk, whose last entry is forced to 1."""
    cdf = list(cdf)
    cdf[-1] = 1.0
    return [c - (cdf[i - 1] if i else 0.0) for i, c in enumerate(cdf)]


def spatial(spec, n):
    """(probability of each page per touch, or None for 'all', random?)."""
    spec = spec or {}
    kind = spec.get('type')
    if kind == 'sequential':
        return [1.0 / n] * n, False
    if kind == 'all':
        return None, False
    if kind == 'hotspot':
        hot = [int(h) for h in spec.get('hot_pages') or []]
        ratio = num(spec, 'hot_ratio', 0.8)
        n_cold = n - len(hot)
        prob = [(1 - ratio) / n_cold if n_cold > 0 else 0.0] * n
        for h in hot:
            if 0 <= h < n:
                prob[h] = ratio / len(hot)
        cdf, cum = [], 0.0
        for p in prob:
            cum += p
            cdf.append(cum)
        return from_cdf(cdf), True
    if kind == 'zipf':
        s = num(spec, 's', 1.0)
        norm = sum(1 / k ** s for k in range(1, n + 1))
        cdf, cum = [], 0.0
        for k in range(1, n + 1):
            cum += 1 / k ** s / norm
            cdf.append(cum)
        return from_cdf(cdf), True
    if kind == 'gaussian':
        c, sigma = num(spec, 'center', n / 2.0), num(spec, 'sigma', 1.0)

        def phi(x):
            return 0.5 * (1 + math.erf((x - c) / (sigma * math.sqrt(2))))
        # page = clamp(round(c + sigma * z)): everything below 0.5 is page 0,
        # everything from n - 1.5 up is the last page
        prob = []
        for p in range(n):
            lo = 0.0 if p == 0 else phi(p - 0.5)
            hi = 1.0 if p == n - 1 else phi(p + 0.5)
            prob.append(hi - lo)
        return prob, True
    return [1.0 / n] * n, True                               # uniform and unknown types


# ---------------------------------------------------------------- config


def cut(cfg, duration):
    """The config limited to its first <duration> seconds."""
    cfg = json.loads(json.dumps(cfg))
    full = num(cfg, 'duration_sec', 0)
    cfg['duration_sec'] = min(full, duration) if full else duration
    tracks = []
    for t in cfg.get('tracks') or []:
        start = num(t, 'start_sec', 0.0)
        end = num(t, 'end_sec', full)
        if start >= duration:
            continue
        # keep the track's own length: ramp and steps are timed by it
        t.setdefault('end_sec', end)
        if t['temporal'].get('type') == 'ramp':
            t['temporal'].setdefault('total_sec', end - start)
        t['end_sec'] = min(end, duration)
        tracks.append(t)
    cfg['tracks'] = tracks
    return cfg


def tracks_of(cfg):
    duration = num(cfg, 'duration_sec', 0)
    regions = [int(num(r, 'pages', 10)) for r in cfg.get('regions') or []]
    out = []
    for t in cfg.get('tracks') or []:
        r = int(num(t, 'region', 0))
        if not 0 <= r < len(regions):
            r = 0                                            # as memtest does
        start, end = num(t, 'start_sec', 0.0), num(t, 'end_sec', duration)
        prob, rnd = spatial(t.get('spatial'), regions[r])
        out.append(dict(region=r, start=start, end=end, hz=temporal(t.get('temporal'), end - start),
                        prob=prob, random=rnd))
    return duration, regions, out


def epoch_rates(tracks, regions, t0, t1):
    """{region: (per-page rates, random?)} averaged over [t0, t1) seconds."""
    out = {}
    for tr in tracks:
        # the part of the epoch where the track runs (memtest: start <= t <= end)
        lo, hi = max(t0, tr['start']), min(t1, tr['end'])
        if hi <= lo:
            continue
        step = (t1 - t0) / SUBSTEPS
        total = 0.0
        for k in range(SUBSTEPS):
            t = t0 + (k + 0.5) * step
            if lo <= t < hi or (t == hi == tr['end']):
                total += tr['hz'](t - tr['start'])
        hz = total / SUBSTEPS
        if hz <= 0:
            continue
        n = regions[tr['region']]
        rates, rnd = out.setdefault(tr['region'], ([0.0] * n, False))
        if tr['prob'] is None:
            for p in range(n):
                rates[p] += hz
        else:
            for p in range(n):
                rates[p] += hz * tr['prob'][p]
        out[tr['region']] = (rates, rnd or tr['random'])
    return out


def build(cfg, name, epoch_ms, seed):
    duration, regions, tracks = tracks_of(cfg)
    prof = pf.Profile(meta={'source': 'memtest:' + name, 'builder': 'memtest2profile',
                            'epoch_ms': epoch_ms, 'seed': seed})
    for i, n in enumerate(regions):
        prof.regions.append(pf.Region(i, n * PAGE))
        prof.events.append(pf.Event('map', 0, i, 0, n * PAGE))
        prof.events.append(pf.Event('populate', 0, i, 0, n * PAGE))

    last_key, last = None, None
    for k in range(int(round(duration * 1000 / epoch_ms))):
        t0, t1 = k * epoch_ms / 1000, (k + 1) * epoch_ms / 1000
        rates = epoch_rates(tracks, regions, t0, t1)
        key = tuple(sorted((r, tuple(v), rnd) for r, (v, rnd) in rates.items()))
        if not rates:
            last_key, last = None, None
            continue
        if key == last_key and last.t + last.dur == k * epoch_ms:
            last.dur += epoch_ms
            continue
        ep = pf.Epoch(k * epoch_ms, epoch_ms)
        for r in sorted(rates):
            v, rnd = rates[r]
            p = 0
            while p < len(v):
                q = p
                # equal up to rounding (memtest's last cdf entry is forced to 1)
                while q + 1 < len(v) and abs(v[q + 1] - v[p]) <= 1e-9 * v[p]:
                    q += 1
                if v[p] > 0:
                    ep.accesses.append(pf.Access(r, p * PAGE, (q - p + 1) * PAGE, rate=v[p],
                                                 write=1.0,
                                                 pattern='random' if rnd else 'roundrobin'))
                p = q + 1
        prof.epochs.append(ep)
        last_key, last = key, ep
    return prof


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('config')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--epoch-ms', type=int, default=10)
    ap.add_argument('--duration', type=float, help='use only the first S seconds')
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    if args.epoch_ms <= 0:
        ap.error('--epoch-ms must be positive')
    cfg = json.loads(Path(args.config).read_text())
    if args.duration:
        cfg = cut(cfg, args.duration)
    prof = build(cfg, Path(args.config).stem, args.epoch_ms, args.seed)
    pf.write(args.output, prof)
    print('memtest2profile: %d regions, %d epochs' % (len(prof.regions), len(prof.epochs)),
          file=sys.stderr)


if __name__ == '__main__':
    main()
