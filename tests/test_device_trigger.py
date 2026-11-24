from contextlib import closing
import sqlite3
from unittest import TestCase

from hydrobook import capture_plan,device_trigger,sensor_log
from hydrobook.formats import EvidenceError
from hydrobook.store import connect,initialize


class TriggerTests(TestCase):
    def setUp(self):
        self.db=self.enterContext(closing(connect(':memory:')))
        initialize(self.db)
        capture_plan.initialize(self.db)
        device_trigger.initialize(self.db)
        capture_plan.program(self.db,'P','A','level',4,8,2)

    def test_head_cannot_take_second_trigger_until_actual_ack(self):
        device_trigger.issue(self.db,'C','P',4)
        with self.assertRaises(sqlite3.IntegrityError):
            device_trigger.issue(self.db,'C2','P',6)
        self.assertEqual(sensor_log.progress(self.db,'P')['missing'],[4,6])
        device_trigger.acknowledge(self.db,'C','R','A',4,20,'w')
        self.assertEqual(sensor_log.progress(self.db,'P')['missing'],[6])
        self.assertEqual(device_trigger.issue(self.db,'C2','P',6)['tick'],6)

    def test_different_physical_head_cannot_ack(self):
        device_trigger.issue(self.db,'C','P',4)
        with self.assertRaises(EvidenceError):
            device_trigger.acknowledge(self.db,'C','R','B',4,20,'w')
