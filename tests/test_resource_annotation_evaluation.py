"""Fault injection for the source-transcription evaluator."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tools.evaluate_resource_annotations import evaluate


class ResourceAnnotationsTests(unittest.TestCase):
    def setUp(self):
        self.first = b"synthetic source bytes"
        self.second = b"separate synthetic source bytes"
        self.source = sha256(self.first).hexdigest()
        self.holdout = sha256(self.second).hexdigest()
        self.design = {
            "case_id": "design", "split": "DESIGN_REGRESSION", "project_id": "P01", "source_sha256": self.source,
            "formal_status": "APPROVED", "forbidden_selected_pages": [3],
            "expected_observations": [
                {"variable": "contained_treo_kt", "raw_value": "7,500", "value": "7.500", "unit": "kt TREO",
                 "pages": [2], "selected": True},
            ],
        }
        self.held = {"case_id": "held", "split": "INDEPENDENT_HOLDOUT", "project_id": "P02",
                     "source_sha256": self.holdout, "formal_status": "WITHHOLD", "forbidden_selected_pages": [],
                     "expected_observations": []}
        self.annotations = {"schema": "independent-resource-annotations-v1", "cutoff": "2026-11-28",
                            "cases": [self.design, self.held]}
        self.extraction = {"cutoff": "2026-11-28", "cases": [self.design]}
        self.row = {"project_id": "P01", "source_sha256": self.source, "variable": "contained_treo_kt", "page": 2,
                    "raw_value": "7,500", "value": "7.5", "unit": "kt TREO", "selected_for_census": True}

    def run_evaluation(self, obs, *, corruption=False):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / self.source).write_bytes(b"changed" if corruption else self.first)
            (root / self.holdout).write_bytes(self.second)
            return evaluate(self.annotations, self.extraction, obs, root)

    def test_exact_candidate_and_unattempted_holdout(self):
        report = self.run_evaluation([self.row])
        design = report["partitions"]["DESIGN_REGRESSION"]
        holdout = report["partitions"]["INDEPENDENT_HOLDOUT"]
        self.assertEqual((design["candidate_tp"], design["precision"], design["recall"]), (1, 1.0, 1.0))
        self.assertEqual(holdout["status"], "NOT_RUN")
        self.assertIsNone(holdout["precision"])
        self.assertIsNone(holdout["candidate_fn"])

    def test_lexical_error_and_cross_scope_promotion_fail(self):
        wrong = {**self.row, "raw_value": "7500"}
        reserved = {**self.row, "page": 3}
        result = self.run_evaluation([wrong, reserved])["cases"][0]
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual((result["candidate_tp"], result["candidate_fp"], result["candidate_fn"]), (0, 1, 1))
        self.assertEqual(result["formal_false_admissions"], 2)

    def test_corrupt_source_cannot_support_quality_metrics(self):
        report = self.run_evaluation([self.row], corruption=True)
        self.assertEqual(report["cases"][0]["source_integrity"], "FAIL")
        self.assertEqual(report["partitions"]["DESIGN_REGRESSION"]["measured_cases"], 0)

    def test_holdout_split_rejects_project_or_source_overlap(self):
        clean = deepcopy(self.annotations)
        for key in ("project_id", "source_sha256"):
            self.annotations = deepcopy(clean)
            self.annotations["cases"][1][key] = self.annotations["cases"][0][key]
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "Holdout overlaps"):
                self.run_evaluation([])
