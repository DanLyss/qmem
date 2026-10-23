#!/usr/bin/env python3
"""Build a qmem-replay profile (v2) from a qmem-record directory.

usage: qmem-build.py <record dir> -o <profile> [--epoch-ms 100] [--seed 42]
                     [--intensity passthrough] [--write all]
                     [--timing roundrobin]

readers (one per record source) -> model -> estimators
-> writer. See docs/skeleton.md, section 3, and docs/formats/profile.md.
Only anonymous memory is replayed; file mappings are counted and skipped.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import estimators                       # noqa: E402
import profile_v2 as pf                 # noqa: E402
from model import Model                 # noqa: E402
from readers.layout import Record       # noqa: E402
from util import die, warn              # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('record')
    ap.add_argument('-o', '--output', required=True)
    ap.add_argument('--epoch-ms', type=int, default=100)
    ap.add_argument('--seed', type=int, default=42)
    for kind in estimators.CHOOSABLE:
        ap.add_argument('--' + kind, default=estimators.DEFAULTS[kind],
                        choices=estimators.available(kind))
    args = ap.parse_args()
    if args.epoch_ms <= 0:
        ap.error('--epoch-ms must be positive')
    if not Path(args.record).is_dir():
        die('%s is not a directory' % args.record)

    record = Record(args.record)
    model = Model(record, args.epoch_ms)
    choice = {k: getattr(args, k) for k in estimators.CHOOSABLE}
    prof = pf.Profile(meta={'source': '_'.join(record.name.split()) or 'record',
                            'builder': 2, **choice,
                            'epoch_ms': args.epoch_ms, 'seed': args.seed})
    for name in estimators.pipeline(choice):
        estimators.load(name).estimate(model, args, prof)
    pf.write(args.output, prof)
    warn('%d regions, %d map/unmap/populate events, %d epochs, file mappings '
         'skipped: %d' % (len(prof.regions), len(prof.events), len(prof.epochs),
                          model.n_files))


if __name__ == '__main__':
    main()
