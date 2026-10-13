#!/usr/bin/env python3
"""Exact-page recall / precision of the mixed workload's replay, per zone.

usage: zones.py <cycle dir>
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'legacy' / 'verify'))
from gt import page_sets, read_gt  # noqa: E402

d = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)
og, rg = read_gt(d / 'gt_orig.bin'), read_gt(d / 'gt_replay.bin')
o_regions = {int(f[1]): (int(f[3], 16), int(f[2])) for f in
             (l.split() for l in (d / 'profile.txt').read_text().splitlines())
             if f[:1] == ['region'] and len(f) > 3}
r_regions = {int(f[1]): (int(f[2], 16), int(f[3])) for f in
             (l.split() for l in (d / 'layout.txt').read_text().splitlines())
             if f[:1] == ['region']}
meta = json.loads((d / 'orig' / 'meta.json').read_text())
o_shift = og['start_boot_ns'] / 1e6 - meta['start_uptime'] * 1000

start, n = og['ranges'][0]
reg = next(r for r, (b, s) in o_regions.items() if b <= start < b + s)
off = (start - o_regions[reg][0]) // 4096
zones = {'A Zipf': (0, n // 10), 'B window': (n // 10, 4 * n // 10),
         'C bursts': (4 * n // 10, 6 * n // 10), 'D stride': (6 * n // 10, 9 * n // 10),
         'E random only': (9 * n // 10, n)}

print('%-14s %7s %9s %7s %9s   (touched pages, 100 ms | 1 s)' %
      ('zone', 'recall', 'precision', 'recall', 'precision'))
for name, (a, b) in zones.items():
    mask = ((1 << (b - a)) - 1) << (a + off)
    row = []
    for window_ms in (100, 1000):
        o_sets, _ = page_sets(og, o_regions, o_shift, window_ms)
        r_sets, _ = page_sets(rg, r_regions, 0, window_ms)
        inter = osum = rsum = 0
        for key in set(o_sets) | set(r_sets):
            if key[0] != reg:
                continue
            o = o_sets.get(key, (0, 0))[0] & mask
            r = r_sets.get(key, (0, 0))[0] & mask
            inter += (o & r).bit_count()
            osum += o.bit_count()
            rsum += r.bit_count()
        row += [inter / osum if osum else 0, inter / rsum if rsum else 0]
    print('%-14s %6.1f%% %8.1f%% %6.1f%% %8.1f%%' % (name, *(100 * v for v in row)))
