from contextlib import closing
import json
from pathlib import Path
import unittest

from hydrobook.store import Snapshot, connect, ingest, initialize, transaction


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


class ArchiveCase(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(":memory:")))
        initialize(self.db)
        self.import_file("season.json")

    def import_file(self, name):
        with transaction(self.db, writing=True):
            return ingest(self.db, (FIXTURES / name).read_bytes())

    def input(self, *events):
        with transaction(self.db, writing=True):
            return ingest(self.db, json.dumps(events).encode())

    def snapshot(self, cut=None):
        return Snapshot(self.db, cut)

    def revise(self, kind, identity, **changes):
        row = self.snapshot().active(kind)[identity]
        row.pop("receipt")
        row["rev"] += 1
        row.update(changes)
        return self.input(row)


def event(kind, identity, **fields):
    return dict(kind=kind, id=identity, rev=1, state="active", **fields)
