"""Capture blind bottle labels before they are delivered to a bench."""
from .formats import day, require, text


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS sample_label (
        label TEXT PRIMARY KEY, sample TEXT NOT NULL UNIQUE, station TEXT NOT NULL,
        day INTEGER NOT NULL, document TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS sample_result (
        label TEXT PRIMARY KEY REFERENCES sample_label, operator TEXT NOT NULL,
        reading INTEGER NOT NULL, document TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS sample_reveal (
        label TEXT PRIMARY KEY REFERENCES sample_result, assessor TEXT NOT NULL)''',
)


def initialize(db):
    for ddl in SCHEMA:
        db.execute(ddl)


def label(db, label, sample, station, sample_day, document):
    fields = (text(label), text(sample), text(station), day(sample_day), text(document))
    current = db.execute('SELECT * FROM sample_label WHERE label=?', (label,)).fetchone()
    require(current is None or tuple(current) == fields, 'conflicting label receipt')
    if current is None:
        db.execute('INSERT INTO sample_label VALUES (?,?,?,?,?)', fields)
    return {'label': label, 'duplicate': current is not None}


def origin(db, label):
    row = db.execute('SELECT * FROM sample_label WHERE label=?', (text(label),)).fetchone()
    require(row is not None, 'no registered bottle')
    return dict(row)
