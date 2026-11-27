"""Verify exact source inventory and the boundary between retrieval and admission."""

from datetime import date
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.source_inventory import _failures, _zip_summary, inventory_sources
from au_ree_v3.sources import ContentStore, HttpResult, acquire_sources

CAPTURE_TIME = "2026-11-27T18:00:00+00:00"
CUTOFF = date(2026, 11, 28)
BASELINE_PROFILE = SimpleNamespace(profile_id="synthetic-inventory", research_cutoff=CUTOFF,
                                   timezone=ZoneInfo("Asia/Taipei"))


def _seed(project, url, kind):
    return {"project_ids": [project], "source_key": project, "url": url, "kind": kind,
            "priority_by_project": {project: 1}, "historical_sha256": [], "reference": "synthetic"}


def _zip(name, body):
    writer = io.BytesIO()
    with ZipFile(writer, "w") as archive:
        archive.writestr(name, body)
    return writer.getvalue()


class SourceInventoryTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.store = ContentStore(self.root / "store")

    def _manifest(self, name, rows):
        seeds = [_seed(project, url, kind) for project, url, kind, _, _ in rows]
        results = {url: HttpResult("FETCHED", url, CAPTURE_TIME, 200, mime, body)
                   for _, url, _, mime, body in rows}
        with patch("au_ree_v3.sources._profile_inputs",
                   return_value=(BASELINE_PROFILE, tuple(dict.fromkeys(row[0] for row in rows)), seeds)):
            acquire_sources(Path("profile"), self.root / name, store=self.store.root,
                            fetcher=lambda url, method, timeout, max_bytes: results[url])
        return self.root / name / "source_manifest.json"

    def _run(self, name, manifest, alternates=()):
        out = self.root / name
        receipt = inventory_sources(manifest, self.store.root, out, alternate_manifests=alternates, workers=2)
        inventory = json.loads((out / "source_content_inventory.json").read_bytes())
        return receipt, {row["source_sha256"]: row for row in inventory["sources"]}

    def test_readback_deduplicates_body_and_keeps_both_project_links(self):
        csv = b'item,record\r\n"Total","123.0 tonnes TREO Mineral Resource"\r\n'
        # A numeric statement remains a retrieval passage even with qualified words.
        manifest = self._manifest("capture", [
            ("P-01", "https://one.example/data.csv", "csv", "text/csv", csv),
            ("P-02", "https://two.example/data.csv", "csv", "text/csv", csv),
        ])
        receipt, indexed = self._run("inventory", manifest)
        self.assertEqual(receipt["status"], "PASS", receipt)
        self.assertEqual(receipt["counts"]["unique_source_sha256"], 1)
        self.assertEqual(receipt["counts"]["temporal_edition_statuses"], {"ELIGIBLE": 2})
        record = indexed[sha256(csv).hexdigest()]
        self.assertEqual(record["project_ids"], ["P-01", "P-02"])
        self.assertEqual(record["deposit_scope"], "UNKNOWN")
        self.assertEqual(record["parse"]["records_including_header"], 2)
        self.assertEqual(record["content_signals"]["ORE_RESERVE"], "UNKNOWN")
        self.assertIn("123.0 tonnes", record["passages"]["REE_OXIDES"][0]["quote"])
        self.assertIn("CSV record 2", record["passages"]["REE_OXIDES"][0]["locator"])
        self.assertEqual(receipt["candidate_status"], "RETRIEVAL_ONLY")

    def test_wrong_bytes_are_isolated_and_remaining_source_still_parses(self):
        html = b"<html><p>JORC 2012 Mineral Resource</p><p>Total 123.0 tonnes TREO</p></html>"
        csv = b"a,b\n1,2\n"
        manifest = self._manifest("capture", [
            ("P-01", "https://one.example/report", "html", "text/html", html),
            ("P-02", "https://two.example/data.csv", "csv", "text/csv", csv),
        ])
        csv_sha = sha256(csv).hexdigest()
        self.store.path(csv_sha).write_bytes(b"changed")
        receipt, indexed = self._run("inventory", manifest)
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(indexed[csv_sha]["integrity"], "FAIL")
        self.assertEqual(indexed[csv_sha]["passages"], {})
        self.assertEqual(indexed[sha256(html).hexdigest()]["parse"]["status"], "TEXT_INDEXED")

    def test_tampered_temporal_state_is_blocked_even_with_original_decision_id(self):
        html = b"<html><p>No scientific values</p></html>"
        manifest = self._manifest("capture", [
            ("P-01", "https://one.example/report", "html", "text/html", html),
        ])
        data = json.loads(manifest.read_bytes())
        data["editions"][0]["temporal_decision"]["status"] = "INELIGIBLE"
        manifest.write_text(json.dumps(data), encoding="utf-8")
        receipt, indexed = self._run("inventory", manifest)
        self.assertEqual(receipt["status"], "BLOCKED")
        result = indexed[sha256(html).hexdigest()]
        self.assertEqual(result["integrity"], "EXACT_SHA")
        self.assertEqual(result["temporal_editions"][0]["reason"], "TEMPORAL_DECISION_RECOMPUTE_MISMATCH")

    def test_archive_csv_member_is_bound_without_extracting_paths(self):
        data = _zip("official/coordinates.csv", b'Project,Source\nABC,"Longitude latitude E 120.2"\n')
        digest = sha256(data).hexdigest()
        summary, found, _ = _zip_summary(DocumentCache(), data, digest)
        entry = summary["members"][0]
        self.assertEqual(entry["integrity"], "CRC_VERIFIED")
        self.assertIn("member_sha256", entry)
        self.assertTrue(found["COORDINATES"][0]["locator"].startswith("ZIP official/coordinates.csv"))
        invalid = _zip("../outside.csv", b"a,b\n1,2\n")
        with self.assertRaisesRegex(DocumentError, "ZIP_MEMBER_POLICY_REJECTED"):
            _zip_summary(DocumentCache(), invalid, sha256(invalid).hexdigest())

    def test_official_failure_recovery_distinguishes_checked_exact_and_related_files(self):
        failed = "https://www.asx.com.au/old"
        exact = "https://announcements.asx.com.au/exact.pdf"
        related = "https://announcements.asx.com.au/related.pdf"
        bad = {"requested_url": failed, "status": "MIME_MISMATCH", "project_ids": ["A"], "http_status": 200}
        matched = {"requested_url": exact, "status": "PUBLIC_CAPTURED", "project_ids": ["A"],
                   "sha256": "d1", "receipt_sha256": "r1"}
        other = {"requested_url": related, "status": "PUBLIC_CAPTURED", "project_ids": ["A"],
                 "sha256": "d2", "receipt_sha256": "r2"}
        seeds = {failed: {"historical_sha256": ["d1"]}}
        only_related = _failures([bad], [matched, other], seeds, {"r2"})[0]
        self.assertEqual(only_related["alternate_resolution"], "RELATED_OFFICIAL_SOURCE_ONLY")
        both = _failures([bad], [matched, other], seeds, {"r1", "r2"})[0]
        self.assertEqual(both["alternate_resolution"], "EXACT_HISTORICAL_BYTES_CAPTURED")
        self.assertEqual({item["relation"] for item in both["captured_official_alternatives"]},
                         {"EXACT_HISTORICAL_BYTES", "SAME_PROJECT_LINK_ONLY"})

    def test_full_pdf_view_reuses_same_parsed_extent(self):
        pdf = b"%PDF-1.4 synthetic test body"
        digest = sha256(pdf).hexdigest()
        cache = DocumentCache()
        with (patch("au_ree_v3.documents.pdf_pages", return_value=("one", "two")) as parse,
              patch("au_ree_v3.documents.pdf_parser_version", return_value="synthetic")):
            self.assertEqual(cache.pdf(pdf, digest).pages, ("one", "two"))
            self.assertEqual(cache.pdf(pdf, digest, 1).pages, ("one",))
            with self.assertRaisesRegex(DocumentError, "PHYSICAL_PAGE_MISSING"):
                cache.pdf(pdf, digest, 3)
        parse.assert_called_once_with(pdf, None)


if __name__ == "__main__":
    unittest.main()
