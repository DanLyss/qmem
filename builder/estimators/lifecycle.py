"""lifecycle: regions and their map/unmap events from the smaps snapshots.

What the first snapshot shows existed from the start; a change between two
snapshots is put halfway between them."""
from intervals import subtract
from profile_v2 import Event, Region


def when(prev_ts, ts):
    return 0 if prev_ts is None else (prev_ts + ts) // 2


def estimate(model, options, profile):
    profile.regions += [Region(i, e - s, s) for i, (s, e) in enumerate(model.regions)]
    prev, prev_ts = [], None
    for ts, anon, _ in model.smaps:
        t = when(prev_ts, ts)
        for kind, parts in (('unmap', subtract(prev, anon)), ('map', subtract(anon, prev))):
            for s, e in parts:
                r = model.region_of(s)
                profile.events.append(Event(kind, t, r, s - model.regions[r][0], e - s))
        prev, prev_ts = anon, ts
