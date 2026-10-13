"""Record time -> the application's (active) time."""
import csv


def unpause(record):
    """A function from record time (ms) to active time: VM pauses found by
    qmem-record (pauses.csv) are taken out. The application never saw them,
    but every record timestamp after a pause is late by it."""
    path = record.pauses()
    pauses = []
    if path:
        with open(path) as f:
            pauses = sorted((int(r['ts_ms']), int(r['pause_ms'])) for r in csv.DictReader(f))
    if not pauses:
        return lambda t: t

    def active(t):
        shift = 0
        for ts, length in pauses:
            if t <= ts:
                break
            shift += min(length, t - ts)
        return t - shift
    return active
