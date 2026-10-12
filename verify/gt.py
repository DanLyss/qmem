"""Ground truth of page accesses (common/qmem_gt.h) and exact-page metrics.

A ground-truth file says exactly which pages a run touched and wrote in
each time window. For the original and the replay, both mapped to region +
page + profile time, verify compares the sets of pages window by window:

  recall     |O & R| / |O|   pages the program touched that replay touched too
  precision  |O & R| / |R|   pages replay touched that the program touched too
  jaccard    |O & R| / |O | R|

summed over all windows, for touches and for writes, at several window
lengths; and the number of touches of each.
"""
import struct
from pathlib import Path

PAGE = 4096


def read_gt(path):
    """{'window_ms', 'start_boot_ns', 'ranges': [(start, npages)],
    'records': [(window, range, touches, writes, touched_int, written_int)]}"""
    data = Path(path).read_bytes()
    if data[:4] != b'QGT1':
        raise ValueError('%s is not a qmem ground-truth file' % path)
    window_ms, start_boot_ns, n, _ = struct.unpack_from('<IQII', data, 4)
    off = 24
    ranges = []
    for _ in range(n):
        ranges.append(struct.unpack_from('<QQ', data, off))
        off += 16
    records = []
    while off < len(data):
        w, r, touches, writes = struct.unpack_from('<IIQQ', data, off)
        off += 24
        size = (ranges[r][1] + 7) // 8
        touched = int.from_bytes(data[off:off + size], 'little')
        written = int.from_bytes(data[off + size:off + 2 * size], 'little')
        off += 2 * size
        records.append((w, r, touches, writes, touched, written))
    return {'window_ms': window_ms, 'start_boot_ns': start_boot_ns,
            'ranges': ranges, 'records': records}


def page_sets(gt, regions, shift_ms, window_ms):
    """{(region, window): (touched pages, written pages)} as int bitsets over
    the region's pages, in profile time, with windows of window_ms; and the
    number of touches per region.

    regions: {id: (base, size)}; shift_ms: profile time of the run's time 0."""
    order = sorted(regions.items(), key=lambda kv: kv[1][0])
    sets, touches = {}, {}
    for w, r, n, _, touched, written in gt['records']:
        start, npages = gt['ranges'][r]
        reg = next((rid for rid, (base, size) in order if base <= start < base + size), None)
        if reg is None:
            continue
        shift = (start - regions[reg][0]) // PAGE
        mid = w * gt['window_ms'] + gt['window_ms'] / 2 + shift_ms
        if mid < 0:
            continue
        key = (reg, int(mid // window_ms))
        t, wr = sets.get(key, (0, 0))
        sets[key] = (t | touched << shift, wr | written << shift)
        touches[reg] = touches.get(reg, 0) + n
    return sets, touches


def compare(o_sets, r_sets, regions_of_interest):
    def totals(idx):
        inter = osum = rsum = union = 0
        for key in set(o_sets) | set(r_sets):
            if key[0] not in regions_of_interest:
                continue
            o = o_sets.get(key, (0, 0))[idx]
            r = r_sets.get(key, (0, 0))[idx]
            inter += (o & r).bit_count()
            osum += o.bit_count()
            rsum += r.bit_count()
            union += (o | r).bit_count()
        return {'recall': inter / osum if osum else None,
                'precision': inter / rsum if rsum else None,
                'jaccard': inter / union if union else None,
                'pages_orig': osum, 'pages_replay': rsum}
    return {'touched': totals(0), 'written': totals(1)}


def gt_metrics(orig_path, replay_path, o_regions, r_regions, o_shift_ms,
               windows=(100, 1000)):
    """Exact-page metrics of the replay against the original.

    o_regions / r_regions: {id: (base, size)} of the original (profile) and
    the replay (layout); o_shift_ms: profile time of the original's ground
    truth time 0. The replay's ground truth runs on profile time."""
    og, rg = read_gt(orig_path), read_gt(replay_path)
    out = {'windows': []}
    for window_ms in windows:
        o_sets, o_touch = page_sets(og, o_regions, o_shift_ms, window_ms)
        r_sets, r_touch = page_sets(rg, r_regions, 0, window_ms)
        # the regions the program's ground truth covers
        interest = {k[0] for k in o_sets}
        m = compare(o_sets, r_sets, interest)
        m['window_ms'] = window_ms
        out['windows'].append(m)
    o_n = sum(o_touch.get(r, 0) for r in interest)
    r_n = sum(r_touch.get(r, 0) for r in interest)
    out['touches'] = {'orig': o_n, 'replay': r_n, 'ratio': r_n / o_n if o_n else None}
    return out
