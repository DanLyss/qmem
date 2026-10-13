"""Where a record keeps each source's files (docs/formats/record.md)."""
import json
from pathlib import Path

from util import die

# record v1: everything in the top directory
V1_FILES = {
    ('damon', 'access.csv'): 'access.csv',
    ('damon', 'damon.data.kdamonds'): 'damon.data.kdamonds',
    ('softdirty', 'writes.csv'): 'writes.csv',
}


class Record:
    def __init__(self, d):
        self.dir = Path(d)
        self.name = self.dir.resolve().name
        if (self.dir / 'record.json').exists():
            meta = json.loads((self.dir / 'record.json').read_text())
            if meta.get('format') != 'qmem-record' or meta.get('version') != 2:
                die('%s: unknown record.json format' % d)
            self.version = 2
            self.sources = meta['sources']
        elif (self.dir / 'meta.json').exists() or (self.dir / 'access.csv').exists():
            self.version = 1
            meta = {}
            if (self.dir / 'meta.json').exists():
                meta = json.loads((self.dir / 'meta.json').read_text())
            self.sources = {
                'damon': {'status': 'ok'},
                'smaps': {'status': 'ok'},
                'softdirty': ({'status': 'ok', 'period_ms': meta.get('softdirty_ms') or 0}
                              if (self.dir / 'writes.csv').exists() else {'status': 'off'}),
            }
        else:
            die('%s is not a qmem-record directory (no record.json)' % d)
        if not (self.dir / 'smaps').is_dir():
            die('%s has no smaps snapshots' % d)

    def has(self, source):
        return self.sources.get(source, {}).get('status') == 'ok'

    def param(self, source, key, default=None):
        return self.sources.get(source, {}).get(key, default)

    def path(self, source, name):
        """Path of a source's file, or None without the source."""
        if not self.has(source):
            return None
        if self.version == 1:
            p = self.dir / V1_FILES.get((source, name), name)
        else:
            p = self.dir / source / name
        return p if p.exists() else None

    def smaps_dir(self):
        return self.dir / 'smaps'

    def pauses(self):
        p = self.dir / 'pauses.csv'
        return p if p.exists() else None
