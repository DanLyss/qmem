#!/usr/bin/env python3
"""Build a qmem-replay profile from a qmem-record directory.

usage: mkprofile.py <record dir> -o <profile.txt> [--epoch-ms 100] [--seed 42]

Regions, their lifetime and resident memory come from the smaps snapshots, access rates from
access.csv (DAMON), write ratios from writes.csv. Only anonymous memory is
replayed in this iteration; file mappings are counted and skipped.
See docs/modules/replay.md.
"""
import argparse
import bisect
import csv
import json
import re
import sys
from pathlib import Path

PAGE = 4096
VMA = re.compile(r'^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$')


def die(msg):
    sys.exit('mkprofile: ' + msg)


# ------------------------------------------------------------------ intervals


def merge(intervals):
    """Sort and merge overlapping or touching [start, end) intervals."""
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [tuple(i) for i in out]


def subtract(a, b):
    """Parts of the merged intervals a not covered by the merged intervals b."""
    out = []
    for s, e in a:
        cur = s
        for bs, be in b:
            if be <= cur or bs >= e:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
        if cur < e:
            out.append((cur, e))
    return out


def intersect(a, b):
    out = []
    for s, e in a:
        for bs, be in b:
            lo, hi = max(s, bs), min(e, be)
            if lo < hi:
                out.append((lo, hi))
    return out


# ------------------------------------------------------------------ reading


def is_anon(perms, name):
    if perms.startswith('---'):
        return False            # guard pages: nothing to replay
    return name in ('', '[heap]', '[stack]') or name.startswith('[anon')


def read_smaps(d):
    """[(ts_ms, merged anonymous intervals, [(start, end, rss_bytes)])],
    number of file mappings."""
    snaps, files = [], set()
    for f in sorted((d / 'smaps').glob('*.txt'), key=lambda f: int(f.stem)):
        anon, rss, cur = [], [], None
        for line in f.read_text().splitlines():
            m = VMA.match(line)
            if m:
                start, end, perms, name = int(m[1], 16), int(m[2], 16), m[3], m[4].strip()
                cur = None
                if is_anon(perms, name):
                    anon.append((start, end))
                    cur = [start, end, 0]
                    rss.append(cur)
                elif name and not name.startswith('['):
                    files.add((start, end, name))
            elif cur is not None and line.startswith('Rss:'):
                cur[2] = int(line.split()[1]) * 1024
        snaps.append((int(f.stem), merge(anon), [tuple(r) for r in rss]))
    if not snaps:
        die('no smaps snapshots in %s' % d)
    return snaps, len(files)


def read_access(d):
    """[(start_ms, end_ms, [(start, end, access_rate)])] sorted by time."""
    snaps = {}
    with open(d / 'access.csv') as f:
        for r in csv.DictReader(f):
            key = (int(r['snapshot_start_ns']) / 1e6, int(r['snapshot_end_ns']) / 1e6)
            snaps.setdefault(key, []).append(
                (int(r['start']), int(r['end']), float(r['access_rate'])))
    return [(s, e, regions) for (s, e), regions in sorted(snaps.items())]


def to_seconds(text):
    """'5 ms', '5000 us', '1 s' or a bare number of microseconds."""
    m = re.match(r'^\s*([0-9.]+)\s*(us|ms|s)?\s*$', str(text))
    if not m:
        die('cannot read interval %r' % text)
    scale = {'us': 1e-6, 'ms': 1e-3, 's': 1.0, None: 1e-6}[m[2]]
    return float(m[1]) * scale


def find_key(obj, key):
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = find_key(v, key)
            if found is not None:
                return found
    return None


def sample_seconds(d):
    path = d / 'damon.data.kdamonds'
    if path.exists():
        found = find_key(json.loads(path.read_text()), 'sample_us')
        if found is not None:
            return to_seconds(found)
    return 0.005                # DAMON's default


def read_writes(d):
    """(period_ms, {ts_ms: [(start, end, present, dirty)]}) or None."""
    path = d / 'writes.csv'
    if not path.exists():
        return None
    samples = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            samples.setdefault(int(r['ts_ms']), []).append(
                (int(r['start']), int(r['end']), int(r['present_pages']),
                 int(r['dirty_pages'])))
    if not samples:
        return None
    period = 0
    meta = d / 'meta.json'
    if meta.exists():
        period = json.loads(meta.read_text()).get('softdirty_ms') or 0
    if not period:
        stamps = sorted(samples)
        period = stamps[1] - stamps[0] if len(stamps) > 1 else stamps[0]
    return period, samples


# ------------------------------------------------------------------ building


def build(d, epoch_ms):
    smaps, n_files = read_smaps(d)
    regions = merge(i for _, anon, _ in smaps for i in anon)
    region_starts = [s for s, _ in regions]

    def region_of(addr):
        i = bisect.bisect_right(region_starts, addr) - 1
        return i if i >= 0 and addr < regions[i][1] else None

    # map/unmap events from the differences between snapshots, populate
    # events from the growth of resident memory
    events, prev, populated = [], [], []
    for ts, anon, rss in smaps:
        for kind, parts in (('unmap', subtract(prev, anon)), ('map', subtract(anon, prev))):
            for s, e in parts:
                r = region_of(s)
                events.append((ts, kind, r, s - regions[r][0], e - s))
        populated = intersect(populated, anon)
        for s, e in populate_ranges(rss, populated):
            r = region_of(s)
            events.append((ts, 'populate', r, s - regions[r][0], e - s))
            populated = merge(populated + [(s, e)])
        prev = anon

    def mapped_at(t_ms):
        stamps = [ts for ts, _, _ in smaps]
        i = bisect.bisect_right(stamps, t_ms) - 1
        return smaps[i][1] if i >= 0 else []

    # epochs from DAMON snapshots
    damon = read_access(d)
    sample_s = sample_seconds(d)
    writes = read_writes(d)
    epochs = []
    if damon:
        starts = [s for s, _, _ in damon]

        def snapshot_at(t):
            i = bisect.bisect_right(starts, t) - 1
            return damon[i][2] if i >= 0 and t < damon[i][1] else None

        t = int(damon[0][0] // epoch_ms * epoch_ms)
        while t < damon[-1][1]:
            mid = t + epoch_ms / 2
            regs = snapshot_at(mid)
            if regs is None:
                regs = snapshot_at(t)
            if regs:
                acc = epoch_accesses(regs, mapped_at(mid), regions, region_of, sample_s)
                if acc:
                    set_write_ratios(acc, writes, mid, regions)
                    epochs.append((t, acc))
            t += epoch_ms
    return regions, events, epochs, n_files


def populate_ranges(rss, populated):
    """Ranges to populate so that each mapping has its recorded Rss resident.

    smaps does not say which pages are resident, so new resident memory is
    taken from the lowest not yet populated addresses of the mapping."""
    out = []
    for start, end, rss_bytes in rss:
        have = sum(e - s for s, e in intersect([(start, end)], populated))
        need = rss_bytes - have
        if need < PAGE:
            continue
        for s, e in subtract([(start, end)], populated):
            take = min(e - s, -(-need // PAGE) * PAGE)
            out.append((s, s + take))
            need -= take
            if need <= 0:
                break
    return out


def epoch_accesses(damon_regions, mapped, regions, region_of, sample_s):
    """[(region, abs_start, abs_end, rate)] for the DAMON regions with
    accesses, clipped to what is mapped."""
    out = []
    for s, e, rate in damon_regions:
        if rate <= 0:
            continue
        for lo, hi in intersect([(s, e)], mapped):
            lo, hi = lo // PAGE * PAGE, -(-hi // PAGE) * PAGE
            r = region_of(lo)
            if r is None:
                continue
            hi = min(hi, regions[r][1])
            pages = (hi - lo) // PAGE
            # touches per second so that DAMON, sampling one page of the
            # region every sample interval, finds it accessed rate % of times
            out.append([r, lo, hi, round(rate / 100 * pages / sample_s), 1.0])
    return out


def set_write_ratios(acc, writes, mid, regions):
    """Written bytes of a mapping in the period, over the bytes accessed in it."""
    if writes is None:
        return
    period, samples = writes
    covering = [ts for ts in samples if ts - period < mid <= ts]
    if not covering:
        return
    for start, end, present, dirty in samples[covering[0]]:
        inside = [a for a in acc if a[1] < end and a[2] > start]
        accessed = sum(min(a[2], end) - max(a[1], start) for a in inside)
        if accessed:
            ratio = min(1.0, dirty * PAGE / accessed)
            for a in inside:
                a[4] = ratio


def write_profile(path, regions, events, epochs, epoch_ms, seed):
    lines = ['qmem-profile 1', 'epoch_ms %d' % epoch_ms, 'seed %d' % seed]
    # the third field is the region's base in the original; replay ignores it,
    # verify maps the original's addresses with it
    lines += ['region %d %d 0x%x' % (i, e - s, s) for i, (s, e) in enumerate(regions)]
    lines += ['%s %d %d %d %d' % (kind, ts, r, off, length)
              for ts, kind, r, off, length in events]
    for t, acc in epochs:
        lines.append('epoch %d' % t)
        for r, lo, hi, rate, wr in acc:
            lines.append('access %d %d %d %d %s' % (
                r, lo - regions[r][0], hi - lo, rate, ('%.3f' % wr).rstrip('0').rstrip('.')
                if wr < 1 else '1.0'))
    Path(path).write_text('\n'.join(lines) + '\n')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('record')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--epoch-ms', type=int, default=100)
    ap.add_argument('--seed', type=int, default=42)
    args = ap.parse_args()
    d = Path(args.record)
    if not (d / 'access.csv').exists():
        die('%s is not a qmem-record directory (no access.csv)' % d)
    if args.epoch_ms <= 0:
        die('--epoch-ms must be positive')

    regions, events, epochs, n_files = build(d, args.epoch_ms)
    write_profile(args.output, regions, events, epochs, args.epoch_ms, args.seed)
    print('mkprofile: %d regions, %d map/unmap/populate events, %d epochs, '
          'file mappings skipped: %d' % (len(regions), len(events), len(epochs),
                                         n_files), file=sys.stderr)


if __name__ == '__main__':
    main()
