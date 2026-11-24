"""Simulate the removable recorder's bounded storage, independently of the archive."""
from .formats import integer, require, text


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS logger_device (
       id TEXT PRIMARY KEY, pages INTEGER NOT NULL CHECK(pages>0), counter INTEGER NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS logger_page (
       device TEXT NOT NULL REFERENCES logger_device, slot INTEGER NOT NULL, sequence INTEGER NOT NULL,
       value INTEGER NOT NULL, PRIMARY KEY(device,slot))''',
    '''CREATE TABLE IF NOT EXISTS logger_card (
       id TEXT PRIMARY KEY, device TEXT NOT NULL REFERENCES logger_device, body TEXT NOT NULL)''',
)


def initialize(db):
    for ddl in SCHEMA:
        db.execute(ddl)


def setup(db, id, pages):
    db.execute('INSERT INTO logger_device VALUES (?,?,0)',(text(id),integer(pages,1,256)))
    return {'device':id,'pages':pages}


def record(db, device, value):
    row = db.execute('SELECT pages,counter FROM logger_device WHERE id=?',(text(device),)).fetchone()
    require(row is not None,'unknown logger device')
    sequence = row['counter']+1
    slot = (sequence-1)%row['pages']
    db.execute('''INSERT INTO logger_page VALUES (?,?,?,?) ON CONFLICT(device,slot)
                  DO UPDATE SET sequence=excluded.sequence,value=excluded.value''',
               (device,slot,sequence,integer(value,-100_000,100_000)))
    db.execute('UPDATE logger_device SET counter=? WHERE id=?',(sequence,device))
    return {'device':device,'sequence':sequence,'slot':slot}


def pages(db, device, first, last):
    return list(db.execute('''SELECT slot,sequence,value FROM logger_page WHERE device=? AND
                              sequence BETWEEN ? AND ? ORDER BY sequence''',(device,first,last)))
