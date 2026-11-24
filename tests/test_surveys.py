from hydrobook import flow, horizon
from support import ArchiveCase, event


class SurveyTests(ArchiveCase):
    def test_joint_constraints_narrow_offset_and_respect_budget_endpoints(self):
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 3, 6)["range"], [-55, -51])
        self.assertEqual(horizon.bounds(self.snapshot(), "REF", "NEW", 3, 4)["range"], [51, 55])
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 3, 3)["state"], "error_budget_exceeded")
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 5, 6)["state"], "unlinked_datums")

    def test_late_certified_tie_makes_only_affected_day_impossible(self):
        self.import_file("contradiction.json")
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 3, 6)["state"], "inconsistent_level_loop")
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 4, 6)["state"], "aligned")
        self.import_file("withdraw.json")
        self.assertEqual(horizon.bounds(self.snapshot(), "NEW", "REF", 3, 6)["state"], "aligned")

    def test_rejected_survey_excludes_every_tie_using_that_review(self):
        self.revise("survey", "SV1", result="rejected")
        result = horizon.bounds(self.snapshot(), "NEW", "REF", 3, 6)
        self.assertEqual(result["state"], "unlinked_datums")
        self.assertEqual({row[1] for row in result["excluded"]}, {"SV1", "T1", "T2", "T3"})

    def test_same_datum_is_comparable_without_any_survey(self):
        self.revise("survey", "SV1", result="rejected")
        self.assertEqual(horizon.bounds(self.snapshot(), "REF", "REF", 1, 0)["range"], [0, 0])


class DischargeTests(ArchiveCase):
    def test_piecewise_rating_no_extrapolation_and_no_future_gaugings(self):
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["litres_per_second"], [80, 110])
        self.assertEqual(flow.curve(self.snapshot(), "N2", "REF", 3, 320, 350)["state"], "no_certified_campaign")
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 299, 350)["state"], "unbracketed_stage")
        self.revise("gauging", "C-N2-H", day=4)
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["state"], "unbracketed_stage")
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 4, 320, 350)["state"], "rated")

    def test_rounded_outward_fractional_discharge(self):
        self.revise("gauging", "C-N2-H", flow=161)
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 350, 350)["litres_per_second"], [110, 111])

    def test_competing_certified_curves_fail_until_review(self):
        self.input(event("campaign", "OVER", profile="N2", datum="NEW", start=3, end=5, result="certified"))
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["state"], "overlapping_campaigns")
        self.revise("campaign", "OVER", result="rejected")
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["state"], "rated")

    def test_contested_gaugings_and_non_monotone_revisions_fail_differently(self):
        self.input(event("gauging", "DUP", campaign="C-N2", day=3, stage=300, flow=73))
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["state"], "contested_gauging")
        self.revise("gauging", "DUP", state="void")
        self.revise("gauging", "C-N2-H", flow=0)
        self.assertEqual(flow.curve(self.snapshot(), "N2", "NEW", 3, 320, 350)["state"], "non_monotone_rating")
