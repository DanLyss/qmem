"""timing=roundrobin (STUB): every access walks its pages in a circle."""


def estimate(model, options, profile):
    for ep in profile.epochs:
        for a in ep.accesses:
            a.pattern = 'roundrobin'
