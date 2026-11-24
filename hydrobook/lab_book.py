"""Lock an independent bench reading and then open its field label."""
from .formats import integer, require, text
from .sample_labels import origin


def record(db, label, operator, reading, document):
    origin(db, label)
    fields = (text(label), text(operator), integer(reading, 0, 10_000_000), text(document))
    current = db.execute('SELECT * FROM sample_result WHERE label=?', (label,)).fetchone()
    require(current is None or tuple(current) == fields, 'conflicting bench receipt')
    if current is None:
        db.execute('INSERT INTO sample_result VALUES (?,?,?,?)', fields)
    return {'label': label, 'locked': True, 'duplicate': current is not None}


def reveal(db, label, assessor):
    text(assessor)
    row = db.execute('SELECT * FROM sample_result WHERE label=?', (text(label),)).fetchone()
    require(row is not None, 'record the bench result first')
    require(row['operator'] != assessor, 'independent assessor required')
    current = db.execute('SELECT assessor FROM sample_reveal WHERE label=?', (label,)).fetchone()
    require(current is None or current['assessor'] == assessor, 'conflicting assessor receipt')
    if current is None:
        db.execute('INSERT INTO sample_reveal VALUES (?,?)', (label, assessor))
    return {'origin': origin(db, label), 'result': dict(row), 'assessor': assessor}
