"""source smaps: anonymous mappings and their Rss over time."""
import re

from intervals import merge
from readers.timebase import unpause
from util import die

VMA = re.compile(r'^([0-9a-f]+)-([0-9a-f]+) (\S+) \S+ \S+ \S+\s*(.*)$')


def is_anon(perms, name):
    if perms.startswith('---'):
        return False            # guard pages: nothing to replay
    return name in ('', '[heap]', '[stack]') or name.startswith('[anon')


def read(record):
    """[(ts_ms, merged anonymous intervals, [(start, end, rss_bytes)])],
    number of file mappings."""
    snaps, files = [], set()
    active = unpause(record)
    for f in sorted(record.smaps_dir().glob('*.txt'), key=lambda f: int(f.stem)):
        anon, rss, cur = [], [], None
        for line in f.read_text().splitlines():
            m = VMA.match(line)
            if m:
                start, end, perms, name = int(m[1], 16), int(m[2], 16), m[3], m[4].strip()
                cur = None
                if is_anon(perms, name):
                    anon.append((start, end))
                    cur = [start, end, 0]
                    rss.append(cur)
                elif name and not name.startswith('['):
                    files.add((start, end, name))
            elif cur is not None and line.startswith('Rss:'):
                cur[2] = int(line.split()[1]) * 1024
        snaps.append((active(int(f.stem)), merge(anon), [tuple(r) for r in rss]))
    if not snaps:
        die('no smaps snapshots in %s' % record.dir)
    return snaps, len(files)
