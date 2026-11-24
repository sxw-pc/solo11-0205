from hydrobook import archive
from hydrobook.formats import EvidenceError
from hydrobook.store import head
from support import ArchiveCase, event


class FieldArchiveTests(ArchiveCase):
    def test_station_handover_uses_half_open_footprint(self):
        before = archive.stage(self.snapshot(), "NORTH", "UPPER", 2)
        after = archive.stage(self.snapshot(), "NORTH", "UPPER", 3)
        self.assertEqual((before["section"], before["stage"], before["datum"]), ("N-old", 240, "REF"))
        self.assertEqual((after["section"], after["stage"], after["datum"]), ("N-new", 350, "NEW"))
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 5)["state"], "unassigned_station")

    def test_overlapping_relocation_has_no_arbitrary_winner(self):
        self.revise("section", "N-old", end=4)
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 3)["state"], "overlapping_sections")
        self.assertEqual(archive.stage(self.snapshot(1), "NORTH", "UPPER", 3)["state"], "measured")
        self.revise("section", "N-old", end=3)
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 3)["state"], "measured")

    def test_moved_to_another_reach_has_no_current_series(self):
        self.revise("section", "N-new", reach="OTHER")
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 3)["state"], "wrong_reach")
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "OTHER", 3)["state"], "measured")

    def test_witness_overrides_automation_and_tied_witnesses_need_review(self):
        self.input(event("reading", "D-2", strip="NS1", day=2, mark=420, source="digitizer"))
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 2)["stage"], 240)
        self.input(event("reading", "W-2", strip="NS1", day=2, mark=235, source="witness"))
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 2)["state"], "competing_chart_marks")
        self.revise("reading", "W-2", mark=240)
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 2)["stage"], 240)

    def test_witness_outside_its_chart_scale_is_not_replaced_by_digitizer(self):
        self.input(event("reading", "D-2", strip="NS1", day=2, mark=235, source="digitizer"))
        self.revise("reading", "n2", mark=1100)
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 2)["state"], "chart_scale_exceeded")

    def test_withdrawn_highest_revision_does_not_reactivate_older_reading(self):
        self.revise("reading", "n1", state="void")
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 1)["state"], "missing_chart_mark")
        self.assertEqual(archive.stage(self.snapshot(1), "NORTH", "UPPER", 1)["stage"], 210)

    def test_revision_collision_rolls_back_preceding_new_rows(self):
        with self.assertRaisesRegex(EvidenceError, "conflicting revision"):
            self.input(event("reading", "new", strip="NS1", day=1, mark=220, source="digitizer"),
                       event("reading", "n1", strip="NS1", day=1, mark=220, source="digitizer"))
        self.assertEqual(head(self.db), 1)
        self.assertNotIn("new", self.snapshot().active("reading"))
        self.assertTrue(self.import_file("season.json")["duplicate"])

    def test_older_evidence_arriving_late_does_not_override_revision(self):
        backdated = event("reading", "past", strip="NS1", day=1, mark=215, source="witness")
        self.input(dict(backdated, rev=2, mark=230))
        self.input(backdated)
        self.assertEqual(archive.stage(self.snapshot(), "NORTH", "UPPER", 1)["stage"], 230)
