import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from tools.census_gap_inventory import classify, check_bytes, fingerprint, run


UNKNOWN = {"value_state": "UNKNOWN", "reason": "NO_SELECTED_MRE", "evidence_state": "EVIDENCE_INSUFFICIENT"}


class GapClassificationTests(unittest.TestCase):
    def test_unselected_resource_is_unknown_even_without_original(self):
        self.assertEqual(classify(UNKNOWN, [], set(), set(), set()), "SOURCE_NOT_INDEXED")
        self.assertEqual(classify(UNKNOWN, [], set(), {"historic-sha"}, set()), "SOURCE_EDITION_MISSING")
        self.assertEqual(classify(UNKNOWN, [], {"current-sha"}, {"historic-sha"}, set()), "STATEMENT_SCOPE_UNBOUND")
        with self.assertRaisesRegex(ValueError, "applicability decision"):
            classify({**UNKNOWN, "value_state": "NOT_APPLICABLE"}, [], set(), set(), set())

    def test_field_bound_signals_prioritize_time_scope_and_use(self):
        for reason, status, selection, expected in [
            ("HISTORICAL_OBSERVATION_DATE_MISSING", "EVIDENCE_INSUFFICIENT", None, "TEMPORAL_EVIDENCE_MISSING"),
            ("EXACT_SOURCE_AND_RESOURCE_EVIDENCE", "VALIDATED", "SHADOW_OUTSIDE_REPORTING_SCOPE",
             "STATEMENT_SCOPE_UNBOUND"),
            ("NO_APPROVED_CENSUS_DERIVATION_SELECTION", "SHADOW_VALIDATED", None,
             "VALIDATED_BUT_NO_APPROVED_USE"),
            ("SAME_SCOPE_COMPANION_OXIDE_PRECISION_DISAGREEMENT", "SCIENTIFIC_CONFLICT", None,
             "CHEMICAL_OR_MATERIAL_CONFLICT"),
        ]:
            with self.subTest(reason=reason):
                signals = [{"reason": reason, "status": status, "selection": selection}]
                self.assertEqual(classify(UNKNOWN, signals, {"source"}, set(), {"HEADER_OR_UNIT_UNBOUND"}), expected)

    def test_reported_absence_requires_an_exact_original_anchor(self):
        cell = {**UNKNOWN, "reason": "NOT_REPORTED", "source_sha256": "", "anchor_id": ""}
        self.assertEqual(classify(cell, [], set(), set(), set()), "SOURCE_NOT_INDEXED")
        cell.update(source_sha256="original-sha", anchor_id="source-anchor")
        self.assertEqual(classify(cell, [], set(), set(), set()), "NOT_REPORTED")

    def test_frozen_receipt_rejects_changed_bytes(self):
        original = b'{"eligible":true}'
        self.assertEqual(check_bytes(original, fingerprint(original), "test"), original)
        with self.assertRaisesRegex(ValueError, "Fingerprint mismatch"):
            check_bytes(b'{"eligible":false}', fingerprint(original), "test")


class RealGapInventoryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("AU_REE_STAGE3_WORKSPACE"), "fixed research originals and receipts not configured")
    def test_originals_grid_and_determinism(self):
        workspace = Path(os.environ["AU_REE_STAGE3_WORKSPACE"])
        v2_root = Path(os.environ["AU_REE_STAGE3_V2_ROOT"])
        prior = workspace / "work/census-stage3"
        with tempfile.TemporaryDirectory() as temporary:
            destinations = [Path(temporary) / trial for trial in ("first", "second")]
            for out in destinations:
                receipt = run(workspace, v2_root, prior / "baseline-main", [prior / "cold-a", prior / "cold-b"], out)
                self.assertEqual(receipt["counts"]["cells"], 4743)
                self.assertEqual(receipt["counts"]["comparable_keys"], {"R1": 4, "S1": 2})
                self.assertEqual(receipt["distinct_fixed_and_historical_queue_items"], {"BLOCKED": 4, "READY": 55})
                self.assertEqual(receipt["routing"]["unbound_scoped_sightings"], 143)
            for filename in list(receipt["outputs_sha256"]) + ["stage3_baseline.json"]:
                self.assertEqual((destinations[0] / filename).read_bytes(), (destinations[1] / filename).read_bytes())
            with (destinations[0] / "gap_funnel.csv").open(newline="") as stream:
                cells = list(csv.DictReader(stream))
            self.assertEqual(len({(row["project_id"], row["column"]) for row in cells}), 4743)
            with (destinations[0] / "census_gap_queue.jsonl").open() as stream:
                queue = [json.loads(line) for line in stream]
            self.assertEqual(len({item["queue_id"] for item in queue}), 59)
            self.assertTrue(all(not item["formal_admission"] for item in queue))
            self.assertTrue(all("value" not in hint for item in queue for hint in item["same_edition_v2_hints"]))


if __name__ == "__main__":
    unittest.main()
