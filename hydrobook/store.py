"""SQLite capture, typed evidence projections, and receipt-level auditing."""
from contextlib import contextmanager
from hashlib import sha256
import json
import sqlite3

from .formats import FIELDS, encoded, integer, parse, require


SCOPES = ("capture", "transport", "posting")

SCHEMA = """
CREATE TABLE receipt(id INTEGER PRIMARY KEY, digest TEXT UNIQUE NOT NULL, raw BLOB NOT NULL,
   scope TEXT NOT NULL CHECK(scope IN ('capture','transport','posting')));
CREATE TABLE reading_index(receipt INTEGER NOT NULL REFERENCES receipt, ordinal INTEGER NOT NULL,
   kind TEXT NOT NULL, identifier TEXT NOT NULL, revision INTEGER NOT NULL,
   PRIMARY KEY(receipt,ordinal));
CREATE TABLE transport_volume(desk TEXT NOT NULL, shipment TEXT NOT NULL, part INTEGER NOT NULL,
   total INTEGER NOT NULL, digest TEXT NOT NULL, raw BLOB NOT NULL,
   receipt INTEGER NOT NULL REFERENCES receipt, PRIMARY KEY(desk,shipment,part));
CREATE TABLE volume_group(desk TEXT NOT NULL, shipment TEXT NOT NULL, gid TEXT NOT NULL,
   parts TEXT NOT NULL, members TEXT NOT NULL, replaces TEXT NOT NULL, requires TEXT NOT NULL,
   state TEXT NOT NULL CHECK(state IN ('HELD','ACTIVE','CONFLICT')),
   conflict TEXT, posting_receipt INTEGER REFERENCES receipt,
   PRIMARY KEY(desk,shipment,gid));
CREATE TABLE staged_record(desk TEXT NOT NULL, shipment TEXT NOT NULL, part INTEGER NOT NULL,
   ordinal INTEGER NOT NULL, kind TEXT NOT NULL, identifier TEXT NOT NULL, revision INTEGER NOT NULL,
   body TEXT NOT NULL, PRIMARY KEY(desk,shipment,part,kind,identifier,revision));
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
        if current == 2:
            return
        require(current in (0, 1), "unsupported database schema")
        if current == 0:
            for statement in SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            for kind in FIELDS:
                db.execute(f"""CREATE TABLE {kind}(id TEXT NOT NULL, rev INTEGER NOT NULL,
                    first_receipt INTEGER NOT NULL REFERENCES receipt, body TEXT NOT NULL,
                    PRIMARY KEY(id,rev))""")
        else:
            # Version 1 kept a single receipt stream.  Existing receipts were all
            # captured directly into the projections, so they keep the capture scope.
            db.execute("ALTER TABLE receipt ADD COLUMN scope TEXT NOT NULL DEFAULT 'capture'")
            db.execute("""CREATE TABLE volume_group(desk TEXT NOT NULL, shipment TEXT NOT NULL,
               gid TEXT NOT NULL, parts TEXT NOT NULL, members TEXT NOT NULL,
               replaces TEXT NOT NULL, requires TEXT NOT NULL,
               state TEXT NOT NULL CHECK(state IN ('HELD','ACTIVE','CONFLICT')),
               conflict TEXT, posting_receipt INTEGER REFERENCES receipt,
               PRIMARY KEY(desk,shipment,gid))""")
            db.execute("""CREATE TABLE staged_record(desk TEXT NOT NULL, shipment TEXT NOT NULL,
               part INTEGER NOT NULL, ordinal INTEGER NOT NULL, kind TEXT NOT NULL,
               identifier TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
               PRIMARY KEY(desk,shipment,part,kind,identifier,revision))""")
        db.execute("PRAGMA user_version=2")


def digest(raw):
    return sha256(raw).hexdigest()


def head(db):
    return db.execute("SELECT coalesce(max(id),0) FROM receipt").fetchone()[0]


def _project(db, receipt, records):
    """Insert projection rows, refusing to overwrite an existing revision body."""
    for row in records:
        kind, identity, rev, body = row["kind"], row["id"], row["rev"], encoded(row)
        previous = db.execute(f"SELECT body FROM {kind} WHERE id=? AND rev=?", (identity, rev)).fetchone()
        require(previous is None or previous[0] == body, f"conflicting revision: {kind} {identity} {rev}")
        if previous is None:
            db.execute(f"INSERT INTO {kind} VALUES(?,?,?,?)", (identity, rev, receipt, body))


def ingest(db, raw, scope="capture"):
    """Capture a receipt straight into the evidence projections.

    ``capture`` receipts are direct evidence intakes (the ``capture`` command and
    fixture imports).  ``posting`` receipts use :func:`log_posting` instead, so
    that held transport material can never enter a projection through this path.
    """
    require(scope in ("capture",), "ingest only accepts directly captured receipts")
    records = parse(raw)
    fingerprint = digest(raw)
    prior = db.execute("SELECT id FROM receipt WHERE digest=?", (fingerprint,)).fetchone()
    if prior is not None:
        return {"receipt": prior[0], "duplicate": True, "records": len(records)}
    receipt = db.execute(
        "INSERT INTO receipt(digest,raw,scope) VALUES(?,?,?)", (fingerprint, raw, scope)).lastrowid
    for line, row in enumerate(records, 1):
        db.execute("INSERT INTO reading_index VALUES(?,?,?,?,?)",
                   (receipt, line, row["kind"], row["id"], row["rev"]))
    _project(db, receipt, records)
    return {"receipt": receipt, "duplicate": False, "records": len(records)}


def log_transport(db, raw):
    """Register a received volume's raw receipt without projecting any record.

    Transport receipts prove intake only.  Their records stay staged until a
    complete revision group is posted, so a partial shipment can never move the
    evidence cut to a half-finished revision.
    """
    records = parse(raw)
    fingerprint = digest(raw)
    prior = db.execute("SELECT id, scope FROM receipt WHERE digest=?", (fingerprint,)).fetchone()
    if prior is not None:
        require(prior["scope"] == "transport", "receipt digest already exists outside transport intake")
        return {"receipt": prior["id"], "duplicate": True, "records": len(records)}
    receipt = db.execute(
        "INSERT INTO receipt(digest,raw,scope) VALUES(?,?,?)",
        (fingerprint, raw, "transport")).lastrowid
    for line, row in enumerate(records, 1):
        db.execute("INSERT INTO reading_index VALUES(?,?,?,?,?)",
                   (receipt, line, row["kind"], row["id"], row["rev"]))
    return {"receipt": receipt, "duplicate": False, "records": len(records)}


def log_posting(db, raw):
    """Post a complete revision group's records as one evidence-cut entry.

    Returns the posting receipt.  When the same raw bytes already arrived as a
    transport receipt, that receipt is promoted to the posting scope instead of
    creating a second receipt: intake proof and evidence entry stay separately
    queryable (transport tables versus the projections), while re-posting a
    group never takes effect twice.
    """
    records = parse(raw)
    fingerprint = digest(raw)
    prior = db.execute("SELECT id, scope FROM receipt WHERE digest=?", (fingerprint,)).fetchone()
    if prior is not None:
        if prior["scope"] == "transport":
            db.execute("UPDATE receipt SET scope='posting' WHERE id=?", (prior["id"],))
            _project(db, prior["id"], records)
            return {"receipt": prior["id"], "duplicate": False, "promoted": True,
                    "records": len(records)}
        return {"receipt": prior["id"], "duplicate": True, "promoted": False,
                "records": len(records)}
    receipt = db.execute(
        "INSERT INTO receipt(digest,raw,scope) VALUES(?,?,?)",
        (fingerprint, raw, "posting")).lastrowid
    for line, row in enumerate(records, 1):
        db.execute("INSERT INTO reading_index VALUES(?,?,?,?,?)",
                   (receipt, line, row["kind"], row["id"], row["rev"]))
    _project(db, receipt, records)
    return {"receipt": receipt, "duplicate": False, "promoted": False, "records": len(records)}


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
        if row["scope"] == "transport":
            result = log_transport(target, row["raw"])
        elif row["scope"] == "posting":
            result = log_posting(target, row["raw"])
        else:
            result = ingest(target, row["raw"])
        require(result["receipt"] == row["id"], "receipt ordinal mismatch")
        count += result["records"]
    for table in ("reading_index", *FIELDS):
        original = sorted(tuple(row) for row in db.execute(f"SELECT * FROM {table}"))
        rebuilt = sorted(tuple(row) for row in target.execute(f"SELECT * FROM {table}"))
        require(original == rebuilt, f"{table} differs from raw evidence")
    return count
