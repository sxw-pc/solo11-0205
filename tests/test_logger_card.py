from contextlib import closing
from unittest import TestCase

from hydrobook import logger_cache, logger_card
from hydrobook.formats import EvidenceError
from hydrobook.store import connect, initialize


class LoggerTests(TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        initialize(self.db)
        logger_cache.initialize(self.db)
        logger_cache.setup(self.db,'d',2)

    def test_only_copy_if_all_pages_are_resident(self):
        logger_cache.record(self.db,'d',7)
        logger_cache.record(self.db,'d',8)
        original = logger_card.pull(self.db,'first','d',1,2)
        logger_cache.record(self.db,'d',9)
        with self.assertRaises(EvidenceError):
            logger_card.pull(self.db,'late','d',1,2)
        self.assertEqual(logger_card.read(self.db,'first'),original)

    def test_sequence_distinguishes_reused_physical_slot(self):
        a,b,c=[logger_cache.record(self.db,'d',v) for v in [10,20,30]]
        self.assertEqual(a['slot'],c['slot'])
        self.assertNotEqual(a['sequence'],c['sequence'])
        card=logger_card.pull(self.db,'second','d',2,3)
        self.assertEqual([entry['value'] for entry in card['samples']],[20,30])
