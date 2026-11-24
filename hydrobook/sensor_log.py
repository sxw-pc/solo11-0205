"""Validate a capture against its commanded tick and track outstanding triggers."""
from .capture_plan import ticks
from .formats import integer, require, text


def capture(db, id, plan, tick, value, document):
    fields = (text(id),text(plan),integer(tick,0,100_000),integer(value,-100_000,100_000),text(document))
    require(tick in ticks(db, plan), 'tick is not a part of this plan')
    prior = db.execute('SELECT * FROM sampler_value WHERE id=?', (id,)).fetchone()
    require(prior is None or tuple(prior)==fields, 'conflicting sensor receipt')
    if prior is None:
        db.execute('INSERT INTO sampler_value VALUES (?,?,?,?,?)', fields)
    return {'receipt': id, 'duplicate': prior is not None}


def progress(db, plan):
    rows = list(db.execute('SELECT tick,id FROM sampler_value WHERE plan=? ORDER BY tick',(plan,)))
    seen = {r['tick'] for r in rows}
    return {'plan': plan, 'missing': [t for t in ticks(db,plan) if t not in seen],
            'proof': [r['id'] for r in rows]}
