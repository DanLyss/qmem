"""[start, end) address intervals."""


def merge(intervals):
    """Sort and merge overlapping or touching intervals."""
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
