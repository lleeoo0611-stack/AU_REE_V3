import csv
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.census_display_coverage import compare_legacy, display_state, estimated_review_scope, run


class CoverageSemanticsTests(unittest.TestCase):
    def test_legacy_comparison_distinguishes_formatting_from_display_difference(self):
        self.assertEqual(compare_legacy("1.20", "1.2", "decimal"), "FORMATTING_ONLY")
        self.assertEqual(compare_legacy("1.20", "1.2", "text"), "DIFFERENT_DISPLAY_VALUE")
        self.assertEqual(compare_legacy("", "archived", "text"), "V2_ONLY")

    def test_missing_source_and_quote_statuses_separate_from_verified_value(self):
        missing = {"value_state": "UNKNOWN"}
        self.assertEqual(display_state(missing, [], False), "SOURCE_MISSING")
        self.assertEqual(display_state(missing, [], True), "UNKNOWN")
        self.assertEqual(display_state(missing, [{"locator": "p1"}], True), "SOURCE_QUOTE_ONLY")
        with self.assertRaisesRegex(ValueError, "affirmative inapplicability"):
            display_state({"value_state": "NOT_APPLICABLE", "metadata_evidence": ""}, [], True)

    def test_scope_upper_bound_counts_three_projections_as_one_assertion(self):
        item = {"route": "FIXED_ORIGINAL_REREAD", "source_sha256": "source", "project_id": "project",
                "assertion_family": "DEVELOPMENT_STAGE", "adds_new_project_reach_if_valid": True,
                "fields": [{"data_type": "text"}] * 3}
        self.assertEqual(estimated_review_scope([item, item]), [{
            "source_sha256": "source", "data_type": "text", "distinct_project_upper_bound": 1,
            "distinct_new_project_upper_bound": 1, "distinct_assertion_target_upper_bound": 1,
        }])


class ExactCoverageTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AU_REE_STAGE4_WORKSPACE"), "fixed Stage 4 source corpus not configured")
    def test_fixed_grid_and_repeated_artifact_bytes(self):
        workspace = Path(os.environ["AU_REE_STAGE4_WORKSPACE"])
        v2 = Path(os.environ["AU_REE_STAGE4_V2_ROOT"])
        root = workspace / "work/census-stage4"
        with TemporaryDirectory() as temporary:
            first, second = [Path(temporary) / suffix for suffix in ("first", "second")]
            for out in (first, second):
                receipt = run(workspace, v2, root / "current-main", [root / "cold-a", root / "cold-b"], out)
                self.assertEqual((receipt["C4-0"], receipt["C4-1"]), ("PASS", "PASS"))
                self.assertEqual(receipt["kpis"]["meaningful_project_reach"], 9)
                self.assertEqual(sum(receipt["historical_comparison_counts"].values()), 4743)
            for file in (*receipt["outputs_sha256"], "coverage_baseline_receipt.json"):
                self.assertEqual((first / file).read_bytes(), (second / file).read_bytes())
            with (first / "core51_v2_v3_cell_comparison.csv").open(newline="") as stream:
                keys = {(row["project_id"], row["column_id"]) for row in csv.DictReader(stream)}
            self.assertEqual(len(keys), 4743)


if __name__ == "__main__":
    unittest.main()
