import json
import unittest
from contextlib import closing

from hydrobook import channel
from hydrobook.formats import EvidenceError
from hydrobook.store import Snapshot, connect, head, ingest, initialize, transaction


SHIPMENT = 'shift-5'


def envelope(part, records, groups=None, desk='field', total=2, shipment=SHIPMENT):
    payload = dict(desk=desk, shipment=shipment, part=part, total=total, records=records)
    if groups is not None:
        payload['groups'] = groups
    return json.dumps(payload).encode()


def group(gid, members, parts, replaces=None, requires=None):
    return dict(id=gid, parts=parts, members=[list(ref) for ref in members],
                replaces=[list(ref) for ref in (replaces or [])],
                requires=[list(ref) for ref in (requires or [])])


def section(identity, rev=1, state='active', station='NORTH', reach='UPPER',
            datum='REF', profile='P1', start=1, end=3):
    return dict(kind='section', id=identity, rev=rev, state=state, station=station,
                reach=reach, datum=datum, profile=profile, start=start, end=end, zero=0)


def survey(identity, reviewer='desk'):
    return dict(kind='survey', id=identity, rev=1, state='active', start=5, end=7,
                reviewer=reviewer, result='certified')


def reading(identity, rev=1, state='active', strip='NS1', day=1, mark=210):
    return dict(kind='reading', id=identity, rev=rev, state=state, strip=strip,
                day=day, mark=mark, source='witness')


def ref(row):
    return (row['kind'], row['id'], row['rev'])


class TransportVolumes(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        initialize(self.db)
        # Booked evidence already in the archive before the shipment arrives.
        with transaction(self.db, writing=True):
            self.baseline = ingest(self.db, json.dumps([
                section('SEC-A'), section('SEC-B', profile='P2'), section('SEC-C', profile='P3')]).encode())
        self.baseline_cut = head(self.db)

    def accept(self, raw):
        with transaction(self.db, writing=True):
            return channel.accept(self.db, raw)

    def group(self, status, gid):
        return next(item for item in status['groups'] if item['id'] == gid)

    def test_missing_replacement_volume_holds_revision_while_independent_certificate_posts(self):
        # Volume 2 withdraws the old section; the replacement section is
        # promised in volume 1, which has not arrived yet.  The same volume
        # carries a measurement certificate whose revision scope is independent.
        withdrawn = section('SEC-A', rev=2, state='void')
        replacement = section('SEC-A', rev=3, datum='NEW', profile='P9', start=3, end=6)
        certificate = survey('SV-CERT', reviewer='basin')
        second = envelope(2, [withdrawn, certificate], [
            group('relocation', [ref(withdrawn)], parts=[1, 2], replaces=[ref(section('SEC-A'))]),
            group('certificate', [ref(certificate)], parts=[2]),
        ])
        result = self.accept(second)

        # Intake is immediately provable...
        self.assertEqual(result['state'], 'PARTIAL')
        self.assertEqual(result['missing'], [1])
        self.assertIn(2, result['receipts'])
        # ...while the half relocation revision cannot reach the evidence cut.
        relocation = self.group(result, 'relocation')
        self.assertEqual(relocation['state'], 'HELD')
        self.assertEqual(relocation['missing_parts'], [1])
        self.assertIsNone(relocation['posting_receipt'])
        active_sections = Snapshot(self.db).active('section')
        self.assertIn('SEC-A', active_sections)
        self.assertEqual(active_sections['SEC-A']['receipt'], self.baseline['receipt'])
        # The self-contained, provably independent certificate posts at once.
        certificate_group = self.group(result, 'certificate')
        self.assertEqual(certificate_group['state'], 'ACTIVE')
        self.assertIsNotNone(certificate_group['posting_receipt'])
        self.assertEqual(list(Snapshot(self.db).active('survey')), ['SV-CERT'])
        self.assertEqual(result['evidence'], 'PARTIALLY_POSTED')

        # Re-sending the identical envelope changes nothing: no duplicate effect.
        cut_before_resend = head(self.db)
        again = self.accept(second)
        self.assertEqual(self.group(again, 'relocation')['state'], 'HELD')
        self.assertEqual(self.group(again, 'certificate')['posting_receipt'],
                         certificate_group['posting_receipt'])
        self.assertEqual(head(self.db), cut_before_resend)

        # Volume 1 arrives with the promised replacement: the whole group
        # (withdrawal plus replacement) takes effect on one evidence cut.
        first = envelope(1, [replacement], [
            group('relocation', [ref(replacement)], parts=[1, 2],
                  replaces=[ref(section('SEC-A'))]),
        ])
        completed = self.accept(first)
        self.assertEqual(completed['state'], 'COMPLETE')
        relocation = self.group(completed, 'relocation')
        self.assertEqual(relocation['state'], 'ACTIVE')
        self.assertIsNotNone(relocation['posting_receipt'])
        sections = Snapshot(self.db).active('section')
        # The withdrawal and replacement posted together: the active SEC-A is
        # the new segment, and the old rev1 is shadowed, never reactivated.
        self.assertEqual(sections['SEC-A']['rev'], 3)
        self.assertEqual(sections['SEC-A']['datum'], 'NEW')
        old = self.db.execute('SELECT body,first_receipt FROM section WHERE id=? AND rev=?',
                              ('SEC-A', 1)).fetchone()
        self.assertEqual(old['first_receipt'], self.baseline['receipt'])
        self.assertEqual(Snapshot(self.db, cut=self.baseline_cut).active('section')['SEC-A']['rev'], 1)
        booked = self.db.execute('SELECT * FROM section WHERE id=? AND rev=?', ('SEC-A', 3)).fetchone()
        self.assertEqual(json.loads(booked['body'])['datum'], 'NEW')
        self.assertEqual(booked['first_receipt'], relocation['posting_receipt'])
        # The cut only ever moved forward; transport receipts alone never did.
        self.assertGreater(head(self.db), self.baseline_cut)

    def test_mutually_supplementing_groups_with_divergent_originals_cannot_half_post(self):
        # Two groups complete each other across the same box, and both assume
        # the same revision R-1 has a different body.  Each volume alone looks
        # fine; together they fail on a single evidence cut.
        left = reading('R-1', rev=2, mark=220)
        right = reading('R-1', rev=2, mark=280)
        own_left = reading('LEFT', rev=1, mark=100)
        own_right = reading('RIGHT', rev=1, mark=300)

        first = envelope(1, [left, own_left], [
            group('pair-left', [ref(left), ref(own_left)], parts=[1, 2],
                  requires=[ref(right)]),
        ])
        after_first = self.accept(first)
        self.assertEqual(self.group(after_first, 'pair-left')['state'], 'HELD')
        self.assertEqual(Snapshot(self.db).active('reading'), {})

        second = envelope(2, [right, own_right], [
            group('pair-right', [ref(right), ref(own_right)], parts=[1, 2],
                  requires=[ref(left)]),
        ])
        after_second = self.accept(second)
        self.assertEqual(after_second['state'], 'COMPLETE')
        for gid in ('pair-left', 'pair-right'):
            entry = self.group(after_second, gid)
            self.assertEqual(entry['state'], 'CONFLICT')
            self.assertIsNone(entry['posting_receipt'])
            self.assertEqual(entry['conflict']['reason'], 'divergent_revision')
            self.assertIn('R-1', ' '.join(entry['conflict']['sources']))
            self.assertTrue(entry['conflict']['next_step'])
        self.assertEqual(after_second['evidence'], 'CONFLICT')
        # No half group ever reached the projections; baseline evidence stands.
        self.assertEqual(Snapshot(self.db).active('reading'), {})
        self.assertEqual(
            self.db.execute("SELECT count(*) FROM reading").fetchone()[0], 0)
        # Replaying either volume keeps the conflict on record without posting.
        again = self.accept(second)
        self.assertEqual(self.group(again, 'pair-right')['state'], 'CONFLICT')
        self.assertEqual(Snapshot(self.db).active('reading'), {})

    def test_late_volume_conflicting_with_booked_revision_is_recorded_not_overriding(self):
        booked = reading('R-9', rev=1, mark=210)
        with transaction(self.db, writing=True):
            ingest(self.db, json.dumps([booked]).encode())
        late = reading('R-9', rev=1, mark=250)
        raw = envelope(2, [late], [
            group('late-r9', [ref(late)], parts=[2],
                  requires=[ref(section('SEC-B'))]),
        ])
        result = self.accept(raw)
        entry = self.group(result, 'late-r9')
        self.assertEqual(entry['state'], 'CONFLICT')
        self.assertEqual(entry['conflict']['reason'], 'late_version_conflict')
        self.assertIn('R-9', ' '.join(entry['conflict']['sources']))
        self.assertTrue(entry['conflict']['next_step'])
        # Booked content survives; a formal higher revision can still advance it.
        self.assertEqual(Snapshot(self.db).active('reading')['R-9']['mark'], 210)
        revision = reading('R-9', rev=2, mark=250)
        fixed = self.accept(envelope(1, [revision], [
            group('formal-r9', [ref(revision)], parts=[1], replaces=[ref(booked)]),
        ]))
        self.assertEqual(self.group(fixed, 'formal-r9')['state'], 'ACTIVE')
        self.assertEqual(Snapshot(self.db).active('reading')['R-9']['mark'], 250)

    def test_volumes_without_group_declarations_keep_immediate_single_record_contract(self):
        second = envelope(2, [survey('S2')], groups=None)
        result = self.accept(second)
        self.assertEqual(result['missing'], [1])
        self.assertEqual(list(Snapshot(self.db).active('survey')), ['S2'])
        self.assertEqual(self.group(result, result['groups'][0]['id'])['state'], 'ACTIVE')
        self.assertEqual(self.accept(envelope(1, [survey('S1')]))['state'], 'COMPLETE')
        self.assertEqual(self.accept(second)['receipts'], {1: 3, 2: 2})

    def test_collision_refuses_payload_and_leaves_original(self):
        self.accept(envelope(1, [survey('S1')]))
        with self.assertRaisesRegex(EvidenceError, 'another payload'):
            self.accept(envelope(1, [survey('S9')]))
        with self.assertRaisesRegex(EvidenceError, 'total differs'):
            self.accept(envelope(2, [survey('S9')], total=3))
        self.assertEqual(list(Snapshot(self.db).active('survey')), ['S1'])

    def test_identical_part_numbers_at_two_desks_are_distinct(self):
        self.accept(envelope(1, [survey('S1')]))
        self.accept(envelope(1, [survey('S2')], desk='backup'))
        self.assertEqual(channel.status(self.db, 'field', SHIPMENT)['receipts'], {1: 2})
        self.assertEqual(channel.status(self.db, 'backup', SHIPMENT)['receipts'], {1: 3})

    def test_repeated_revision_in_same_group_is_idempotent_and_divergent_bodies_conflict(self):
        same = reading('DUP', rev=1, mark=220)
        self.accept(envelope(1, [same], [group('dup-group', [ref(same)], parts=[1, 2])]))
        second_same = envelope(2, [same], [group('dup-group', [ref(same)], parts=[1, 2])])
        cut_before_resend = head(self.db)
        self.assertEqual(self.group(self.accept(second_same), 'dup-group')['state'], 'ACTIVE')
        self.assertEqual(head(self.db), cut_before_resend)
        self.assertEqual(Snapshot(self.db).active('reading')['DUP']['mark'], 220)

        # A separate shipment restaging the same reference with another body
        # must be held as a conflict, never silently replacing the booking.
        other = reading('DUP', rev=1, mark=290)
        divergent = self.accept(envelope(1, [other], [
            group('dup-again', [ref(other)], parts=[1])], shipment='shift-6'))
        entry = self.group(divergent, 'dup-again')
        self.assertEqual(entry['state'], 'CONFLICT')
        self.assertEqual(entry['conflict']['reason'], 'late_version_conflict')
        self.assertEqual(Snapshot(self.db).active('reading')['DUP']['mark'], 220)

    def test_dependent_group_cannot_book_against_unposted_divergent_originals(self):
        # BASE rev1 exists in two separate shipments with divergent bodies.
        # A group requiring BASE has its own part in hand but cannot book
        # against a revision whose body is not settled on the evidence cut,
        # and neither BASE group can win by posting first.
        base_a = reading('BASE', rev=1, mark=110)
        base_b = reading('BASE', rev=1, mark=190)
        own = survey('SV-DEP')
        first = envelope(1, [base_a, own], [
            group('base-a', [ref(base_a)], parts=[1, 2]),
            group('depends-on-base', [ref(own)], parts=[1, 2], requires=[ref(base_a)]),
        ])
        after_first = self.accept(first)
        self.assertEqual(self.group(after_first, 'depends-on-base')['state'], 'HELD')
        self.assertNotIn('SV-DEP', Snapshot(self.db).active('survey'))
        # A complete second shipment carries the divergent BASE body.  Because
        # base-a is still held with another body, base-b cannot book over it.
        divergent = self.accept(envelope(1, [base_b], [
            group('base-b', [ref(base_b)], parts=[1])], shipment='shift-7'))
        base_b_group = self.group(divergent, 'base-b')
        self.assertEqual(base_b_group['state'], 'CONFLICT')
        self.assertEqual(base_b_group['conflict']['reason'], 'divergent_revision')
        self.assertIn('BASE', ' '.join(base_b_group['conflict']['sources']))
        # The dependent group is re-evaluated against the same cut and fails too.
        status = channel.status(self.db, 'field', SHIPMENT)
        dependent = self.group(status, 'depends-on-base')
        self.assertEqual(dependent['state'], 'CONFLICT')
        self.assertEqual(dependent['conflict']['reason'], 'divergent_revision')
        # No half group and no competing revision ever reached the projections.
        self.assertNotIn('SV-DEP', Snapshot(self.db).active('survey'))
        self.assertNotIn('BASE', Snapshot(self.db).active('reading'))

    def test_envelope_rejecting_unknown_or_inconsistent_group_declarations(self):
        bad_group = dict(id='g', parts=[2], members=[['reading', 'X', 1]], replaces=[], requires=[])
        with self.assertRaisesRegex(EvidenceError, 'not one of its parts'):
            self.accept(envelope(1, [reading('X')], [bad_group]))
        wandering = reading('W')
        with self.assertRaisesRegex(EvidenceError, 'must belong to a group'):
            self.accept(envelope(2, [wandering, survey('S8')], [
                group('only-one', [ref(survey('S8'))], parts=[2])]))
        # Group declarations spanning volumes must stay consistent.
        head_part = envelope(1, [reading('H')], [group('span', [ref(reading('H'))], parts=[1, 2])])
        self.accept(head_part)
        changed = dict(group('span', [ref(reading('H', mark=299))], parts=[1, 2],
                             replaces=[ref(section('SEC-B'))]))
        with self.assertRaisesRegex(EvidenceError, 'declarations differ'):
            self.accept(envelope(2, [reading('H', mark=299)], [changed]))


if __name__ == '__main__':
    unittest.main()
