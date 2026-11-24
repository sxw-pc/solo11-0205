from hydrobook import annual
from hydrobook.formats import EvidenceError
from hydrobook.store import transaction
from support import ArchiveCase, event


class EditionTests(ArchiveCase):
    def issue(self, cut=None):
        with transaction(self.db, writing=True):
            return annual.publication(self.db, "BASIN-26", cut)

    def test_late_loop_withdraws_cross_station_peak_but_keeps_local_volume(self):
        base = self.issue()
        self.import_file("contradiction.json")
        held = self.issue()
        original, current = base["report"], held["report"]
        self.assertEqual(original["comparison"], {"state": "separable_peak", "station": "NORTH"})
        self.assertEqual(current["comparison"]["state"], "incomplete_stage")
        self.assertEqual(current["series"]["NORTH"]["stage_gaps"], [3])
        self.assertEqual(current["series"]["NORTH"]["volume_l"], original["series"]["NORTH"]["volume_l"])
        self.assertEqual(annual.read_issue(self.db, 1), base)
        self.import_file("withdraw.json")
        recovered = self.issue()
        self.assertEqual(recovered["prior"], held["issue"])
        self.assertEqual(recovered["report"]["comparison"], original["comparison"])
        self.assertEqual(annual.audit(self.db), {"verified": True, "issues": 3, "records": 32})

    def test_late_chart_witness_breaks_both_discharge_and_common_stage(self):
        self.issue()
        self.input(event("reading", "W-D4", strip="NS2", day=4, mark=341, source="witness"))
        issued = self.issue()["report"]
        north = issued["series"]["NORTH"]
        self.assertEqual(north["stage_gaps"], [4])
        self.assertEqual(north["volume_gaps"], [4])
        self.assertIsNone(north["volume_l"])
        self.assertEqual(annual.read_issue(self.db, 1)["report"]["decision"], "complete")
        self.revise("reading", "W-D4", mark=330)
        self.assertEqual(self.issue()["report"]["decision"], "complete")
        self.assertTrue(annual.audit(self.db)["verified"])

    def test_rating_withdrawal_changes_only_volume(self):
        baseline = self.issue()["report"]
        self.revise("campaign", "C-N2", result="rejected")
        current = self.issue()["report"]
        self.assertEqual(current["series"]["NORTH"]["volume_gaps"], [3, 4])
        self.assertEqual(current["comparison"], baseline["comparison"])
        self.assertEqual(current["series"]["NORTH"]["peak_mm"], baseline["series"]["NORTH"]["peak_mm"])
        self.assertTrue(annual.audit(self.db)["verified"])

    def test_uncertain_peak_order_cannot_be_decided_by_interval_midpoint(self):
        self.revise("reading", "s4", mark=297)
        result = self.issue()["report"]
        self.assertEqual(result["series"]["NORTH"]["peak_mm"], [295, 299])
        self.assertEqual(result["series"]["SOUTH"]["peak_mm"], [297, 297])
        self.assertEqual(result["comparison"]["state"], "peak_order_uncertain")

    def test_confirmed_partial_reports_replay_and_skip_past_insertion(self):
        self.import_file("contradiction.json")
        first = self.issue()
        self.assertEqual(first, self.issue())
        with self.assertRaisesRegex(EvidenceError, "cannot issue before"):
            self.issue(1)
        self.assertTrue(annual.audit(self.db)["verified"])

    def test_conflicting_series_assignments_block_publication_atomically(self):
        self.input(event("series", "ALIAS", edition="BASIN-26", station="NORTH", reach="OTHER"))
        with self.assertRaisesRegex(EvidenceError, "conflicting series"):
            self.issue()
        self.assertEqual(self.db.execute("SELECT count(*) FROM issue").fetchone()[0], 0)
        self.revise("series", "ALIAS", state="void")
        self.assertEqual(self.issue()["issue"], 1)

    def test_audit_detects_tampered_projection_even_if_unused(self):
        self.issue()
        self.db.execute("DELETE FROM tie WHERE id='T3'")
        with self.assertRaisesRegex(EvidenceError, "tie differs"):
            annual.audit(self.db)

    def test_audit_detects_issuance_chain_tampering(self):
        self.issue()
        self.import_file("contradiction.json")
        self.issue()
        self.db.execute("UPDATE issue SET prior=NULL WHERE id=2")
        with self.assertRaisesRegex(EvidenceError, "issue 2 replay mismatch"):
            annual.audit(self.db)
