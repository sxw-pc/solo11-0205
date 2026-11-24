from contextlib import closing
from unittest import TestCase

from hydrobook.formats import EvidenceError
from hydrobook import lab_book, sample_labels
from hydrobook.store import connect, initialize


class LabBookTests(TestCase):
    def setUp(self):
        self.db = self.enterContext(closing(connect(':memory:')))
        initialize(self.db)
        sample_labels.initialize(self.db)
        sample_labels.label(self.db, 'L1', 'S1', 'north', 3, 'field-1')

    def test_reveal_requires_locked_result_and_independent_operator(self):
        with self.assertRaises(EvidenceError):
            lab_book.reveal(self.db, 'L1', 'audit')
        self.assertTrue(lab_book.record(self.db, 'L1', 'bench', 45, 'bench-1')['locked'])
        with self.assertRaises(EvidenceError):
            lab_book.reveal(self.db, 'L1', 'bench')
        self.assertEqual(lab_book.reveal(self.db, 'L1', 'audit')['origin']['sample'], 'S1')

    def test_repeated_result_compares_original(self):
        lab_book.record(self.db, 'L1', 'bench', 45, 'bench-1')
        self.assertTrue(lab_book.record(self.db, 'L1', 'bench', 45, 'bench-1')['duplicate'])
        with self.assertRaises(EvidenceError):
            lab_book.record(self.db, 'L1', 'bench', 46, 'bench-1')
