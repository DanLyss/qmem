"""The builder's model: what the readers found, before any estimator.

  regions       anonymous regions, the merge of every mapping ever seen
  smaps         [(ts_ms, mapped intervals, [(start, end, rss_bytes)])]
  observations  per epoch, what DAMON saw: [Observation], see below
  sample_s      DAMON's sampling interval
  writes        softdirty samples or None
"""
import bisect
from dataclasses import dataclass, field

from intervals import intersect, merge
from readers import damon, smaps, softdirty
from util import PAGE, warn


@dataclass
class Seen:
    """Accesses DAMON saw in a page-aligned part of one region."""
    region: int
    start: int          # absolute addresses in the original
    end: int
    access_rate: float  # DAMON's, percent of samples


@dataclass
class Observation:
    t: int
    dur: int
    seen: list = field(default_factory=list)


class Model:
    def __init__(self, record, epoch_ms):
        self.record = record
        self.epoch_ms = epoch_ms
        self.smaps, self.n_files = smaps.read(record)
        self.regions = merge(i for _, anon, _ in self.smaps for i in anon)
        self._starts = [s for s, _ in self.regions]
        self.sample_s = damon.sample_seconds(record)
        self.writes = softdirty.read(record)
        snaps = damon.read(record)
        if snaps is None:
            warn('no damon source: the profile has no epochs')
        self.observations = self._observe(snaps or [])

    def region_of(self, addr):
        i = bisect.bisect_right(self._starts, addr) - 1
        return i if i >= 0 and addr < self.regions[i][1] else None

    def mapped_at(self, t_ms):
        stamps = [ts for ts, _, _ in self.smaps]
        i = bisect.bisect_right(stamps, t_ms) - 1
        return self.smaps[i][1] if i >= 0 else []

    def _observe(self, snaps):
        """One Observation per epoch from the DAMON snapshot at its middle;
        epochs with nothing accessed are left out."""
        if not snaps:
            return []
        epoch_ms = self.epoch_ms
        starts = [s for s, _, _ in snaps]
        held = 0

        def snapshot_at(t):
            i = bisect.bisect_right(starts, t) - 1
            return snaps[i][2] if i >= 0 and t < snaps[i][1] else None

        out = []
        t = int(snaps[0][0] // epoch_ms * epoch_ms)
        while t < snaps[-1][1]:
            mid = t + epoch_ms / 2
            regs = snapshot_at(mid)
            if regs is None:
                regs = snapshot_at(t)
            if regs is None:
                # no snapshot covers t (DAMON missed some: the system
                # stalled): keep the last known pattern rather than idle
                i = bisect.bisect_right(starts, mid) - 1
                if i >= 0:
                    held += epoch_ms
                    regs = snaps[i][2]
            if regs:
                seen = self._clip(regs, self.mapped_at(mid))
                if seen:
                    out.append(Observation(t, epoch_ms, seen))
            t += epoch_ms
        if held:
            warn('%d ms without DAMON snapshots (gap); the last pattern is '
                 'held there' % held)
        return out

    def _clip(self, damon_regions, mapped):
        """DAMON regions with accesses, clipped to what is mapped."""
        out = []
        for s, e, rate in damon_regions:
            if rate <= 0:
                continue
            for lo, hi in intersect([(s, e)], mapped):
                lo, hi = lo // PAGE * PAGE, -(-hi // PAGE) * PAGE
                r = self.region_of(lo)
                if r is None:
                    continue
                out.append(Seen(r, lo, min(hi, self.regions[r][1]), rate))
        return out
