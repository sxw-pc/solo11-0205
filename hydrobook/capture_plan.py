"""A scheduled list of capture ticks for each deployed field head."""
from .formats import integer, require, text


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS sampler_plan (
        id TEXT PRIMARY KEY, head TEXT NOT NULL, channel TEXT NOT NULL, start INTEGER NOT NULL,
        stop INTEGER NOT NULL, period INTEGER NOT NULL CHECK(period>0))''',
    '''CREATE TABLE IF NOT EXISTS sampler_value (
        id TEXT PRIMARY KEY, plan TEXT NOT NULL REFERENCES sampler_plan, tick INTEGER NOT NULL,
        value INTEGER NOT NULL, document TEXT NOT NULL, UNIQUE(plan,tick))''',
)


def initialize(db):
    for ddl in SCHEMA:
        db.execute(ddl)


def program(db, id, head, channel, start, stop, period):
    fields = (text(id),text(head),text(channel),integer(start,0,100_000),integer(stop,0,100_000),
              integer(period,1,100_000))
    require(start < stop and stop-start <= 1024, 'invalid plan window')
    db.execute('INSERT INTO sampler_plan VALUES (?,?,?,?,?,?)', fields)
    return {'plan': id, 'ticks': list(ticks(db, id))}


def ticks(db, plan):
    row = db.execute('SELECT start,stop,period FROM sampler_plan WHERE id=?', (text(plan),)).fetchone()
    require(row is not None, 'no capture plan')
    return range(*row)
