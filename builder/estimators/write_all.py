"""write=all (STUB): every access writes, write=1."""


def estimate(model, options, profile):
    for ep in profile.epochs:
        for a in ep.accesses:
            a.write = 1.0
