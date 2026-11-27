from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.migration.baseline import ARCHIVED_CENSUS, BASELINES
from au_ree_v3.migration.temporal_audit import (
    TemporalInputs,
    _audit_row,
    _capture_evidence,
    _read_expected,
    audit_temporal,
)


class TemporalAuditTests(unittest.TestCase):
    def setUp(self):
        self.digest = "a" * 64
        self.key = ("frozen-pdf", self.digest)
        self.cutoff = "2026-11-13"
        self.old_at = "2026-11-03T01:00:00+00:00"
        self.new_at = "2026-11-17T01:00:00+00:00"
        self.publication = "2024-06-01"
        self.url = "https://example.test/frozen.pdf"
        self.values = {
            "observation_id": "observation-1", "project_id": "project-1",
            "statement_id": "statement-1", "scope": "scope-1", "source_sha256": self.digest,
            "effective_date": "2024-05-31", "effective_date_precision": "DAY",
            "publication_date": self.publication, "publication_date_precision": "DAY",
            "available_date": "2026-11-17", "acquired_at": self.old_at, "research_cutoff": self.cutoff,
            "selected_for_census": True, "selection_role": "MATCHED_SELECTED_STATEMENT", "source_url": self.url,
        }
        self.expected = {"case_id": f"{ARCHIVED_CENSUS}:observation:observation-1", "values": self.values}
        self.inputs = TemporalInputs(
            observations={"observation-1": {**self.values, "selected_for_census": "1"}},
            selection={self.key: {
                "cutoff": self.cutoff, "publication_date": self.publication,
                "available_date": "2026-11-17", "date_basis": "DOCUMENT_PUBLICATION",
            }},
            acquisition={self.key: {
                "publication_date": self.publication, "available_date": "2026-11-17",
                "acquired_at": self.new_at, "url": self.url,
            }},
            release={self.key: {
                "publication_date": {"value": self.publication},
                "available_date": {"value": "2026-11-03"}, "acquired_at": {"value": self.old_at},
            }},
            captures={self.key: {
                "key": self.key[0], "sha256": self.digest, "kind": "pdf", "url": self.url,
                "available_date": "2026-11-03", "acquired_at": self.old_at,
                "publication_date": self.publication,
            }},
            phase22_required=frozenset({self.digest}),
            references={"capture": {"commit": "a" * 40, "path": "lock.json", "sha256": "b" * 64}},
            cutoff=self.cutoff,
        )

    def test_same_edition_reacquisition_keeps_both_observed_dates(self):
        result = _audit_row(self.expected, [self.key], self.inputs)
        self.assertEqual(result["unclassified_temporal_decision"]["status"], "CONFLICT")
        self.assertEqual(result["v3_temporal_decision"]["status"], "ELIGIBLE")
        self.assertEqual(result["observed_v2_dates"]["available_date"], "2026-11-17")
        self.assertEqual(result["v3_temporal_decision"]["earliest_proven_public_date"], "2026-11-03")
        self.assertEqual(result["reacquired_at"], self.new_at)
        self.assertTrue(result["observed_availability_after_acquisition"])
        self.assertEqual(result["formal_selection_disposition"], "HISTORICAL_COMPARISON_ONLY")

    def test_other_edition_and_unverified_capture_cannot_support_promotions(self):
        other_edition = ("frozen-pdf", "f" * 64)
        wrong = replace(self.inputs, captures={other_edition: self.inputs.captures[self.key]})
        unverified = replace(self.inputs, phase22_required=frozenset())
        for inputs in (wrong, unverified):
            with self.subTest(inputs=inputs):
                result = _audit_row(self.expected, [self.key], inputs)
                self.assertEqual(result["v3_temporal_decision"]["status"], "CANDIDATE")
                self.assertEqual(result["temporal_evidence"], [])

    def test_capture_timestamp_is_confirmed_in_both_lock_and_release(self):
        inconsistent = replace(self.inputs, release={self.key: {
            **self.inputs.release[self.key], "available_date": {"value": "2026-11-04"},
        }})
        self.assertEqual(_capture_evidence(self.values, self.key, inconsistent), ())

    def test_observation_dates_must_match_frozen_v2_payload(self):
        changed = {**self.expected, "values": {**self.values, "effective_date": "2024-02-01"}}
        with self.assertRaisesRegex(ValueError, "temporal facts disagree"):
            _audit_row(changed, [self.key], self.inputs)

    def test_oracle_fingerprint_mismatch_fails_before_reading_rows(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            expected = root / "expected.jsonl"
            manifest = root / "oracle_manifest.json"
            payload = json.dumps(self.expected).encode()
            expected.write_bytes(payload)
            manifest.write_text(json.dumps({
                "expected_file": {"path": expected.name, "sha256": sha256(payload).hexdigest()},
                "ref": BASELINES["refs"]["engineering"], "science_ref": BASELINES["refs"]["science"],
                "case_count": 1,
            }))
            rows, _, _ = _read_expected(expected, manifest)
            self.assertEqual(rows, [self.expected])
            expected.write_bytes(payload + b"\n")
            with self.assertRaisesRegex(ValueError, "Oracle manifest"):
                _read_expected(expected, manifest)

    def test_unavailable_v2_refs_block_the_audit(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = audit_temporal(root / "absent", root / "expected", root / "oracle", root / "work")
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertFalse((root / "work" / "temporal_dispositions.jsonl").exists())
