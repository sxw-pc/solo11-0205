import json
import unittest
from contextlib import closing

from hydrobook import channel
from hydrobook.formats import EvidenceError
from hydrobook.store import Snapshot, connect, initialize, transaction


def volume(part, record, desk='field', total=2):
    envelope = dict(desk=desk, shipment='shift-5', part=part, total=total, records=[record])
    return json.dumps(envelope).encode()


def survey(id, reviewer):
    return dict(kind='survey', id=id, rev=1, state='active', start=5, end=7,
                reviewer=reviewer, result='certified')


class TransportVolumes(unittest.TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        initialize(self.db)

    def accept(self, raw):
        with transaction(self.db, writing=True):
            return channel.accept(self.db, raw)

    def test_each_received_volume_is_visible_and_keeps_its_envelope(self):
        second = volume(2, survey('S2', 'desk'))
        self.assertEqual(self.accept(second)['missing'], [1])
        self.assertEqual(list(Snapshot(self.db).active('survey')), ['S2'])
        self.assertEqual(self.accept(volume(1, survey('S1', 'desk')))['state'], 'COMPLETE')
        self.assertEqual(self.db.execute('SELECT raw FROM transport_volume WHERE part=2').fetchone()[0], second)
        self.assertEqual(self.accept(second)['receipts'], {1: 2, 2: 1})

    def test_collision_refuses_payload_and_leaves_original(self):
        self.accept(volume(1, survey('S1', 'desk')))
        with self.assertRaisesRegex(EvidenceError, 'another payload'):
            self.accept(volume(1, survey('S9', 'desk')))
        with self.assertRaisesRegex(EvidenceError, 'total differs'):
            self.accept(volume(2, survey('S9', 'desk'), total=3))
        self.assertEqual(list(Snapshot(self.db).active('survey')), ['S1'])

    def test_identical_part_numbers_at_two_desks_are_distinct(self):
        self.accept(volume(1, survey('S1', 'desk')))
        self.accept(volume(1, survey('S2', 'desk'), 'backup'))
        self.assertEqual(channel.status(self.db, 'field', 'shift-5')['receipts'], {1: 1})
        self.assertEqual(channel.status(self.db, 'backup', 'shift-5')['receipts'], {1: 2})
