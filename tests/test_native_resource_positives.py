"""Check independently transcribed native values through both authorized research uses."""

from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from au_ree_v3.contracts import ResearchRelease, canonical_json
from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.fresh_observations import extract_sources
from au_ree_v3.native_table_discovery import discover_page_rows
from au_ree_v3.research_inputs import CENSUS_RESOURCE_RULE, checked_research_inputs
from au_ree_v3.resource_table_binding import bind_page_row
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/native_resource_v1"
CONFIG = ROOT / "config/research_profiles/resource_bindings_v4.json"

# Native length/count presentations kept as printed. No historical Census values.
EXPECTED = {
    "AU-dac3c8e40326b8244cdb": {
        "page": 4, "effective": "2026-09", "cutoff": "1000",
        "cells": {"ore_mt": ("1.7", "Bt", "1700", "Mt"),
                  "grade": ("1,700", "ppm", "1700", "ppm")},
    },
    "AU-216d0983f6892f9b58f9": {
        "page": 97, "effective": "2026-08", "cutoff": "300",
        "cells": {"ore_mt": ("278.80", "Mt", "278.8", "Mt"),
                  "grade": ("482.64", "ppm", "482.64", "ppm")},
    },
}


@unittest.skipUnless(os.environ.get("AU_REE_NATIVE_RESOURCE_ASSETS") and shutil.which("pdftotext")
                     and shutil.which("pdfimages"), "exact ASX originals and Poppler required")
class NativeResourcePositiveTests(unittest.TestCase):
    def _exact_document(self, source_sha, page):
        bases = Path(os.environ["AU_REE_NATIVE_RESOURCE_ASSETS"])
        source = next(path for path in (bases / source_sha, bases / "objects" / source_sha) if path.is_file())
        original = source.read_bytes()
        self.assertEqual(sha256(original).hexdigest(), source_sha)
        return DocumentCache().pdf(original, source_sha, page)

    def test_exact_threshold_series_keeps_three_original_inventories_separate(self):
        doc = self._exact_document("eb484b6f7118d7319173b4f3637b2b66e9058abdf69bf6d5cc1cf358306190e5", 1)
        rows = discover_page_rows(doc, page=1)
        measured = [tuple((cell.variable, cell.value.text, cell.unit) for cell in row.cells if cell.variable)
                    for row in rows]
        self.assertEqual(measured, [
            (("ore_mt", "29.98", "Mt"), ("grade", "1.04", "%"), ("contained_treo_kt", "311,800", "t")),
            (("ore_mt", "21.06", "Mt"), ("grade", "1.35", "%"), ("contained_treo_kt", "284,300", "t")),
            (("ore_mt", "11.63", "Mt"), ("grade", "1.93", "%"), ("contained_treo_kt", "224,400", "t")),
        ])
        self.assertEqual([row.context_anchors[-1].text for row in rows], ["0.20", "0.40", "1.00"])
        self.assertTrue(all(row.cells[0].reason == "CUTOFF_THRESHOLD_IS_NOT_A_RESOURCE_GRADE" for row in rows))
        self.assertNotIn("40.82", {cell.value.text for row in rows for cell in row.cells})

    def test_exact_companion_unit_and_modified_chemistry_are_separate(self):
        wudinna = self._exact_document("89467aedb62238c6a98902633fba1bcaf4ccd0f081beaa736facf71533ac3864", 3)
        row, = discover_page_rows(wudinna, page=3)
        grade, = [cell for cell in row.cells if cell.variable == "grade"]
        self.assertEqual((grade.value.text, grade.unit, grade.unit_label.physical_page), ("470", "ppm", 1))
        self.assertIn("200 ppm TREO-Ce", grade.caption[0].text)
        goschen = self._exact_document("5f8f642b6c9b561ca0f397fee0990e52786d85af3e67d53756def87b5cde2cd0", 12)
        row, = discover_page_rows(goschen, page=12)
        self.assertEqual(row.strategy, "NAMED_RESOURCE_SUBTABLE")
        self.assertEqual([cell.value.text for cell in row.cells], ["94,200,000", "0.08", "74,000"])
        self.assertEqual([cell.variable for cell in row.cells], ["ore_mt", None, None])
        self.assertTrue(all(cell.reason == "CHEMICALLY_MODIFIED_TREO_IS_NOT_STANDARD_TREO" for cell in row.cells[1:]))

    def test_native_discovery_distinguishes_issuer_vintage_and_row_marker(self):
        source_sha = "bc93c9d96bccf1d5badff0508915633117c723f7858ccddbfedb8a3be938ef7e"
        bases = Path(os.environ["AU_REE_NATIVE_RESOURCE_ASSETS"])
        source = next(path for path in (bases / source_sha, bases / "objects" / source_sha) if path.is_file())
        original = source.read_bytes()
        self.assertEqual(sha256(original).hexdigest(), source_sha)
        document = DocumentCache().pdf(original, source_sha, 4)
        row, = discover_page_rows(document, page=4)
        self.assertEqual(row.strategy, "NUMBERED_RESOURCE_TABLE")
        self.assertEqual([(c.variable, c.value.text, c.unit, c.parent_header and c.parent_header.text) for c in row.cells], [
            (None, "1,000", None, None),
            ("ore_mt", "1.8", "Bt", "MRE - 2025"), ("grade", "1,700", "ppm", "MRE - 2025"),
            ("ore_mt", "1.7", "Bt", "MRE - 2026"), ("grade", "1,700", "ppm", "MRE - 2026"),
        ])
        marker = row.cells[2].footnote_marker
        self.assertEqual(marker.text, "*")
        self.assertEqual(document.locate(4, line=marker.line, column=marker.start_column,
                                         end_column=marker.end_column).text, "*")

    def test_exact_issuer_total_uses_same_table_alignment_witnesses(self):
        source_sha = "a861c5b1088895ecd45210d6d7d3e3a319deee81dd85853ebdc184fd4c1b8acb"
        bases = Path(os.environ["AU_REE_NATIVE_RESOURCE_ASSETS"])
        source = next(path for path in (bases / source_sha, bases / "objects" / source_sha) if path.is_file())
        original = source.read_bytes()
        self.assertEqual(sha256(original).hexdigest(), source_sha)
        document = DocumentCache().pdf(original, source_sha, 14)
        rows = document.locate(14).text.splitlines()
        caption, = [i for i, row in enumerate(rows, start=1) if row.strip().startswith("Table 2: Total Yangibana Project")]
        total = next(i for i in range(caption + 1, len(rows) + 1) if rows[i - 1].strip().startswith("TOTAL "))
        result = bind_page_row(document, page=14, row_line=total, caption_line=caption)
        self.assertEqual([(cell.value.text, cell.variable, cell.unit) for cell in result], [
            ("29.93", "ore_mt", "Mt"), ("0.93", "grade", "%"),
            ("0.32", None, None), ("276,955", "contained_treo_kt", "t"),
        ])
        self.assertEqual({cell.layout_method for cell in result[:2]}, {"TABLE_WIDE_ROW_ALIGNMENT"})
        for cell in result:
            located_value = document.locate(14, line=cell.value.line, column=cell.value.start_column,
                                            end_column=cell.value.end_column)
            self.assertEqual(located_value.text, cell.value.text)
        reserve, = [i for i, row in enumerate(rows, start=1) if row.strip().startswith("Table 1: Total Yangibana Project")]
        with self.assertRaisesRegex(DocumentError, "ROW_OUTSIDE_SOURCE_TABLE"):
            bind_page_row(document, page=14, row_line=total, caption_line=reserve)

    def test_exact_source_pairs_through_store_and_purpose_gate(self):
        bases = Path(os.environ["AU_REE_NATIVE_RESOURCE_ASSETS"])
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            store = ContentStore(directory / "cas")
            manifest = FIXTURE / "acquisition_manifest.json"
            source_manifest = json.loads(manifest.read_bytes())
            for capture in (FIXTURE / "captures").glob("*.json"):
                self.assertEqual(store.put(capture.read_bytes()), capture.stem)
            editions = source_manifest["editions"]
            for edition in editions:
                source_sha = edition["sha256"]
                source = next(path for path in (bases / source_sha, bases / "objects" / source_sha) if path.is_file())
                self.assertEqual(store.put(source.read_bytes()), source_sha)
            # Check that v4 still contains the exact approved real-source recipes being tested.
            spec = json.loads(CONFIG.read_bytes())
            spec["bindings"] = [binding for binding in spec["bindings"]
                                if binding.get("source_sha256") in {row["sha256"] for row in editions}]
            self.assertEqual(len(spec["bindings"]), len(EXPECTED))
            bindings = directory / "bindings.json"
            bindings.write_text(canonical_json(spec), encoding="utf-8")
            database = directory / "research.sqlite"
            result = extract_sources(bindings, manifest, store.root, database, directory / "result",
                                     code_commit="a" * 40)
            diagnostic = [(row["case_id"], row["verdict"], row["reason"])
                          for row in result["census_resource_admission"]]
            self.assertEqual(result["status"], "PASS", diagnostic)
            self.assertEqual(result["census_only_selected_count"], 4)
            self.assertEqual(result["selected_count"], 4)
            self.assertEqual({entry["verdict"] for entry in result["census_resource_admission"]}, {"SELECTED"})
            with Store(database, trusted_temporal_evidence_ids=result["trusted_temporal_evidence_ids"]) as ledger:
                release = ledger.get(ResearchRelease, result["release_id"])
                census = checked_research_inputs(ledger, release, purpose="CENSUS")
                self.assertEqual(checked_research_inputs(ledger, release, purpose="MCDA"), ())
                self.assertEqual({row.accepted.reason for row in census}, {CENSUS_RESOURCE_RULE})
                for project, target in EXPECTED.items():
                    selected = [entry for entry in census if entry.raw.project_id == project]
                    self.assertEqual({entry.anchor.page for entry in selected}, {target["page"]})
                    self.assertEqual({entry.raw.effective_date for entry in selected}, {target["effective"]})
                    self.assertEqual(len({entry.raw.statement_id for entry in selected}), 1)
                    self.assertEqual(len({entry.scope.scope_id for entry in selected}), 1)
                    self.assertEqual({entry.raw.variable for entry in selected}, set(target["cells"]))
                    for entry in selected:
                        raw, converted = entry.raw, entry.candidate
                        expected_raw, expected_unit, expected_value, expected_conversion = target["cells"][raw.variable]
                        self.assertEqual((raw.raw_value, raw.raw_unit), (expected_raw, expected_unit))
                        self.assertEqual((Decimal(converted.value), converted.unit),
                                         (Decimal(expected_value), expected_conversion))
                        payload = json.loads(raw.original_payload)
                        self.assertEqual(payload["cutoff_grade"], target["cutoff"])
                        self.assertEqual(payload["cutoff_analyte"], "TREO")
                        self.assertEqual(entry.validation.outcome, "VALIDATED")
                        self.assertIn(raw.raw_value, entry.anchor.quote)

            # Both counterexamples use the real source bytes and valid capture proofs.
            jupiter = next(binding for binding in spec["bindings"]
                           if binding["project"]["native_id"] == "jupiter")
            for column in jupiter["table"]["columns"]:
                column["group_header"] = "MRE - 2025"
            self.assert_only_other_pair(spec, bindings, manifest, store, directory, "wrong-year", "north-stanmore")
            for column in jupiter["table"]["columns"]:
                column["group_header"] = "MRE - 2026"
            north = next(binding for binding in spec["bindings"]
                         if binding["project"]["native_id"] == "north-stanmore")
            north["treo_image_context"]["images"][0]["sha256"] = "0" * 64
            self.assert_only_other_pair(spec, bindings, manifest, store, directory, "bad-chemistry-image", "jupiter")

    def assert_only_other_pair(self, spec, bindings, manifest, store, directory, check, valid_project):
        bindings.write_text(canonical_json(spec), encoding="utf-8")
        output = directory / check
        result = extract_sources(bindings, manifest, store.root, output / "research.sqlite", output,
                                 code_commit="a" * 40)
        selected = {row["project_id"] for row in result["census_resource_admission"] if row["verdict"] == "SELECTED"}
        expected = {binding["project"]["entity_id"] for binding in spec["bindings"]
                    if binding["project"]["native_id"] == valid_project}
        self.assertEqual(selected, expected)
        self.assertEqual(result["selected_count"], 2)
