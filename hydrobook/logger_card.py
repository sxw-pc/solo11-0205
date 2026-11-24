"""Copy a complete contiguous recorder interval onto the archive desk card."""
import json

from .formats import encoded, integer, require, text
from .logger_cache import pages


def pull(db, id, device, first, last):
    text(id)
    integer(first,1,10_000_000)
    integer(last,first,10_000_000)
    raw = pages(db,text(device),first,last)
    require(len(raw)==last-first+1, 'sequence was never captured or has been overwritten')
    body = encoded({'first':first,'last':last,'samples':[dict(row) for row in raw]})
    db.execute('INSERT INTO logger_card VALUES (?,?,?)',(id,device,body))
    return read(db,id)


def read(db, id):
    row=db.execute('SELECT device,body FROM logger_card WHERE id=?',(text(id),)).fetchone()
    require(row is not None,'unknown sampler card')
    return {'card':id,'device':row['device'],**json.loads(row['body'])}
