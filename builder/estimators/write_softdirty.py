"""write=softdirty (STUB, the behaviour of iteration 1): the write field is
the written bytes of a mapping in the softdirty period over the bytes
accessed in it; 1.0 (everything is written) without softdirty at all."""
from util import PAGE


def estimate(model, options, profile):
    for ep in profile.epochs:
        spans = []
        for a in ep.accesses:
            lo = model.regions[a.region][0] + a.off
            spans.append([a, lo, lo + a.len])
        if model.writes is None:
            for a in ep.accesses:
                a.write = 1.0
            continue
        set_ratios(spans, model.writes, ep.t + ep.dur / 2)


def set_ratios(spans, writes, mid):
    period, samples = writes
    covering = [ts for ts in samples if ts - period < mid <= ts]
    # no sample covers the epoch (e.g. the application exited before the
    # next one): the nearest period tells more than assuming every access
    # writes
    ts = covering[0] if covering else min(samples, key=lambda t: abs(t - period / 2 - mid))
    for a, _, _ in spans:
        a.write = 1.0
    for start, end, present, dirty in samples[ts]:
        inside = [sp for sp in spans if sp[1] < end and sp[2] > start]
        accessed = sum(min(hi, end) - max(lo, start) for _, lo, hi in inside)
        if accessed:
            ratio = min(1.0, dirty * PAGE / accessed)
            for a, _, _ in inside:
                a.write = ratio
