"""resident: populate events so that each mapping has its recorded Rss
resident.

smaps does not say which pages are resident, so new resident memory is
taken from the lowest not yet populated addresses of the mapping."""
from estimators.lifecycle import when
from intervals import intersect, merge, subtract
from profile_v2 import Event
from util import PAGE


def populate_ranges(rss, populated):
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


def estimate(model, options, profile):
    populated, prev_ts = [], None
    for ts, anon, rss in model.smaps:
        t = when(prev_ts, ts)
        prev_ts = ts
        populated = intersect(populated, anon)
        for s, e in populate_ranges(rss, populated):
            r = model.region_of(s)
            profile.events.append(Event('populate', t, r, s - model.regions[r][0], e - s))
            populated = merge(populated + [(s, e)])
