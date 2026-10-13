"""source softdirty: written pages of each mapping per sampling period."""
import csv

from readers.timebase import unpause


def read(record):
    """(period_ms, {ts_ms: [(start, end, present, dirty)]}) or None."""
    path = record.path('softdirty', 'writes.csv')
    if path is None:
        return None
    samples = {}
    active = unpause(record)
    with open(path) as f:
        for r in csv.DictReader(f):
            samples.setdefault(active(int(r['ts_ms'])), []).append(
                (int(r['start']), int(r['end']), int(r['present_pages']),
                 int(r['dirty_pages'])))
    if not samples:
        return None
    period = record.param('softdirty', 'period_ms') or 0
    if not period:
        stamps = sorted(samples)
        period = stamps[1] - stamps[0] if len(stamps) > 1 else stamps[0]
    return period, samples
