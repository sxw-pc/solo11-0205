"""Keep sealed transport volumes before importing their hydrological payloads.

Receiving contract
------------------
A volume envelope proves *intake* immediately (a transport receipt plus the raw
envelope), but intake never moves the evidence cut by itself.  Records are
staged until the revision group they belong to can be validated as a whole on
one evidence cut and posted atomically (a posting receipt feeding the
projections).

A group declares the parts that carry its complete record set (``parts``), the
older revisions it replaces or withdraws (``replaces``) and the original
records it depends on (``requires``).  A fully self-contained group whose
revision scope is provably independent of any missing volume may post early;
groups spanning volumes wait until every declared part has arrived.  Every
replacement, withdrawal and supplement in the same group takes effect together
or stays held together — a half group can never be booked.

Content conflicts never overwrite booked evidence: the group is recorded as
CONFLICT with the conflicting source and the available next step, and only a
formal later revision can advance the evidence beyond it.
"""

from .formats import FIELDS, encoded, integer, parse, require, text, unique_keys
import json


ENVELOPE_FIELDS = {'desk', 'shipment', 'part', 'total', 'records', 'groups'}


def _ref(value):
    require(isinstance(value, list) and len(value) == 3, 'group reference must be [kind,id,rev]')
    kind, identity, rev = value
    require(kind in FIELDS, 'group reference uses unknown kind')
    text(identity)
    integer(rev, 1, 1_000_000)
    return kind, identity, rev


def _group_declaration(declaration, part, total, row_index):
    require(isinstance(declaration, dict), 'group declaration must be an object')
    require(set(declaration) == {'id', 'parts', 'members', 'replaces', 'requires'},
            'group declaration needs id, parts, members, replaces and requires')
    gid = text(declaration['id'])
    parts = declaration['parts']
    require(isinstance(parts, list) and bool(parts), 'group parts must be a non-empty list')
    parts = [integer(value, 1, total) for value in parts]
    require(len(set(parts)) == len(parts), 'group parts contain duplicates')
    require(part in parts, 'group declared in a volume that is not one of its parts')
    members = [_ref(value) for value in declaration['members']]
    require(bool(members), 'group must contain at least one member')
    require(len(set(members)) == len(members), 'group members contain duplicate references')
    replaces = [_ref(value) for value in declaration['replaces']]
    requires = [_ref(value) for value in declaration['requires']]
    available = set(row_index)
    for ref in members:
        require(ref in available, f'group {gid} member {ref} is not present in its volume records')
    return {'id': gid, 'parts': parts, 'members': members,
            'replaces': replaces, 'requires': requires}


def decode_envelope(raw):
    envelope = json.loads(raw.decode('utf-8'), object_pairs_hook=unique_keys)
    require(isinstance(envelope, dict) and set(envelope) <= ENVELOPE_FIELDS
            and {'desk', 'shipment', 'part', 'total', 'records'} <= set(envelope),
            'invalid volume envelope')
    text(envelope['desk'])
    text(envelope['shipment'])
    integer(envelope['total'], 1, 200)
    integer(envelope['part'], 1, envelope['total'])
    records = encoded(envelope['records']).encode()
    parsed = parse(records)
    row_index = {(row['kind'], row['id'], row['rev']): row for row in parsed}
    require(len(row_index) == len(parsed), 'volume records contain duplicate references')
    declarations = envelope.get('groups')
    if declarations is None:
        groups = [{
            'id': f"part{envelope['part']}:{ref[0]}:{ref[1]}:{ref[2]}",
            'parts': [envelope['part']],
            'members': [ref], 'replaces': [], 'requires': [],
        } for ref in row_index]
    else:
        require(isinstance(declarations, list) and bool(declarations),
                'groups must be a non-empty list when present')
        groups = [_group_declaration(item, envelope['part'], envelope['total'], row_index)
                  for item in declarations]
        require(len({group['id'] for group in groups}) == len(groups),
                'duplicate group id inside one envelope')
        covered = [ref for group in groups for ref in group['members']]
        require(len(covered) == len(set(covered)), 'a record belongs to more than one group')
        require(set(covered) == set(row_index), 'every volume record must belong to a group')
    return envelope, records, parsed, groups


def _load_group(db, desk, shipment, gid):
    row = db.execute('SELECT * FROM volume_group WHERE desk=? AND shipment=? AND gid=?',
                     (desk, shipment, gid)).fetchone()
    return None if row is None else {
        'id': row['gid'], 'parts': json.loads(row['parts']),
        'members': json.loads(row['members']),
        'replaces': json.loads(row['replaces']), 'requires': json.loads(row['requires']),
        'state': row['state'], 'conflict': json.loads(row['conflict']) if row['conflict'] else None,
        'posting_receipt': row['posting_receipt'],
    }


def _projection_body(db, ref):
    kind, identity, rev = ref
    row = db.execute(f'SELECT body, first_receipt FROM {kind} WHERE id=? AND rev=?',
                     (identity, rev)).fetchone()
    return None if row is None else (row['body'], row['first_receipt'])


def _staged_body(db, desk, shipment, ref):
    kind, identity, rev = ref
    row = db.execute("""SELECT body FROM staged_record WHERE desk=? AND shipment=?
        AND kind=? AND identifier=? AND revision=?""", (desk, shipment, kind, identity, rev)).fetchone()
    return None if row is None else row['body']


def _members(db, desk, shipment, gid):
    group = _load_group(db, desk, shipment, gid)

    def position(ref):
        row = db.execute("""SELECT part,ordinal FROM staged_record
            WHERE desk=? AND shipment=? AND kind=? AND identifier=? AND revision=?""",
                         (desk, shipment, *ref)).fetchone()
        return row[:2] if row is not None else (10**9, 10**9)

    # Keep deterministic, receipt-stable ordering across the group's parts.
    return sorted((tuple(item) for item in group['members']), key=position)


def _register_group(db, desk, shipment, group, part):
    existing = _load_group(db, desk, shipment, group['id'])
    if existing is None:
        db.execute("""INSERT INTO volume_group(desk,shipment,gid,parts,members,replaces,requires,state)
            VALUES(?,?,?,?,?,?,?, 'HELD')""",
                   (desk, shipment, group['id'], json.dumps(group['parts']),
                    json.dumps(group['members']), json.dumps(group['replaces']),
                    json.dumps(group['requires'])))
        return
    # The same group may be declared by several volumes; the stable scope
    # (parts, replaced and required revisions) must agree, while each volume
    # contributes its own member records to the union.
    require(existing['state'] == 'HELD', f"group {group['id']} is no longer open")
    same = (existing['parts'] == group['parts']
            and [tuple(item) for item in existing['replaces']] == group['replaces']
            and [tuple(item) for item in existing['requires']] == group['requires'])
    require(same, f"group {group['id']} declarations differ across volumes")
    members = {tuple(item) for item in existing['members']} | set(group['members'])
    db.execute('UPDATE volume_group SET members=? WHERE desk=? AND shipment=? AND gid=?',
               (json.dumps(sorted(members)), desk, shipment, group['id']))


def _conflict(reason, sources, next_step):
    return {'reason': reason, 'sources': sorted(sources), 'next_step': next_step}


def _held_groups(db):
    return [(row['desk'], row['shipment'], row['gid']) for row in db.execute(
        "SELECT desk, shipment, gid FROM volume_group WHERE state='HELD' ORDER BY desk, shipment, gid")]


def _received_parts(db):
    return {(row['desk'], row['shipment']):
            {part['part'] for part in db.execute(
                'SELECT DISTINCT part FROM transport_volume WHERE desk=? AND shipment=?',
                (row['desk'], row['shipment']))}
            for row in db.execute('SELECT DISTINCT desk, shipment FROM transport_volume')}


def _staged_groups(db, target):
    """Held groups that actually carry a reference as one of their members."""
    kind, identity, rev = target
    return {(row['desk'], row['shipment'], row['gid']) for row in db.execute("""
        SELECT DISTINCT g.desk, g.shipment, g.gid FROM volume_group AS g
        WHERE g.state='HELD' AND EXISTS (SELECT 1 FROM json_each(g.members) AS m
        WHERE json_extract(m.value, '$[0]')=? AND json_extract(m.value, '$[1]')=?
        AND json_extract(m.value, '$[2]')=?)""", (kind, identity, rev))}


def _all_staged_bodies(db, target):
    """Distinct staged bodies for a reference across every shipment."""
    kind, identity, rev = target
    rows = db.execute("""SELECT DISTINCT body FROM staged_record
        WHERE kind=? AND identifier=? AND revision=?""", (kind, identity, rev)).fetchall()
    return {row['body'] for row in rows}


def _components(keys, links):
    """Connected components of the group dependency graph."""
    adjacency = {key: set() for key in keys}
    for left, right in links:
        adjacency.setdefault(left, set()).add(right)
        adjacency.setdefault(right, set()).add(left)
    seen, components = set(), []
    for start in keys:
        if start in seen:
            continue
        stack, component = [start], set()
        while stack:
            current = stack.pop()
            if current in seen:
                continue
            seen.add(current)
            component.add(current)
            stack.extend(adjacency[current] - seen)
        components.append(sorted(component))
    return components


def _evaluate(db):
    """Validate every held group together on one evidence cut.

    Groups that reference the same still-unbooked revision form one connected
    component.  Such a component is validated jointly and posts atomically: no
    group may book against another group's material before that material has
    itself been posted.  Components independent of each other post separately,
    which lets a provably self-contained group enter the archive while a
    broken shipment elsewhere stays held.  All decisions are computed against
    the same pre-evaluation snapshot, so postings within this call can never
    change another component's verdict mid-way.
    """
    keys = _held_groups(db)
    if not keys:
        return
    loaded = {key: _load_group(db, *key) for key in keys}
    received = _received_parts(db)
    links, waiting, conflicts = [], {}, {}

    def mark_conflict(key, reason, sources, next_step):
        conflicts.setdefault(key, _conflict(reason, sources, next_step))

    for key in keys:
        group = loaded[key]
        missing = sorted(set(group['parts']) - received.get(key[:2], set()))
        if missing:
            waiting[key] = _conflict('awaiting_parts', [json.dumps(missing)],
                                     'wait for the missing shipment volumes')
        members = _members(db, *key)
        for member in members:
            staged = _staged_body(db, key[0], key[1], member)
            if staged is None:
                mark_conflict(key, 'incomplete_group', [json.dumps(member)],
                              'resend the volume carrying the missing member record')
            projected = _projection_body(db, member)
            if projected is not None and staged is not None and projected[0] != staged:
                mark_conflict(key, 'late_version_conflict',
                              [f'{member[0]}/{member[1]}/rev{member[2]} '
                               f'(booked receipt {projected[1]})'],
                              'keep the booked revision and advance it with a higher-rev formal revision')
            carriers = _staged_groups(db, member) - {key}
            bodies = _all_staged_bodies(db, member)
            if staged is not None:
                bodies.add(staged)
            if len(bodies) > 1:
                mark_conflict(key, 'divergent_revision',
                              [f'{member[0]}/{member[1]}/rev{member[2]}'],
                              'issue a new formal revision for the reference; '
                              'the late volume cannot override booked evidence')
            links.extend((key, carrier) for carrier in carriers)
        member_refs = {tuple(item) for item in group['members']}
        for reference, role in (
                *[(tuple(item), 'replaced') for item in group['replaces']],
                *[(tuple(item), 'required') for item in group['requires']]):
            if reference in member_refs:
                # A self-reference is satisfied by the group's own members.
                continue
            if _projection_body(db, reference) is not None:
                continue
            carriers = _staged_groups(db, reference)
            if not carriers:
                waiting[key] = _conflict(
                    f'unresolved_{role}_revision',
                    [f'{reference[0]}/{reference[1]}/rev{reference[2]}'],
                    'wait for the volume carrying this original revision')
                continue
            if len(_all_staged_bodies(db, reference)) > 1:
                mark_conflict(key, 'divergent_revision',
                              [f'{reference[0]}/{reference[1]}/rev{reference[2]}'],
                              'issue a new formal revision for the reference; '
                              'the late volume cannot override booked evidence')
            links.extend((key, carrier) for carrier in carriers)

    for component in _components(keys, links):
        component_conflicts = [conflicts[key] for key in component if key in conflicts]
        if component_conflicts:
            reason = ('divergent_revision'
                      if any(item['reason'] == 'divergent_revision' for item in component_conflicts)
                      else component_conflicts[0]['reason'])
            merged = _conflict(reason,
                               [source for item in component_conflicts for source in item['sources']],
                               component_conflicts[0]['next_step'])
            for key in component:
                db.execute('UPDATE volume_group SET state=?, conflict=? WHERE desk=? AND shipment=? AND gid=?',
                           ('CONFLICT', json.dumps(merged), *key))
            continue
        if any(key in waiting for key in component):
            continue
        # Jointly valid on this cut: post each group, still atomically per group.
        for key in sorted(component):
            members = _members(db, *key)
            raw = json.dumps(
                [json.loads(_staged_body(db, key[0], key[1], member)) for member in members],
                ensure_ascii=True, separators=(',', ':')).encode()
            from .store import log_posting
            outcome = log_posting(db, raw)
            db.execute('UPDATE volume_group SET state=?, posting_receipt=? WHERE desk=? AND shipment=? AND gid=?',
                       ('ACTIVE', outcome['receipt'], *key))


def accept(db, raw):
    from .store import digest, log_transport

    envelope, records, parsed, groups = decode_envelope(raw)
    desk, shipment, part, total = (envelope[key] for key in ('desk', 'shipment', 'part', 'total'))
    key = (desk, shipment, part)
    existing = db.execute('SELECT * FROM transport_volume WHERE desk=? AND shipment=? AND part=?', key).fetchone()
    if existing:
        require(existing['raw'] == raw, 'volume identity already has another payload')
        return status(db, desk, shipment)
    plan = db.execute('SELECT total FROM transport_volume WHERE desk=? AND shipment=? LIMIT 1', key[:2]).fetchone()
    require(plan is None or plan['total'] == total, 'shipment total differs across volumes')
    outcome = log_transport(db, records)
    db.execute('INSERT INTO transport_volume VALUES (?,?,?,?,?,?,?)',
               (*key, total, digest(raw), raw, outcome['receipt']))
    for ordinal, row in enumerate(parsed, 1):
        db.execute("""INSERT INTO staged_record(desk,shipment,part,ordinal,kind,identifier,revision,body)
            VALUES(?,?,?,?,?,?,?,?)""",
                   (desk, shipment, part, ordinal, row['kind'], row['id'], row['rev'], encoded(row)))
    for group in groups:
        _register_group(db, desk, shipment, group, part)
    # A new volume can settle revisions that other shipments were waiting on,
    # so every held group is re-evaluated together on the same evidence cut.
    _evaluate(db)
    return status(db, desk, shipment)


def status(db, desk, shipment):
    rows = list(db.execute('SELECT part,total,receipt FROM transport_volume WHERE desk=? AND shipment=? ORDER BY part',
                           (text(desk), text(shipment))))
    require(bool(rows), 'unknown desk shipment')
    total = rows[0]['total']
    present = {row['part']: row['receipt'] for row in rows}
    missing = sorted(set(range(1, total + 1)) - present.keys())
    group_rows = list(db.execute(
        'SELECT * FROM volume_group WHERE desk=? AND shipment=? ORDER BY gid', (desk, shipment)))
    groups = [{
        'id': row['gid'],
        'state': row['state'],
        'parts': json.loads(row['parts']),
        'members': json.loads(row['members']),
        'missing_parts': sorted(set(json.loads(row['parts'])) - present.keys()),
        'replaces': json.loads(row['replaces']),
        'requires': json.loads(row['requires']),
        'posting_receipt': row['posting_receipt'],
        'conflict': json.loads(row['conflict']) if row['conflict'] else None,
    } for row in group_rows]
    if any(group['state'] == 'CONFLICT' for group in groups):
        evidence = 'CONFLICT'
    elif all(group['state'] == 'ACTIVE' for group in groups):
        evidence = 'POSTED'
    elif any(group['state'] == 'ACTIVE' for group in groups):
        evidence = 'PARTIALLY_POSTED'
    else:
        evidence = 'HELD'
    return {'desk': desk, 'shipment': shipment, 'total': total, 'receipts': present,
            'missing': missing, 'state': 'PARTIAL' if missing else 'COMPLETE',
            'evidence': evidence, 'groups': groups}
