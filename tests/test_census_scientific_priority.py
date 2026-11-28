import csv
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.census_scientific_priority import direction, evidence_key, fingerprint, run
from au_ree_v3.contracts import canonical_json


class ScientificPriorityTests(unittest.TestCase):
    def test_keeps_exact_edition_scope_date_cutoff_analyte_and_location_separate(self):
        parts = ["p", "sha", "edition", "statement", "2026-09", {"grade": "1000", "unit": "ppm"}, "TREO", "p4"]
        old = fingerprint(canonical_json(evidence_key(*parts)).encode())
        for i in range(len(parts)):
            other = parts.copy()
            other[i] = "other"
            self.assertNotEqual(old, fingerprint(canonical_json(evidence_key(*other)).encode()))
        unbound = evidence_key("p", "sha", None, None, None, None, None, None, source_request_family="DEPOSIT_TYPE")
        self.assertIsNone(unbound["raw_locator"])

    def test_prior_raw_observation_is_not_new_independent_work_yield(self):
        candidate = {"project_id": "p", "field_ids": {"ore_mt"}, "kinds": {"REVIEWED_ORIGINAL_OBSERVATION"},
                     "blocking_reasons": {"HISTORICAL_OBSERVATION_DATE_MISSING"},
                     "source_edition_id": "edition", "raw_locator": "p2", "native_tokens": ["5"],
                     "baseline_fields": [{"field_id": "ore_mt", "value_state": "UNKNOWN"}]}
        definitions = {"ore_mt": {"display_group": "resource"}}
        matrix = {"p": {"R1": {"state": "UNKNOWN"}, "S1": {"state": "COMPARABLE"}}}
        result = direction(candidate, definitions, matrix)
        self.assertEqual(result["conditional_new_criterion_targets"], ["R1"])
        self.assertEqual(result["upper_bound"]["new_independent_direct_observations_for_one_located_token"], 0)
        self.assertEqual(result["upper_bound"]["new_complete_r1_s1_panel_project_if_all_requirements_met"], 1)

    def test_conflict_has_no_science_yield_until_reconciled(self):
        candidate = {"project_id": "p", "field_ids": {"contained_treo_kt"}, "kinds": {"REVIEWED_ORIGINAL_OBSERVATION"},
                     "blocking_reasons": {"CONFLICTING_CONTAINED_TREO_IN_SAME_STATEMENT"},
                     "source_edition_id": "edition", "raw_locator": "p2", "native_tokens": ["5"],
                     "baseline_fields": [{"field_id": "contained_treo_kt", "value_state": "UNKNOWN"}]}
        definitions = {"contained_treo_kt": {"display_group": "resource"}}
        matrix = {"p": {"R1": {"state": "UNKNOWN"}, "S1": {"state": "UNKNOWN"}}}
        result = direction(candidate, definitions, matrix)
        self.assertEqual(result["disposition"], "SCIENTIFIC_CONFLICT")
        self.assertEqual(result["upper_bound"]["new_project_criterion_keys_if_all_purpose_requirements_met"], 0)

    @unittest.skipUnless(os.environ.get("AU_REE_STAGE5_WORKSPACE"), "fixed replay and source corpus not configured")
    def test_exact_fresh_replays_and_repeatable_full_grid(self):
        workspace = Path(os.environ["AU_REE_STAGE5_WORKSPACE"])
        v2_root = Path(os.environ["AU_REE_STAGE5_V2_ROOT"])
        trials = workspace / "work/census-stage5"
        with TemporaryDirectory() as temporary:
            left, right = (Path(temporary) / name for name in ("left", "right"))
            for out in (left, right):
                receipt = run(workspace, v2_root, [trials / "cold-a", trials / "cold-b"],
                              [trials / "display-cold-a", trials / "display-cold-b"], out)
                self.assertEqual(receipt["coverage"]["verified_display"], 46)
                self.assertEqual(receipt["v2_display_comparison"]["V2_ONLY"], 1362)
                self.assertEqual(receipt["received_prior_unbound_sightings"], 142)
                self.assertEqual(receipt["received_prior_no_structure_jobs"], 15)
                self.assertEqual(set(item["stage4_exact_public_edition"] for item in receipt["prior_edition_gaps"]),
                                 {"SOURCE_UNAVAILABLE"})
            for name in ("census_scientific_baseline_receipt.json", *receipt["outputs_sha256"]):
                self.assertEqual((left / name).read_bytes(), (right / name).read_bytes())
            with (right / "census_scientific_gap_grid.csv").open(newline="") as stream:
                keys = {(row["project_id"], row["field_id"]) for row in csv.DictReader(stream)}
            self.assertEqual(len(keys), 4743)


if __name__ == "__main__":
    unittest.main()
