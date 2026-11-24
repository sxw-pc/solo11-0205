"""Keep sealed transport volumes before importing their hydrological payloads."""

from .formats import encoded, integer, parse, require, text, unique_keys
import json


def decode_envelope(raw):
    envelope = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    names = {'desk', 'shipment', 'part', 'total', 'records'}
    require(isinstance(envelope, dict) and set(envelope) == names, 'invalid volume envelope')
    text(envelope['desk'])
    text(envelope['shipment'])
    integer(envelope['total'], 1, 200)
    integer(envelope['part'], 1, envelope['total'])
    records = encoded(envelope['records']).encode()
    parse(records)
    return envelope, records


def accept(db, raw):
    from .store import digest, ingest

    envelope, records = decode_envelope(raw)
    desk, shipment, part, total = (envelope[key] for key in ('desk', 'shipment', 'part', 'total'))
    key = (desk, shipment, part)
    existing = db.execute('SELECT * FROM transport_volume WHERE desk=? AND shipment=? AND part=?', key).fetchone()
    if existing:
        require(existing['raw'] == raw, 'volume identity already has another payload')
        return status(db, desk, shipment)
    plan = db.execute('SELECT total FROM transport_volume WHERE desk=? AND shipment=? LIMIT 1', key[:2]).fetchone()
    require(plan is None or plan['total'] == total, 'shipment total differs across volumes')
    outcome = ingest(db, records)
    db.execute('INSERT INTO transport_volume VALUES (?,?,?,?,?,?,?)', (*key, total, digest(raw), raw, outcome['receipt']))
    return status(db, desk, shipment)


def status(db, desk, shipment):
    records = list(db.execute('SELECT part,total,receipt FROM transport_volume WHERE desk=? AND shipment=? ORDER BY part',
                              (text(desk), text(shipment))))
    require(bool(records), 'unknown desk shipment')
    total = records[0]['total']
    present = {row['part']: row['receipt'] for row in records}
    missing = sorted(set(range(1, total + 1)) - present.keys())
    return {'desk': desk, 'shipment': shipment, 'total': total, 'receipts': present,
            'missing': missing, 'state': 'PARTIAL' if missing else 'COMPLETE'}
