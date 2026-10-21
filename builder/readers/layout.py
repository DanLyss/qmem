"""Where a record keeps each source's files (docs/formats/record.md):
record.json and a subdirectory per source."""
import json
from pathlib import Path

from util import die


class Record:
    def __init__(self, d):
        self.dir = Path(d)
        self.name = self.dir.resolve().name
        try:
            meta = json.loads((self.dir / 'record.json').read_text())
        except FileNotFoundError:
            die('%s is not a qmem-record directory (no record.json)' % d)
        if meta.get('format') != 'qmem-record' or meta.get('version') != 2:
            die('%s: not a record v2' % d)
        self.sources = meta['sources']
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
        p = self.dir / source / name
        return p if p.exists() else None

    def smaps_dir(self):
        return self.dir / 'smaps'
