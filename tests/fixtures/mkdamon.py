#!/usr/bin/env python3
"""Write a small damon.data with a known access pattern, using damo's own
record classes, so `damo report` can be tested without root or DAMON.

usage: mkdamon.py <out file>

Pattern: 3 snapshots of 100 ms each, two regions per snapshot:
  [0x1000, 0x5000)    hot:  20 of 20 samples accessed, age 3 aggregations
  [0x5000, 0x100000)  cold: 0 samples accessed, age 7 aggregations
"""
import importlib.util
import os
import sys

spec = importlib.util.find_spec('damo')
if spec is None:
    sys.exit('damo python package not found')
sys.path.insert(0, os.path.dirname(spec.origin))

import _damon  # noqa: E402
import _damo_records  # noqa: E402

SNAPSHOT_NS = 100_000_000
NR_SNAPSHOTS = 3
HOT = (0x1000, 0x5000)
COLD = (0x5000, 0x100000)


def main():
    intervals = _damon.DamonIntervals('5ms', '100ms', '1s')
    record = _damo_records.DamonRecord(0, 0, intervals, None, 0, [])
    for i in range(NR_SNAPSHOTS):
        regions = [
            _damon.DamonRegion(HOT[0], HOT[1], 20, _damon.unit_samples,
                               3, _damon.unit_aggr_intervals),
            _damon.DamonRegion(COLD[0], COLD[1], 0, _damon.unit_samples,
                               7, _damon.unit_aggr_intervals),
        ]
        record.snapshots.append(_damo_records.DamonSnapshot(
            i * SNAPSHOT_NS, (i + 1) * SNAPSHOT_NS, regions, None))
    err = _damo_records.write_damon_records(
        [record], sys.argv[1], _damo_records.file_type_json_compressed)
    if err:
        sys.exit(err)


if __name__ == '__main__':
    main()
