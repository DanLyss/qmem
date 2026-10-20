"""Profile v2 (docs/formats/profile.md): the items and their writer and
reader."""
from dataclasses import dataclass, field


@dataclass
class Region:
    id: int
    size: int
    base: int = 0
    kind: str = 'anon'


@dataclass
class Event:
    kind: str           # map, unmap, populate
    t: int
    region: int
    off: int
    len: int


@dataclass
class Access:
    region: int
    off: int
    len: int
    rate: float = 0.0   # touches per page per second
    write: float = 0.0
    pattern: str = 'roundrobin'
    threads: int = 1


@dataclass
class Epoch:
    t: int
    dur: int
    accesses: list = field(default_factory=list)


@dataclass
class Counter:
    t: int
    name: str
    value: float


@dataclass
class Profile:
    meta: dict = field(default_factory=dict)
    regions: list = field(default_factory=list)
    events: list = field(default_factory=list)
    epochs: list = field(default_factory=list)
    counters: list = field(default_factory=list)


def num(x):
    if isinstance(x, int):
        return str(x)
    return ('%.6f' % x).rstrip('0').rstrip('.')


def kv(**pairs):
    return ' '.join('%s=%s' % (k, v) for k, v in pairs.items())


def write(path, prof):
    lines = ['qmem-profile 2']
    lines.append('meta ' + kv(**{k: v for k, v in prof.meta.items()
                                 if k not in ('epoch_ms', 'seed')}))
    lines.append('meta ' + kv(epoch_ms=prof.meta['epoch_ms'], seed=prof.meta['seed']))
    lines.append('')
    for r in prof.regions:
        lines.append('region ' + kv(id=r.id, size=r.size, kind=r.kind, base='0x%x' % r.base))
    for e in sorted(prof.events, key=lambda e: e.t):
        lines.append('%s %s' % (e.kind, kv(t=e.t, region=e.region, off=e.off, len=e.len)))
    for c in prof.counters:
        lines.append('counter ' + kv(t=c.t, name=c.name, value=num(c.value)))
    for ep in prof.epochs:
        lines.append('')
        lines.append('epoch ' + kv(t=ep.t, dur=ep.dur))
        for a in ep.accesses:
            extra = {} if a.threads == 1 else {'threads': a.threads}
            lines.append('access ' + kv(region=a.region, off=a.off, len=a.len,
                                        rate=num(a.rate), write=num(a.write),
                                        pattern=a.pattern, **extra))
    with open(path, 'w') as f:
        f.write('\n'.join(lines) + '\n')


def read(path):
    """Profile v2 -> Profile (for tools and tests; replay has its own loader)."""
    prof, epoch = Profile(), None
    with open(path) as f:
        lines = [l.split('#')[0].split() for l in f]
    lines = [l for l in lines if l]
    if lines[:1] != [['qmem-profile', '2']]:
        raise ValueError('%s: not a profile v2' % path)
    for f in lines[1:]:
        k = dict(x.lstrip('!').split('=', 1) for x in f[1:])
        if f[0] == 'meta':
            prof.meta.update(k)
        elif f[0] == 'region':
            prof.regions.append(Region(int(k['id']), int(k['size']),
                                       int(k.get('base', '0'), 16), k.get('kind', 'anon')))
        elif f[0] in ('map', 'unmap', 'populate'):
            prof.events.append(Event(f[0], int(k['t']), int(k['region']),
                                     int(k['off']), int(k['len'])))
        elif f[0] == 'epoch':
            epoch = Epoch(int(k['t']), int(k.get('dur', prof.meta.get('epoch_ms', 100))))
            prof.epochs.append(epoch)
        elif f[0] == 'access':
            epoch.accesses.append(Access(int(k['region']), int(k['off']), int(k['len']),
                                         float(k['rate']), float(k.get('write', 0)),
                                         k.get('pattern', 'roundrobin'),
                                         int(k.get('threads', 1))))
        elif f[0] == 'counter':
            prof.counters.append(Counter(int(k['t']), k['name'], float(k['value'])))
    return prof
