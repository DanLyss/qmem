#!/usr/bin/env python3
"""Write the 100 profiles of the Linux/phone comparison into bench/profiles.

usage: bench/gen.py

Each profile checks one thing of replay (size, rate, number of ranges,
overload, pattern and write share, map/unmap churn, populate, epoch length,
many regions) or is a damoLoad memtest config. Sizes are multiples of 64 KiB,
so the profiles load with 4 and 16 KiB pages. Most need at most 256 MiB;
the 5500-region memtest config needs about 450 MiB.
"""
import csv
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / 'profiles'
MB = 1 << 20
CONFIGS = ROOT / 'third_party' / 'damoLoad' / 'memtest' / 'configs'


class Profile:
    def __init__(self, epoch_ms=100):
        self.lines = ['qmem-profile 2', 'meta epoch_ms=%d' % epoch_ms]
        self.n_regions = 0

    def region(self, size):
        self.lines.append('region id=%d size=%d' % (self.n_regions, size))
        self.n_regions += 1
        return self.n_regions - 1

    def event(self, kind, t, r, off, length):
        self.lines.append('%s t=%d region=%d off=%d len=%d' % (kind, t, r, off, length))

    def epoch(self, t, dur=None):
        self.lines.append('epoch t=%d' % t + ('' if dur is None else ' dur=%d' % dur))

    def access(self, r, off, length, rate, write=None, pattern=None):
        line = 'access region=%d off=%d len=%d rate=%g' % (r, off, length, rate)
        if write is not None:
            line += ' write=%g' % write
        if pattern:
            line += ' pattern=' + pattern
        self.lines.append(line)

    def text(self):
        return '\n'.join(self.lines) + '\n'


def whole(size, rate, dur_ms=2000, epoch_ms=100, write=None, pattern=None, populate=0):
    """One region mapped at 0, optionally populated, one access per epoch."""
    p = Profile(epoch_ms)
    r = p.region(size)
    p.event('map', 0, r, 0, size)
    if populate:
        p.event('populate', 0, r, 0, populate)
    for t in range(0, dur_ms, epoch_ms):
        p.epoch(t)
        p.access(r, 0, size, rate, write, pattern)
    return p


def split(size, k, rate, dur_ms):
    """One region cut into k equal ranges, each its own access."""
    p = Profile()
    r = p.region(size)
    p.event('map', 0, r, 0, size)
    step = size // k // 65536 * 65536 or 65536
    k = size // step
    for t in range(0, dur_ms, 100):
        p.epoch(t)
        for i in range(k):
            p.access(r, i * step, step, rate)
    return p


def churn(period_ms, touch, dur_ms=2000, size=64 * MB):
    """Halves of a region mapped and unmapped in turn every period_ms."""
    p = Profile()
    r = p.region(size)
    half = size // 2
    p.event('map', 0, r, 0, half)
    events = []
    for i, t in enumerate(range(period_ms, dur_ms, period_ms)):
        a, b = (0, half) if i % 2 == 0 else (half, 0)
        events += [('map', t, r, a, half), ('unmap', t, r, b, half)]
    for e in events:
        p.event(*e)
    if touch:
        for t in range(0, dur_ms, 100):
            p.epoch(t)
            p.access(r, 0, size, 5)
    return p


def populate(frac, staged=False, size=128 * MB, dur_ms=2000):
    p = Profile()
    r = p.region(size)
    p.event('map', 0, r, 0, size)
    if staged:
        step = size // 8
        for i in range(8):
            p.event('populate', i * 250, r, i * step, step)
    elif frac:
        p.event('populate', 0, r, 0, int(size * frac) // 65536 * 65536)
    for t in range(0, dur_ms, 100):
        p.epoch(t)
        p.access(r, 0, MB, 10)             # a small hot range, the rest only resident
    return p


def regions(n, dur_ms=2000):
    p = Profile()
    for _ in range(n):
        r = p.region(MB)
        p.event('map', 0, r, 0, MB)
    for t in range(0, dur_ms, 100):
        p.epoch(t)
        for r in range(n):
            p.access(r, 0, MB, 5)
    return p


def memtest(name):
    out = subprocess.run([sys.executable, str(ROOT / 'tools' / 'memtest2profile.py'),
                          str(CONFIGS / name), '-o', '/dev/stdout', '--duration', '2'],
                         capture_output=True, text=True, check=True)
    return out.stdout


def profiles():
    """(category, parameter, profile text, what it checks)"""
    for mb in (1, 2, 4, 8, 16, 32, 64, 96, 128, 256):
        yield ('size', '%dMB' % mb,
               whole(mb * MB, 5, write=0.3, populate=mb * MB // 2).text(),
               'RSS and page faults grow with the size')
    for rate in (0.01, 0.05, 0.1, 0.5, 1, 5, 10, 50, 100, 500, 1000, 5000, 10000, 50000,
                 100000):
        yield ('rate', '%g' % rate, whole(16 * MB, rate).text(),
               'carry at low rates, dropped ticks at high ones')
    for k in (1, 2, 5, 10, 20, 50, 100, 128, 200, 256):
        yield ('ranges', str(k), split(16 * MB, k, 200, 2000).text(),
               'the same touches spread over k ranges')
    for k in (1, 4, 8, 16, 64, 128, 256, 1024, 2048, 4096):
        yield ('overload', str(k), split(256 * MB if k > 256 else 16 * MB, k, 50000, 500).text(),
               'overload over k ranges: the deadline is checked per range')
    for pattern in ('roundrobin', 'random', 'poisson'):
        for write in (0, 0.3, 1):
            yield ('pattern', '%s_w%g' % (pattern, write),
                   whole(8 * MB, 20, write=write, pattern=pattern).text(),
                   'generators and the write share')
    for write in (0, 0.3, 1):
        yield ('pattern', 'random64_w%g' % write,
               whole(64 * MB, 5, write=write, pattern='random').text(),
               'random over a large range')
    for period in (10, 20, 50, 100, 200, 500):
        for touch in (False, True):
            yield ('churn', '%dms_%s' % (period, 'touch' if touch else 'idle'),
                   churn(period, touch).text(), 'map/unmap every period, skipped touches')
    for frac in (0, 0.125, 0.25, 0.5, 0.75, 1):
        yield ('populate', '%g' % frac, populate(frac).text(), 'RSS from populate alone')
    yield ('populate', 'staged', populate(0, staged=True).text(), 'populate in 8 steps')
    yield ('populate', 'staged_x2', populate(0, staged=True, size=256 * MB).text(),
           'populate in 8 steps, 256 MiB')
    for ep in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000):
        yield ('epoch', '%dms' % ep, whole(4 * MB, 0.5, epoch_ms=ep).text(),
               'a slow range over short epochs (carry across epochs)')
    for n in (2, 4, 8, 16, 32, 64, 128, 200):
        yield ('regions', str(n), regions(n).text(), 'many separate regions (VMAs)')
    for name in ('17_zipf_steep', '27_large_region_uniform', '35_moving_hotspot',
                 '46_split_merge_stress', '101_5500reg_realistic_app'):
        yield ('memtest', name.split('_', 1)[1], memtest(name + '.json'),
               'damoLoad memtest config, first 2 s')


def main():
    OUT.mkdir(exist_ok=True)
    for old in OUT.glob('*.txt'):
        old.unlink()
    rows = []
    for i, (cat, param, text, what) in enumerate(profiles()):
        name = '%03d_%s_%s' % (i, cat, param.replace('.', 'p'))
        (OUT / (name + '.txt')).write_text(text)
        rows.append({'id': i, 'name': name, 'category': cat, 'param': param, 'checks': what})
    with open(HERE / 'profiles.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print('%d profiles in %s' % (len(rows), OUT))


if __name__ == '__main__':
    main()
