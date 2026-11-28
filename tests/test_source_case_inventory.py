import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from au_ree_v3.documents import PDFDocument
from au_ree_v3 import source_case_inventory as inventory


class SourceCaseInventoryTests(unittest.TestCase):
    def setUp(self):
        self.source = "a" * 64
        self.row = "Total  1.00  25.0  250"
        self.caption = "Table 1 Mineral Resource for Block A"
        header = "Tonnage Mt  TREO grade ppm  Contained TREO t"
        self.document = PDFDocument(self.source, ("\n".join([
            self.caption, header, self.row, "JORC 2012; in situ; cutoff 300 ppm as of 2025-05-01",
        ]),), "test")
        self.candidate = {
            "source_sha256": self.source, "project_id": "AU-A", "physical_page": 1,
            "locator": "PDF page 1, line 3", "caption_locator": "PDF page 1, line 1",
            "header_locator": "PDF page 1, lines 2-2", "caption": self.caption, "row_token": self.row,
            "native_numeric_tokens": [{"token": "1.00", "start_column": 8, "end_column": 11}],
            "evidence_limits": ["VALUE_COLUMN_ASSOCIATIONS_NOT_VALIDATED"],
        }

    def test_native_token_roundtrip_and_physical_dedup_keep_project_scopes(self):
        left, = inventory._structural_cases(
            self.candidate, {"source_sha256": self.source, "project_id": "AU-A"}, self.document, {}, {},
        )
        right, = inventory._structural_cases(
            self.candidate, {"source_sha256": self.source, "project_id": "AU-B"}, self.document, {}, {},
        )
        self.assertEqual(left["source_token"], "1.00")
        self.assertEqual(left["source_position_id"], right["source_position_id"])
        self.assertNotEqual(left["review_unit_id"], right["review_unit_id"])
        self.assertNotEqual(left["case_id"], right["case_id"])
        self.assertEqual(left["role_guess"], "UNBOUND_TABLE_NUMBER")
        self.assertEqual(left["header"]["text"], "Tonnage Mt  TREO grade ppm  Contained TREO t")
        self.assertTrue(left["priority_signals"]["POSSIBLE_DIRECT_CONTENT_HEADER"])
        self.assertTrue(left["priority_signals"]["ESTIMATE_DATE_TEXT_NEAR_TABLE"])
        self.assertTrue(left["priority_signals"]["CUTOFF_TEXT_NEAR_TABLE"])
        self.assertEqual(left["status"], "NOT_RUN")

    def test_unavailable_original_restricts_context_and_priority(self):
        available, = inventory._structural_cases(
            self.candidate, {"source_sha256": self.source, "project_id": "AU-A",
                             "edition_id": "public", "physical_page": 1}, self.document, {}, {},
        )
        blocked, = inventory._structural_cases(
            self.candidate, {"source_sha256": self.source, "project_id": "AU-B",
                             "edition_id": "public", "physical_page": 1}, None, {},
            {"status": "SOURCE_UNAVAILABLE"},
        )
        self.assertEqual(blocked["status"], "SOURCE_UNAVAILABLE")
        self.assertIsNone(blocked["header"]["text"])
        self.assertEqual([r["project_id"] for r in inventory._prioritize([blocked, available])], ["AU-A", "AU-B"])

    def test_wrong_coordinates_and_altered_row_reject(self):
        with self.assertRaisesRegex(ValueError, "Lexeme differs"):
            inventory._position(self.candidate, {"token": "1.0", "start_column": 8, "end_column": 11})
        with self.assertRaisesRegex(ValueError, "Routed row differs"):
            inventory._location_and_context({**self.candidate, "row_token": self.row + "  0"}, self.document)

    def test_frozen_bytes_and_output_path_guard(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / "asset"
            file.write_bytes(b"new edition")
            with self.assertRaisesRegex(ValueError, "Frozen input SHA-256 mismatch"):
                inventory._read(file, "a" * 64)
            with self.assertRaisesRegex(ValueError, "outside source"):
                inventory.run(verification=root, routing=root, discovery=root, sources=root,
                              assets=root, v2_root=root, out=root / "escape")

    def test_exact_archived_source_and_different_edition_remain_separate(self):
        body = b"%PDF-synthetic"
        locked = hashlib.sha256(body).hexdigest()
        public_url = "https://public.example.invalid/report"
        manifest = json.dumps({"assets": [{
            "expected_sha256": locked,
            "locked_sources": [{"kind": "pdf", "original_url": public_url}],
            "acquisition_attempts": [{"url": public_url, "status": "DISTINCT_VERSION_RETAINED",
                                      "actual_sha256": self.source}],
        }]}).encode()
        with patch.object(inventory, "V2_RECOVERY_SHA", hashlib.sha256(manifest).hexdigest()), patch.object(
            inventory, "_git_blob", side_effect=[manifest, body],
        ):
            gaps, status = inventory._gap_rows(Path("."), [{
                "status": "SOURCE_EDITION_NOT_IN_V3", "legacy_source_shas": [locked],
                "project_id": "AU-A", "column": "ore_mt", "quantity_group": "RESOURCE",
            }], {self.source: [{"project_ids": ["AU-A"], "url": public_url,
                               "historical_reference_sha256": [locked], "version_status": "NEW_SHA"}]})
        self.assertEqual(status, "EXACT_SHA")
        self.assertEqual(gaps[0]["status"], "SOURCE_UNAVAILABLE")
        self.assertEqual(gaps[0]["read_only_v2_snapshot"]["observed_sha256"], locked)
        self.assertEqual(gaps[0]["distinct_v3_editions"][0]["sha256"], self.source)
        self.assertEqual(gaps[0]["live_public_retrieval"], "NOT_RUN")
