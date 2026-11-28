import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.census_criterion_evidence import CRITERIA, eligible_editions, fallback_gaps, run


class CriterionEvidenceTests(unittest.TestCase):
    def test_edition_aliases_preserve_eligible_captures_and_project_identity(self):
        def capture(id_, project, eligible):
            return {"edition_id": id_, "url": "https://example.org/original",
                    "sha256": "sha", "receipt_sha256": id_ + "-capture", "project_ids": [project],
                    "temporal_decision": {"status": "ELIGIBLE" if eligible else "INELIGIBLE"}}

        index = eligible_editions({"editions": [capture("one", "p", True), capture("two", "p", True),
                                                capture("three", "q", False)]})
        self.assertEqual({r["edition_id"] for r in index["p"]["sha"]}, {"one", "two"})
        self.assertNotIn("q", index)

    def test_no_r1_retains_separate_s1_prerequisite_and_historical_edition_gap(self):
        suggestions = [{"kinds": ["EXACT_EDITION_REQUIRED"], "raw_locator": None}]
        gaps = fallback_gaps("S1", False, [], {"other-edition": True}, suggestions)
        self.assertIn("PREREQUISITE_R1", gaps)
        self.assertIn("EDITION_MISMATCH", gaps)
        self.assertIn("COMPONENTS", gaps)
        self.assertNotIn("SOURCE_UNAVAILABLE", gaps)

    def test_missing_date_and_unwired_uses_are_investigation_dimensions(self):
        observations = [{"validation_reason": "HISTORICAL_OBSERVATION_DATE_MISSING",
                         "temporal_status": "INELIGIBLE", "selection_status": "SELECTED"}]
        gaps = fallback_gaps("R1", False, observations, {"exact-original": True}, [])
        self.assertEqual(gaps, {"TIME_ELIGIBILITY", "ESTIMATE_DATE", "APPROVED_USE_UNWIRED"})

    @unittest.skipUnless(os.environ.get("AU_REE_STAGE6_WORKSPACE"), "fixed Stage 5 audit artifacts not configured")
    def test_real_186_keys_and_preserved_diagnostic_scope(self):
        workspace = Path(os.environ["AU_REE_STAGE6_WORKSPACE"])
        work = workspace / "work/census-stage6/baseline"
        with TemporaryDirectory() as temporary:
            out = Path(temporary) / "ledger"
            receipt = run(workspace, [work / "cold-a", work / "cold-b"], out)
            rows = {(row["project_id"], row["criterion"]): row for row in map(
                json.loads, (out / "criterion_evidence_gap_ledger.jsonl").read_text().splitlines())}
            self.assertEqual(len(rows), 186)
            self.assertEqual({key[1] for key in rows}, set(CRITERIA))
            self.assertEqual(receipt["diagnostic_assessment"]["status_counts"],
                             {"COMPARABLE_DIRECT": 3, "COMPARABLE_DERIVED": 2, "INSUFFICIENT_CONTEXT": 1})
            goschen = rows["AU-6d807eb7caec9db0f35e", "R1"]
            self.assertEqual(goschen["baseline_state"], "COMPARABLE")
            self.assertEqual(goschen["diagnostic_semantic_assessment"]["missing_dimensions"], ["treo_members"])
            for project in ("AU-6d807eb7caec9db0f35e", "AU-efff91a06cf344122d88"):
                gap = rows[project, "S1"]
                self.assertEqual(gap["baseline_reason"], "NO_COMPOSITION_SELECTION")
                self.assertEqual(gap["focus_role"], "PANEL_GAP")


if __name__ == "__main__":
    unittest.main()
