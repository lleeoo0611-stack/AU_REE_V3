"""Frozen source receipt verification isolates missing or corrupt source editions."""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from au_ree_v3.contracts.temporal import TemporalEvidence, evaluate_temporal
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.sources import ContentStore, HttpResult, acquire_sources


class FreshSourceVerificationTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.store = ContentStore(root / "store")
        self.profile = SimpleNamespace(
            profile_id="synthetic-proof-test", research_cutoff=date(2026, 11, 28),
            timezone=ZoneInfo("Asia/Taipei"),
        )
        projects = ("P1", "P2")
        self.registry = SimpleNamespace(projects=[SimpleNamespace(project_id=key) for key in projects])
        seeds = [{
            "project_ids": [key], "source_key": key, "url": f"https://{key.lower()}.example.test/original.pdf",
            "kind": "pdf", "reference": "fixture",
        } for key in projects]

        def fetch(url, _method, _timeout, _max_bytes):
            return HttpResult(
                "FETCHED", url, "2026-11-27T20:00:00+00:00", 200, "application/pdf",
                f"%PDF-1.4\n{url}\n%%EOF".encode(),
            )

        with patch("au_ree_v3.sources._profile_inputs", return_value=(self.profile, projects, seeds)):
            acquire_sources(Path("fixture"), root / "receipts", store=self.store.root, fetcher=fetch)
        self.manifest = json.loads((root / "receipts/source_manifest.json").read_bytes())

    def verify(self):
        return verify_source_manifest(self.profile, self.manifest, self.registry, self.store)

    def test_unreadable_edition_does_not_discard_another_projects_source(self):
        bad_sha, good_sha = [edition["sha256"] for edition in self.manifest["editions"]]
        object_path = self.store.path(bad_sha)
        self.assertEqual(self.verify()["status"], "PASS")
        object_path.unlink()
        missing = self.verify()
        self.assertEqual(missing["status"], "BLOCKED")
        self.assertEqual(missing["verified_sha256"], [good_sha])
        self.assertEqual(missing["project_statuses"], {"P1": "SOURCE_OBJECT_UNAVAILABLE", "P2": "SOURCE_ACQUIRED"})
        object_path.write_bytes(b"distinct bytes in this disposable test store")
        corrupt = self.verify()
        self.assertEqual(corrupt["status"], "FAIL")
        self.assertEqual(corrupt["verified_sha256"], [good_sha])
        self.assertEqual(corrupt["failures"][0]["reason"], "SOURCE_OBJECT_OR_PROOF_MISMATCH")

    def test_edition_clock_must_match_immutable_public_receipt(self):
        tampered, unchanged = self.manifest["editions"]
        tampered["acquired_at"] = "2026-11-26T20:00:00+00:00"
        decision = evaluate_temporal(
            source_sha256=tampered["sha256"], cutoff=self.profile.research_cutoff.isoformat(),
            observation_role="DOCUMENT", acquired_at=tampered["acquired_at"],
            reacquired_at=tampered["reacquired_at"],
            evidence=[TemporalEvidence(**row) for row in tampered["temporal_evidence"]],
        )
        self.assertEqual(decision.status, "ELIGIBLE")
        tampered["temporal_decision"] = json.loads(json.dumps(decision.to_dict()))
        result = self.verify()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["verified_sha256"], [unchanged["sha256"]])
        self.assertEqual(result["failures"], [{
            "source_sha256": tampered["sha256"], "receipt_sha256": tampered["receipt_sha256"],
            "status": "FAIL", "reason": "SOURCE_OBJECT_OR_PROOF_MISMATCH",
        }])


if __name__ == "__main__":
    unittest.main()
