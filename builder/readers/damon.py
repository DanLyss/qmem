"""source damon: DAMON snapshots (access.csv) and its sampling interval."""
import csv
import json
import re

from util import die


def read(record):
    """[(start_ms, end_ms, [(start, end, access_rate)])] sorted by time, or
    None without the source."""
    path = record.path('damon', 'access.csv')
    if path is None:
        return None
    snaps = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            key = (int(r['snapshot_start_ns']) / 1e6,
                   int(r['snapshot_end_ns']) / 1e6)
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


def sample_seconds(record):
    path = record.path('damon', 'damon.data.kdamonds')
    if path:
        found = find_key(json.loads(path.read_text()), 'sample_us')
        if found is not None:
            return to_seconds(found)
    return 0.005                # DAMON's default
