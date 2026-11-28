"""Reroute pinned hints in original V3 sources while preserving the candidate boundary."""

import json
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from au_ree_v3.contracts import canonical_json
from au_ree_v3.documents import PDFDocument
from au_ree_v3.migration.baseline import ARCHIVED_CENSUS, BASELINES
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.source_routing import QUEUE_SCHEMA, _native_rows, route_source_work
from au_ree_v3.sources import ContentStore, HttpResult, acquire_sources

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/research_profiles/au_ree_2026-11-28.json"
SETTINGS = load_profile(PROFILE)
REGISTERED = [project.project_id for project in load_registry(SETTINGS.registry_path).projects]
TOKEN_PAGE = """Table 1: Alpha Mineral Resource estimate
Classification        Tonnage Mt        TREO %        Contained TREO kt
Indicated                  6.0             1.00              60
Total                      6.000           1.005             60.3
"""
REFUSED_PAGE = """Table 2: Beta Ore Reserve for the Alpha Mineral Resource scoping program
Classification        Tonnage Mt        TREO %        Contained TREO kt
Total                      2.3             1.10               25.3
"""


def write_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(document) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(path.read_bytes())


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


class SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.assets = ContentStore(self.root / "assets")
        self.first, self.second, self.third = REGISTERED[:3]
        self.state = SimpleNamespace(profile_id=SETTINGS.profile_id, research_cutoff=SETTINGS.research_cutoff,
                                     timezone=SETTINGS.timezone)
        uri = "https://publisher.test/alpha.pdf"
        second_uri = "https://publisher.test/beta.pdf"
        seed = [
            {"project_ids": [self.first, self.second], "source_key": "first", "url": uri, "kind": "pdf",
             "priority_by_project": {}, "historical_sha256": []},
            {"project_ids": [self.third], "source_key": "second", "url": second_uri, "kind": "pdf",
             "priority_by_project": {}, "historical_sha256": []},
        ]
        documents = {uri: b"%PDF-1.4\nsynthetic one", second_uri: b"%PDF-1.4\nsynthetic two"}
        with patch("au_ree_v3.sources._profile_inputs", return_value=(self.state, (self.first, self.second, self.third), seed)):
            acquire_sources(PROFILE, self.root / "acquisition", store=self.assets.root, fetcher=lambda url, *_:
                            HttpResult("FETCHED", url, "2026-11-27T18:00:00+00:00",
                                       200, "application/pdf", documents[url]))
        self.sources = self.root / "acquisition/source_manifest.json"
        self.ids = {entry["source_key"]: entry["sha256"] for entry in read_json(self.sources)["editions"]}
        self.queue = self.root / "queue.json"
        self.proof = self.root / "recovery.json"
        self.bindings = self.root / "bindings.json"
        write_json(self.bindings, {"schema": "fresh-resource-extraction-v1", "profile_id": SETTINGS.profile_id,
                                   "cutoff": SETTINGS.research_cutoff.isoformat(), "timezone": SETTINGS.timezone.key,
                                   "bindings": []})

    def item(self, project, kind="first", pages=(1,)):
        return {"source_sha256": self.ids[kind], "project_id": project, "target_columns": ["TREO品位_pct"],
                "value_pages": pages, "context_pages": pages, "legacy_seed_pages": (), "value_hints": 1,
                "status": "CANDIDATE", "read_original_cas_again": True, "formal_admission": False}

    def queue_rows(self, *rows):
        write_json(self.queue, {"schema": QUEUE_SCHEMA, "rows": rows, "candidate_only": True,
                                "formal_scientific_values_added": 0})
        provenance = next(item["sha256"] for item in BASELINES["pins"][ARCHIVED_CENSUS]
                          if item["path"].endswith("/field_provenance.csv"))
        write_json(self.proof, {
            "schema": "v2-resource-value-rediscovery-v1", "status": "PASS", "candidate_only": True,
            "formal_scientific_values_added": 0, "v2_ref": BASELINES["refs"]["engineering"],
            "v2_field_provenance_sha256": provenance, "v3_candidate_index_sha256": "f" * 64,
            "index_schema": "candidate-evidence-index-v1", "project_cohort": 93,
            "source_manifest_sha256": digest(self.sources), "work_queue_sha256": digest(self.queue),
            "unique_source_project_work_items": len(rows),
            "unique_eligible_source_sha": len({item["source_sha256"] for item in rows}),
        })

    def run_route(self, name="route"):
        return route_source_work(profile=PROFILE, queue=self.queue, recovery_receipt=self.proof, sources=self.sources,
                                 assets=self.assets.root, bindings=self.bindings, out=self.root / name)

    @staticmethod
    def decoded(*args):
        body, digest = args[-2:]
        return PDFDocument(digest, (TOKEN_PAGE, REFUSED_PAGE), "synthetic native runtime")

    def test_native_total_row_is_bound_to_printed_tokens_and_exact_span(self):
        document = PDFDocument("a" * 64, (TOKEN_PAGE, REFUSED_PAGE), "synthetic")
        item, = _native_rows(document, 1)
        self.assertEqual(item["locator"], "PDF page 1, line 4")
        self.assertEqual(item["caption_locator"], "PDF page 1, line 1")
        self.assertEqual([token["token"] for token in item["native_numeric_tokens"]], ["6.000", "1.005", "60.3"])
        for cell in item["native_numeric_tokens"]:
            self.assertEqual(document.locate(1, line=4, column=cell["start_column"],
                                             end_column=cell["end_column"]).text, cell["token"])
        self.assertNotIn("variable", item)
        self.assertEqual(list(_native_rows(document, 2)), [])

    def test_exact_document_reads_once_for_two_project_pages_and_replay_matches(self):
        self.queue_rows(self.item(self.first), self.item(self.second))
        with patch("au_ree_v3.documents.DocumentCache.pdf", side_effect=self.decoded) as rendered:
            first = self.run_route()
            second = self.run_route("rerun")
        self.assertEqual(rendered.call_count, 2)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual((first["source_project_work_items"], first["unique_source_pages_inspected"]), (2, 1))
        self.assertEqual(first["unique_native_structural_tokens"], 3)
        self.assertEqual(first["legacy_display_values_loaded"], 0)
        self.assertEqual(first["formal_scientific_values_added"], 0)
        self.assertEqual(first["original_candidate_count_by_method"], {"NATIVE_TABLE_TOTAL_ROW": 2})
        rows = [json.loads(row) for row in (self.root / "route/routed_original_candidates.jsonl").read_text().splitlines()]
        self.assertEqual({row["project_id"] for row in rows}, {self.first, self.second})
        self.assertTrue(all(row["candidate_only"] and not row["formal_admission"] for row in rows))
        with self.assertRaises(FileExistsError):
            self.run_route()

    def test_wrong_project_and_missing_source_are_isolated_from_a_valid_item(self):
        self.queue_rows(self.item(self.first), self.item(self.third), self.item(self.third, "second"))
        self.assets.path(self.ids["second"]).unlink()
        with patch("au_ree_v3.documents.DocumentCache.pdf", side_effect=self.decoded) as rendered:
            result = self.run_route()
        self.assertEqual(rendered.call_count, 1)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["issues_by_work_item"], {
            "NO_EXACT_V3_PROJECT_SOURCE_ASSOCIATION": 1, "SOURCE_BYTES_UNAVAILABLE": 1,
        })
        self.assertEqual(result["candidate_work_statuses"], {"BLOCKED": 2, "CANDIDATE_ROWS_REVIEWED": 1})

    def test_wrong_sha_and_missing_physical_page_do_not_promote_output(self):
        self.queue_rows(self.item(self.first, pages=(1, 6)), self.item(self.third, "second"))
        self.assets.path(self.ids["second"]).write_bytes(b"%PDF-wrong")
        with patch("au_ree_v3.documents.DocumentCache.pdf", side_effect=self.decoded):
            result = self.run_route()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["issues_by_work_item"], {"SOURCE_SHA_MISMATCH": 1, "REQUESTED_PHYSICAL_PAGE_MISSING": 1})
        self.assertEqual((result["unique_source_pages_requested"], result["unique_source_pages_inspected"]), (3, 1))

    def test_receipt_mismatch_or_extra_numeric_hint_fields_refuse_queue(self):
        row = self.item(self.first)
        self.queue_rows(row)
        self.queue.write_bytes(self.queue.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "does not pin"):
            self.run_route()
        self.queue_rows({**row, "legacy_display_hint": "999.0"})
        with self.assertRaisesRegex(ValueError, "only source"):
            self.run_route()
        self.queue_rows(row, row)
        with self.assertRaisesRegex(ValueError, "unique source-Project"):
            self.run_route()
        self.assertFalse((self.root / "route").exists())

    def test_output_is_validated_before_opening_discovery_or_source_inputs(self):
        occupied = self.root / "occupied"
        occupied.mkdir()
        prior = occupied / "previous.txt"
        prior.write_text("immutable receipt")
        with patch("au_ree_v3.source_routing._inputs") as inputs:
            with self.assertRaisesRegex(FileExistsError, "new empty output"):
                self.run_route("occupied")
            with self.assertRaisesRegex(ValueError, "separate from the immutable Content Store"):
                self.run_route("assets/objects/forbidden")
        inputs.assert_not_called()
        self.assertEqual(prior.read_text(), "immutable receipt")
        self.assertFalse((self.assets.root / "objects/forbidden").exists())

    def test_unverified_temporal_evidence_never_gets_decoded(self):
        original = read_json(self.sources)
        included = next(row for row in original["editions"] if row["source_key"] == "first")
        self.assets.path(included["receipt_sha256"]).unlink()
        self.queue_rows(self.item(self.first))
        with patch("au_ree_v3.documents.DocumentCache.pdf", side_effect=self.decoded) as rendered:
            result = self.run_route()
        rendered.assert_not_called()
        self.assertEqual(result["exact_source_sha_read"], 1)
        self.assertEqual(result["unique_source_pages_inspected"], 0)
        self.assertEqual(result["issues_by_work_item"], {"NO_ELIGIBLE_VERIFIED_CAPTURE_FOR_PROJECT": 1})
        route_file = self.root / "route/routed_source_work.json"
        self.assertNotIn(str(self.assets.root), route_file.read_text())
        work_item, = read_json(route_file)["rows"]
        self.assertEqual(work_item["temporal_editions"][0]["reason"], "CAPTURE_PROOF_UNAVAILABLE_OR_UNVERIFIED")



if __name__ == "__main__":
    unittest.main()
