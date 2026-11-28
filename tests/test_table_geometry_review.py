import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, skipUnless
from unittest.mock import patch

from au_ree_v3.documents import DocumentCache
from au_ree_v3.registry import load_registry
from au_ree_v3.table_geometry_review import SCHEMA, _matching_cell, _matching_row, _review_cases, geometry_resource_reviews

CONFIG = Path(__file__).resolve().parents[1] / "config/research_profiles"
TOWER_SHA = "0ae98cfd93d554b8d948267a50f9c821761fee97c5a230440055c06029fc9d63"


class GeometryReviewTest(TestCase):
    def test_selects_an_exact_source_row_and_rejects_repeated_labels(self):
        case = {"caption": "Table 2 – Main Mineral Resource", "row_label": "Total"}
        row = SimpleNamespace(table_kind="MINERAL_RESOURCE", caption_bbox=(0, 0, 1, 1), **case)
        unrelated = SimpleNamespace(table_kind="NON_RESOURCE", caption_bbox=(0, 0, 1, 1), **case)
        self.assertIs(_matching_row((row, unrelated), case), row)
        with self.assertRaisesRegex(ValueError, "uniquely"):
            _matching_row((row, row), case)

    def test_quantity_requires_an_independent_header_and_printed_unit(self):
        expected = {"role": "grade", "header": "TREO", "unit": "ppm"}
        target = SimpleNamespace(**expected, text="1,200", header_bbox=(0, 0, 2, 2), unit_bbox=(1, 1, 3, 3), unit_text="ppm")
        wrong_header = SimpleNamespace(**{**vars(target), "header": "TREO−CeO₂"})
        joined_values = SimpleNamespace(**{**vars(target), "text": "1,200 120"})
        self.assertIs(_matching_cell(SimpleNamespace(cells=(target, wrong_header, joined_values)), expected), target)
        with self.assertRaisesRegex(ValueError, "uniquely"):
            _matching_cell(SimpleNamespace(cells=(wrong_header,)), expected)

    @patch("au_ree_v3.table_geometry_review.version", return_value="1.26.6")
    def test_unqualified_capture_cannot_enter_the_display_path(self, _):
        spec = {"schema": SCHEMA, "source_manifest_sha256": "manifest", "method": "pymupdf", "runtime": "1.26.6",
                "cases": [{"project_id": "AU-1", "capture_sha256": "capture", "physical_page": 2,
                           "cells": [{"role": "ore_mt"}]}]}
        edition = {"sha256": "original", "kind": "pdf", "project_ids": ["AU-1"],
                   "temporal_decision": {"status": "CANDIDATE"}}
        with self.assertRaisesRegex(ValueError, "verified Project"):
            _review_cases(spec, {"capture": edition}, {"AU-1": object()}, "manifest")

    @skipUnless(os.environ.get("AU_REE_GEOMETRY_TOWER_PDF"), "exact Mt Clere Tower PDF required")
    def test_tower_quantities_replay_as_display_candidates_with_the_printed_cutoff(self):
        spec = json.loads((CONFIG / "resource_table_geometry_review_v1.json").read_bytes())
        case, = spec["cases"]
        capture = case["capture_sha256"]
        edition = {
            "receipt_sha256": capture, "edition_id": "tower-fixture", "sha256": TOWER_SHA,
            "kind": "pdf", "project_ids": [case["project_id"]], "publication_date": None,
            "url": "https://announcements.asx.com.au/asxpdf/20230808/pdf/05sf46x92l83s0.pdf",
            "temporal_decision": {"status": "ELIGIBLE", "earliest_proven_public_date": "2026-11-28", "decision_id": "fixture"},
        }
        original = Path(os.environ["AU_REE_GEOMETRY_TOWER_PDF"]).read_bytes()
        registry = load_registry(CONFIG / "project_registry_2026-11-28.json")
        reviews = geometry_resource_reviews(
            spec, editions={capture: edition}, registry=registry, manifest_sha=spec["source_manifest_sha256"],
            content=SimpleNamespace(read=lambda _: original), cache=DocumentCache(), archived=(),
        )
        self.assertEqual({row["context"]["census_column_id"]: row["context"]["display_value"] for row in reviews},
                         {"ore_mt": "101 Mt", "treo_grade_pct": "840 ppm"})
        self.assertTrue(all(row["context"]["reported_cutoff"] == "300 ppm TREO-CeO2"
                            and row["scope"] == "Tower Mineral Resource Summary"
                            and row["state"] == "CANDIDATE" and "bbox" in row["locator"] for row in reviews))
