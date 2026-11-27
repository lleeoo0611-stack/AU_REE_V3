import csv
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from au_ree_v3.migration.reference_oracle import compare_files

FIXTURE = Path(__file__).parent / "golden" / "synthetic_oracle_expected.jsonl"


class ReferenceOracleTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.expected = self.root / "golden_expected.jsonl"
        self.expected.write_bytes(FIXTURE.read_bytes())
        self.actual = self.root / "actual.jsonl"
        self.output = self.root / "receipt"
        self.reference = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def actual_row(self):
        row = json.loads(FIXTURE.read_text(encoding="utf-8"))
        return {
            "case_id": row["case_id"],
            "values": row["values"],
            "v3_reference": {"path": "v3-observations.jsonl", "record": 1},
        }

    def compare(self, row=None, *, case_ids=None):
        if row is not None:
            self.actual.write_text(json.dumps(row) + "\n", encoding="utf-8")
        return compare_files(self.expected, self.actual, self.output, case_ids=case_ids)

    def test_exact_match_is_counted_as_synthetic(self):
        report = self.compare(self.actual_row())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["verified_synthetic_count"], 1)
        self.assertEqual(report["verified_reference_count"], 0)
        self.assertEqual(report["inputs"]["expected"]["sha256"],
                         hashlib.sha256(FIXTURE.read_bytes()).hexdigest())
        self.assertTrue((self.output / "oracle_diff.csv").is_file())

    def test_injected_scientific_faults_fail(self):
        for path, wrong in (
            (("source_sha256",), "4" * 64),
            (("scope_id",), "synthetic-other-deposit"),
            (("cells", "nd_grade", "value"), 0),
            (("cutoff",), "2026-11-13"),
            (("raw_value",), "1.200001"),
            (("raw_value",), "1.2"),
            (("statement_id",), "synthetic-other-statement"),
            (("source_locator", "row"), "Different resource total"),
            (("selection",), "Candidate"),
            (("rejection_reason",), "EVIDENCE_INSUFFICIENT"),
            (("panel", "ranking"), ["project-C", "project-B", "project-A"]),
            (("panel", "tied_members"), ["project-A", "project-A"]),
            (("panel", "cohort"), ["project-A", "project-B"]),
            (("panel", "seed"), 8),
        ):
            with self.subTest(field=path, wrong=wrong):
                row = self.actual_row()
                node = row["values"]
                for key in path[:-1]:
                    node = node[key]
                node[path[-1]] = wrong
                report = self.compare(row)
                self.assertEqual(report["status"], "FAIL")
                differences = report["differences"]
                self.assertTrue(differences)
                pointer = "/" + "/".join(path)
                for diff in differences:
                    self.assertTrue(diff["field"] == pointer or diff["field"].startswith(pointer + "/"))
                    self.assertEqual(diff["source_reference"], FIXTURE.name)
                    self.assertEqual(diff["v3_reference"], row["v3_reference"])
                with (self.output / "oracle_diff.csv").open(newline="", encoding="utf-8") as handle:
                    exported = list(csv.DictReader(handle))
                self.assertEqual([change["field"] for change in exported],
                                 [change["field"] for change in differences])

    def test_missing_field_is_distinct_from_explicit_null(self):
        row = self.actual_row()
        del row["values"]["rejection_reason"]
        diff, = self.compare(row)["differences"]
        self.assertTrue(diff["expected_present"])
        self.assertFalse(diff["actual_present"])

    def test_extra_science_fields_are_reported(self):
        row = self.actual_row()
        row["values"]["new_processing_basis"] = "different basis"
        diff, = self.compare(row)["differences"]
        self.assertFalse(diff["expected_present"])
        self.assertTrue(diff["actual_present"])

    def test_computed_float_and_derived_decimal_follow_declared_rules(self):
        row = self.actual_row()
        row["values"]["panel"]["score"] += 0.000000002
        row["values"]["panel"]["tied_members"].reverse()
        row["values"]["panel"]["cohort"].reverse()
        row["values"]["decimal_result"] = "1.20004"
        self.assertEqual(self.compare(row)["status"], "PASS")

        row["values"]["panel"]["score"] += 0.0000001
        row["values"]["decimal_result"] = "1.20007"
        differences = self.compare(row)["differences"]
        self.assertEqual({change["field"] for change in differences},
                         {"/panel/score", "/decimal_result"})

    def test_numeric_type_and_precision_are_exact_by_default(self):
        row = self.actual_row()
        row["values"]["panel"]["seed"] = True
        diff, = self.compare(row)["differences"]
        self.assertEqual((diff["expected_type"], diff["actual_type"]), ("int", "bool"))

        row["values"]["panel"]["seed"] = 7.0
        diff, = self.compare(row)["differences"]
        self.assertEqual((diff["expected_type"], diff["actual_type"]), ("int", "Decimal"))

        row["values"]["panel"]["seed"] = 7
        raw = json.dumps(row).replace('"raw_value": "1.200000"', '"raw_value": 1.200000')
        self.actual.write_text(raw, encoding="utf-8")
        diff, = self.compare()["differences"]
        self.assertEqual((diff["expected_type"], diff["actual_type"]), ("str", "Decimal"))

    def test_scope_exclusion_must_be_explicit(self):
        unavailable = {"case_id": "archived-source-missing", "status": "BLOCKED",
                       "reason": "locked source bytes absent"}
        with self.expected.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(unavailable) + "\n")
        report = self.compare(self.actual_row())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["counts"], {"BLOCKED": 1, "PASS": 1})
        selected = self.compare(case_ids=[self.reference["case_id"]])
        self.assertEqual(selected["status"], "PASS")
        self.assertEqual(selected["scope"]["excluded_expected_count"], 1)
        self.assertEqual(self.compare(case_ids=["unknown"])["status"], "FAIL")

    def test_missing_actual_and_empty_inputs_never_pass(self):
        report = self.compare()
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertTrue((self.output / "oracle_diff.json").is_file())

        self.expected.write_text("", encoding="utf-8")
        self.actual.write_text("", encoding="utf-8")
        self.assertEqual(self.compare()["status"], "NOT_RUN")

    def test_duplicate_keys_cases_and_non_finite_numbers_fail(self):
        normal = json.dumps(self.actual_row())
        for body in (
            normal + "\n" + normal,
            normal.replace('"seed": 7', '"seed": 7, "seed": 8'),
            normal.replace('"seed": 7', '"seed": NaN'),
        ):
            with self.subTest(body=body):
                self.actual.write_text(body, encoding="utf-8")
                report = self.compare()
                self.assertEqual(report["status"], "FAIL")
                self.assertEqual(len(report["differences"]), 1)

    def test_frozen_expected_bytes_are_verified_when_manifest_is_present(self):
        manifest = self.expected.with_name("oracle_manifest.json")
        manifest.write_text(json.dumps({"expected_file": {
            "path": self.expected.name,
            "sha256": hashlib.sha256(self.expected.read_bytes()).hexdigest(),
        }}), encoding="utf-8")
        result = self.compare(self.actual_row())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["inputs"]["expected"]["manifest_path"], str(manifest))

        with self.expected.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        self.assertEqual(self.compare()["status"], "FAIL")

    def test_unknown_envelope_fields_fail(self):
        row = self.actual_row()
        row["selection"] = "Accepted"
        report = self.compare(row)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("unknown row fields", report["differences"][0]["reason"])

    def test_changed_tolerance_requires_documented_reason(self):
        self.reference["comparison_rules"]["/panel/score"]["abs_tol"] = 0.00001
        self.expected.write_text(json.dumps(self.reference), encoding="utf-8")
        report = self.compare(self.actual_row())
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("tolerance_reason", report["differences"][0]["reason"])
