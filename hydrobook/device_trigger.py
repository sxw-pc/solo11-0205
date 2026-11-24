"""Issue one physical trigger and wait for the assigned head's signed field note."""
from .capture_plan import ticks
from .formats import integer, require, text
from .sensor_log import capture


SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS sampler_trigger (
       id TEXT PRIMARY KEY, plan TEXT NOT NULL REFERENCES sampler_plan, head TEXT NOT NULL,
       tick INTEGER NOT NULL, acknowledged TEXT REFERENCES sampler_value, UNIQUE(plan,tick))''',
    '''CREATE UNIQUE INDEX IF NOT EXISTS one_active_trigger ON sampler_trigger(head) WHERE acknowledged IS NULL''',
)


def initialize(db):
    for ddl in SCHEMA:
        db.execute(ddl)


def issue(db, id, plan, tick):
    text(id)
    require(integer(tick,0,100_000) in ticks(db,plan), 'trigger outside plan')
    head=db.execute('SELECT head FROM sampler_plan WHERE id=?',(plan,)).fetchone()['head']
    db.execute('INSERT INTO sampler_trigger VALUES (?,?,?,?,NULL)',(id,plan,head,tick))
    return {'trigger':id,'head':head,'tick':tick}


def acknowledge(db, trigger, receipt, head, tick, value, document):
    command=db.execute('SELECT * FROM sampler_trigger WHERE id=?',(text(trigger),)).fetchone()
    require(command is not None and (command['head'],command['tick'])==(head,tick),
            'trigger does not match the physical head and planned tick')
    require(command['acknowledged'] in (None,receipt),'conflicting trigger receipt')
    accepted=capture(db,receipt,command['plan'],tick,value,document)
    db.execute('UPDATE sampler_trigger SET acknowledged=? WHERE id=?',(receipt,trigger))
    return accepted
