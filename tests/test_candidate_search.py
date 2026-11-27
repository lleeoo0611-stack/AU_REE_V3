"""Validate candidate-only retrieval and exact document provenance."""

from hashlib import sha256
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from au_ree_v3.candidate_search import build_candidate_index, search_candidates
from au_ree_v3.sources import ContentStore, SCHEMA

from test_document_views import _example_pdf


class CandidateSearchTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.store = ContentStore(self.root / "source-store")
        self.manifest = self.root / "sources.json"

    def _source(self, digest, kind, project_id):
        return {
            "edition_id": sha256(f"{digest}|{project_id}".encode()).hexdigest(),
            "sha256": digest, "kind": kind, "project_ids": [project_id],
        }

    def _build(self, *entries, name="index"):
        projects = sorted({project for entry in entries for project in entry["project_ids"]})
        self.manifest.write_text(json.dumps({
            "schema": SCHEMA, "profile_id": "test", "cutoff": "2026-11-28",
            "timezone": "Asia/Taipei", "editions": entries,
            "projects": [{"project_id": project} for project in projects],
        }), encoding="utf-8")
        target = self.root / name
        receipt = build_candidate_index(self.manifest, self.store.root, target)
        return receipt, target / "candidate_text.sqlite"

    def test_candidate_index_deduplicates_assets_and_keeps_site_associations(self):
        report = self.store.put(b"<html><p>Leach recovery 80%</p><p>Independent example</p></html>")
        mine = self.store.put(b"provenance,label,source\nGovernment,official,longitude\n")
        first = self._source(report, "html", "P1")
        second = self._source(report, "html", "P2")
        csv = self._source(mine, "csv", "P2")
        receipt, db = self._build(first, second, csv)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["source_count"], 2)
        self.assertEqual(receipt["index_statuses"], {"INDEXED": 2})
        results = search_candidates(db, "T1a", self.root / "search", project_id="P1")
        self.assertEqual(results["scientific_admissions"], 0)
        self.assertEqual(len(results["matches"]), 1)
        hit = results["matches"][0]
        self.assertEqual(hit["status"], "CANDIDATE")
        self.assertEqual(hit["source_sha256"], report)
        self.assertEqual(hit["original_text"], "Leach recovery 80%")
        self.assertEqual(hit["project_ids"], ["P1", "P2"])
        self.assertTrue(hit["locator"].startswith("HTML fragment 1,"))
        filtered = search_candidates(db, "GIS", self.root / "filtered", project_id="P1")
        self.assertEqual(filtered["matches"], [])
        unfiltered = search_candidates(db, "GIS", self.root / "coordinates")
        self.assertTrue(unfiltered["matches"][0]["locator"].startswith("CSV record 2"))
        repeated, second_db = self._build(first, second, csv, name="rebuild")
        self.assertEqual(repeated, receipt)
        self.assertEqual(second_db.read_bytes(), db.read_bytes())

    def test_raw_search_words_are_literals_and_the_index_is_not_overwritten(self):
        report = self.store.put(b"<html><p>Leach recovery 80%</p></html>")
        entry = self._source(report, "html", "P1")
        _, db = self._build(entry)
        before = db.read_bytes()
        good = search_candidates(db, "T1a", self.root / "literal", query="leach recovery")
        self.assertEqual(len(good["matches"]), 1)
        quoted = search_candidates(db, "T1a", self.root / "words", query='leach" OR recovery')
        self.assertEqual(quoted["matches"], [])
        with self.assertRaises(ValueError):
            search_candidates(db, "invalid-purpose", self.root / "bad")
        with self.assertRaises(FileExistsError):
            self._build(entry)
        self.assertEqual(db.read_bytes(), before)

    def test_wrong_sha_is_isolated_from_available_candidate_text(self):
        missing = self.store.put(b"<html><p>before</p></html>")
        self.store.path(missing).write_bytes(b"changed")
        available = self.store.put(b"<html><p>Mining production</p></html>")
        receipt, db = self._build(self._source(missing, "html", "P1"),
                                  self._source(available, "html", "P2"))
        self.assertEqual(receipt["status"], "FAIL")
        self.assertEqual(receipt["index_statuses"], {"FAIL": 1, "INDEXED": 1})
        results = search_candidates(db, "D1", self.root / "partial")
        self.assertEqual([row["source_sha256"] for row in results["matches"]], [available])

    @unittest.skipUnless(shutil.which("pdftotext"), "Poppler unavailable")
    def test_pdf_hit_uses_physical_page_and_original_text_span(self):
        digest = self.store.put(_example_pdf())
        receipt, db = self._build(self._source(digest, "pdf", "P1"))
        results = search_candidates(db, "R1", self.root / "query", query="grade")
        hit, = results["matches"]
        self.assertEqual((hit["page"], hit["line_start"], hit["line_end"]), (1, 1, 1))
        self.assertEqual(hit["original_text"], "Grade 0.0040")
        self.assertEqual(hit["locator"], "PDF page 1, lines 1-1")
        self.assertEqual(receipt["sources"][0]["spans"], 1)


if __name__ == "__main__":
    unittest.main()
