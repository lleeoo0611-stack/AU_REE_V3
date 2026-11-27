"""Frozen source receipt verification isolates missing or corrupt source editions."""

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.sources import ContentStore, HttpResult, acquire_sources


class FreshSourceVerificationTests(unittest.TestCase):
    def test_unreadable_edition_does_not_discard_another_projects_source(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = ContentStore(root / "store")
            profile = SimpleNamespace(
                profile_id="synthetic-proof-test", research_cutoff=date(2026, 11, 28),
                timezone=ZoneInfo("Asia/Taipei"),
            )
            registry = SimpleNamespace(projects=[SimpleNamespace(project_id=key) for key in ("P1", "P2")])
            urls = {key: f"https://{key.lower()}.example.test/original.pdf" for key in ("P1", "P2")}
            seeds = [{
                "project_ids": [key], "source_key": key, "url": url, "kind": "pdf", "reference": "fixture",
            } for key, url in urls.items()]

            def fetch(url, _method, _timeout, _max_bytes):
                return HttpResult(
                    "FETCHED", url, "2026-11-27T20:00:00+00:00", 200, "application/pdf",
                    f"%PDF-1.4\n{url}\n%%EOF".encode(),
                )

            with patch("au_ree_v3.sources._profile_inputs", return_value=(profile, ("P1", "P2"), seeds)):
                acquire_sources(Path("fixture"), root / "receipts", store=store.root, fetcher=fetch)
            manifest = json.loads((root / "receipts/source_manifest.json").read_bytes())
            bad_sha, good_sha = [edition["sha256"] for edition in manifest["editions"]]
            object_path = store.path(bad_sha)
            self.assertEqual(verify_source_manifest(profile, manifest, registry, store)["status"], "PASS")
            object_path.unlink()
            missing = verify_source_manifest(profile, manifest, registry, store)
            self.assertEqual(missing["status"], "BLOCKED")
            self.assertEqual(missing["verified_sha256"], [good_sha])
            self.assertEqual(missing["project_statuses"], {"P1": "SOURCE_OBJECT_UNAVAILABLE", "P2": "SOURCE_ACQUIRED"})
            object_path.write_bytes(b"distinct bytes in this disposable test store")
            corrupt = verify_source_manifest(profile, manifest, registry, store)
            self.assertEqual(corrupt["status"], "FAIL")
            self.assertEqual(corrupt["verified_sha256"], [good_sha])
            self.assertEqual(corrupt["failures"][0]["reason"], "SOURCE_OBJECT_OR_PROOF_MISMATCH")


if __name__ == "__main__":
    unittest.main()
