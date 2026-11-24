from contextlib import closing
from unittest import TestCase

from hydrobook import capture_plan, sensor_log
from hydrobook.formats import EvidenceError
from hydrobook.store import connect, initialize


class SamplerTests(TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        initialize(self.db)
        capture_plan.initialize(self.db)
        capture_plan.program(self.db, 'P', 'head', 'water-level', 4, 10, 2)

    def test_raw_tick_is_checked_against_its_command(self):
        with self.assertRaises(EvidenceError):
            sensor_log.capture(self.db,'S','P',5,20,'old')
        self.assertEqual(sensor_log.progress(self.db,'P')['missing'],[4,6,8])
        self.assertFalse(sensor_log.capture(self.db,'S','P',6,20,'new')['duplicate'])
        self.assertEqual(sensor_log.progress(self.db,'P')['missing'],[4,8])

    def test_transmission_is_idempotent(self):
        self.assertFalse(sensor_log.capture(self.db,'S','P',6,20,'new')['duplicate'])
        self.assertTrue(sensor_log.capture(self.db,'S','P',6,20,'new')['duplicate'])
