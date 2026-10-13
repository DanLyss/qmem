"""intensity=passthrough (STUB): rate is DAMON's pseudo-frequency of the
region, access_rate / 100 / sampling interval, taken as touches per page
per second without any conversion. A place for estimators that will use
the PMU counter, samples and so on."""
from profile_v2 import Access, Epoch


def estimate(model, options, profile):
    for ob in model.observations:
        ep = Epoch(ob.t, ob.dur)
        for s in ob.seen:
            base = model.regions[s.region][0]
            ep.accesses.append(Access(s.region, s.start - base, s.end - s.start,
                                      rate=s.access_rate / 100 / model.sample_s))
        profile.epochs.append(ep)
