"""SQLite capture, typed evidence projections, and receipt-level auditing."""
from contextlib import contextmanager
from hashlib import sha256
import json
import sqlite3

from .formats import FIELDS, encoded, integer, parse, require


SCHEMA = """
CREATE TABLE receipt(id INTEGER PRIMARY KEY, digest TEXT UNIQUE NOT NULL, raw BLOB NOT NULL);
CREATE TABLE reading_index(receipt INTEGER NOT NULL REFERENCES receipt, ordinal INTEGER NOT NULL,
   kind TEXT NOT NULL, identifier TEXT NOT NULL, revision INTEGER NOT NULL,
   PRIMARY KEY(receipt,ordinal));
CREATE TABLE transport_volume(desk TEXT NOT NULL, shipment TEXT NOT NULL, part INTEGER NOT NULL,
   total INTEGER NOT NULL, digest TEXT NOT NULL, raw BLOB NOT NULL,
   receipt INTEGER NOT NULL REFERENCES receipt, PRIMARY KEY(desk,shipment,part));
CREATE TABLE issue(id INTEGER PRIMARY KEY, edition TEXT NOT NULL, cut INTEGER NOT NULL REFERENCES receipt,
   prior INTEGER UNIQUE REFERENCES issue, digest TEXT NOT NULL, body TEXT NOT NULL,
   UNIQUE(edition,cut));
"""


def connect(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=5000")
    return db


@contextmanager
def transaction(db, *, writing=False):
    db.execute("BEGIN IMMEDIATE" if writing else "BEGIN")
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def initialize(db):
    with transaction(db, writing=True):
        current = db.execute("PRAGMA user_version").fetchone()[0]
        if current == 1:
            return
        require(current == 0, "unsupported database schema")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                db.execute(statement)
        for kind in FIELDS:
            db.execute(f"""CREATE TABLE {kind}(id TEXT NOT NULL, rev INTEGER NOT NULL,
                first_receipt INTEGER NOT NULL REFERENCES receipt, body TEXT NOT NULL,
                PRIMARY KEY(id,rev))""")
        db.execute("PRAGMA user_version=1")


def digest(raw):
    return sha256(raw).hexdigest()


def head(db):
    return db.execute("SELECT coalesce(max(id),0) FROM receipt").fetchone()[0]


def ingest(db, raw):
    records = parse(raw)
    fingerprint = digest(raw)
    prior = db.execute("SELECT id FROM receipt WHERE digest=?", (fingerprint,)).fetchone()
    if prior is not None:
        return {"receipt": prior[0], "duplicate": True, "records": len(records)}
    receipt = db.execute("INSERT INTO receipt(digest,raw) VALUES(?,?)", (fingerprint, raw)).lastrowid
    for line, row in enumerate(records, 1):
        kind, identity, rev, body = row["kind"], row["id"], row["rev"], encoded(row)
        previous = db.execute(f"SELECT body FROM {kind} WHERE id=? AND rev=?", (identity, rev)).fetchone()
        require(previous is None or previous[0] == body, f"conflicting revision: {kind} {identity} {rev}")
        if previous is None:
            db.execute(f"INSERT INTO {kind} VALUES(?,?,?,?)", (identity, rev, receipt, body))
        db.execute("INSERT INTO reading_index VALUES(?,?,?,?,?)", (receipt, line, kind, identity, rev))
    return {"receipt": receipt, "duplicate": False, "records": len(records)}


class Snapshot:
    def __init__(self, db, cut=None):
        self.db = db
        latest = head(db)
        self.cut = latest if cut is None else integer(cut, 0, latest)
        self.tables = {}

    def active(self, kind):
        require(kind in FIELDS, "unknown projection")
        if kind not in self.tables:
            rows = self.db.execute(f"""SELECT body, first_receipt FROM {kind} AS row
                WHERE first_receipt<=? AND NOT EXISTS(SELECT 1 FROM {kind} AS other
                WHERE other.id=row.id AND other.rev>row.rev AND other.first_receipt<=?) ORDER BY id""",
                                   (self.cut, self.cut))
            active = {}
            for row in rows:
                entry = json.loads(row[0])
                if entry["state"] == "active":
                    active[entry["id"]] = {**entry, "receipt": row[1]}
            self.tables[kind] = active
        return self.tables[kind]


def reference(row):
    return [row["kind"], row["id"], row["rev"], row["receipt"]]


def refs(rows):
    return [reference(row) for row in rows]


def at(row, day):
    return row["start"] <= day < row["end"]


def replay_receipts(db, target):
    count = 0
    for row in db.execute("SELECT * FROM receipt ORDER BY id"):
        require(digest(row["raw"]) == row["digest"], f"raw receipt {row['id']} digest mismatch")
        result = ingest(target, row["raw"])
        require(result["receipt"] == row["id"], "receipt ordinal mismatch")
        count += result["records"]
    for table in ("reading_index", *FIELDS):
        original = sorted(tuple(row) for row in db.execute(f"SELECT * FROM {table}"))
        rebuilt = sorted(tuple(row) for row in target.execute(f"SELECT * FROM {table}"))
        require(original == rebuilt, f"{table} differs from raw evidence")
    return count
