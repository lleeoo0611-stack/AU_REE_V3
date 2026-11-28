import csv
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

SPEC = importlib.util.spec_from_file_location("resource_column_review", Path(__file__).resolve().parents[1]
                                              / "tools/resource_column_review.py")
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)


class ResourceColumnReviewTest(unittest.TestCase):
    def test_repeated_number_alignment_uses_its_printed_occurrence(self):
        raw = {"physical_page": 2, "row_anchor": {"text": "Total    20    20    85"},
               "value_anchor": {"start_column": 16, "end_column": 17, "text": "20", "line": 4}}
        cells = [SimpleNamespace(text=x, bbox=i) for i, x in enumerate(("20", "20", "85"))]
        geometry = SimpleNamespace(text="Total 20 20 85", cells=cells)
        self.assertIs(review._position(raw, [geometry])[1], cells[1])
        self.assertIsNone(review._position(raw, [geometry, geometry]))

    def test_deduplication_preserves_original_source_scope(self):
        raw = {"source_sha256": "a" * 64, "physical_page": 2, "row_anchor": {"locator": "row one"},
               "value_anchor": {"locator": "value one"}, "header_anchor": {"locator": "text header"},
               "unit_anchor": {"locator": "unit"}, "caption_anchors": [{"locator": "investigation A"}]}
        another = {**raw, "row_anchor": {"locator": "row two"}, "value_anchor": {"locator": "value two"}}
        self.assertEqual(review._column_key(raw, object()), review._column_key(another, object()))
        self.assertNotEqual(review._column_key(raw, object()), review._column_key(
            {**another, "caption_anchors": [{"locator": "investigation B"}]}, object()))

    def test_numerical_source_routes_do_not_turn_derived_or_metadata_into_direct_labels(self):
        base = {"column_id": "contained_treo_kt", "historical_method": "ORE_MT_X_GRADE_PERCENT_X_10"}
        self.assertEqual(review._classification(base), "DERIVED_INPUTS")
        self.assertEqual(review._classification({**base, "historical_method": "REPORTED"}), "DIRECT_TARGET")
        self.assertEqual(review._classification({**base, "column_id": "has_treo_resource"}), "CONTEXT_OR_DISCLOSURE")

    def test_bound_annotation_is_local_and_tampering_refused(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            queue, labels, output = (parent / name for name in ("queue.json", "labels.csv", "result.json"))
            card = {"card_id": "a" * 64, "source_sha256": "b" * 64, "physical_page": 4,
                    "witnesses": [{"candidate_id": "c" * 64}], "linked_targets": [["AU-test", "ore_mt"]]}
            card["evidence_sha256"] = review._key(card)
            review._write(queue, {"schema": review.QUEUE, "purpose": review.PURPOSE, "cards": [card]})
            pin = review._digest(queue.read_bytes())
            valid = ["a" * 64, card["evidence_sha256"], "ASSIGNED", "ore_mt", "source review", "4.02"]
            def load(rows):
                with labels.open("w", newline="", encoding="utf-8") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(review.LABEL_FIELDS)
                    writer.writerows(rows)
            load([valid])
            report = review.import_labels(queue, pin, labels, output)
            self.assertEqual(report["counts"], {"ASSIGNED": 1})
            self.assertEqual(report["median_seconds"], 4.02)
            result = json.loads(output.read_bytes())["annotations"][0]
            self.assertEqual(result["purpose"], review.PURPOSE)
            self.assertEqual(result["affected_census_cells"], [["AU-test", "ore_mt"]])
            self.assertNotIn("formal_admission", result)
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                review.import_labels(queue, "different-pin", labels, parent / "bad-pin.json")
            with self.assertRaises(FileExistsError):
                review.import_labels(queue, pin, labels, output)
            load([valid, valid])
            with self.assertRaisesRegex(ValueError, "duplicate"):
                review.import_labels(queue, pin, labels, parent / "duplicated.json")
            load([["a" * 64, "0" * 64, *valid[2:]]])
            with self.assertRaisesRegex(ValueError, "changed"):
                review.import_labels(queue, pin, labels, parent / "bad-card.json")
            load([valid[:2] + ["UNCERTAIN", "ore_mt", "source review", "4.02"]])
            with self.assertRaisesRegex(ValueError, "Assigned column role"):
                review.import_labels(queue, pin, labels, parent / "wrong-role.json")
