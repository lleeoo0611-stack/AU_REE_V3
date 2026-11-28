import copy
from hashlib import sha256
import json
import os
from pathlib import Path
import unittest

from au_ree_v3.attribute_batches import _milestone, load_routes, read_native_claim
from au_ree_v3.census_metadata import prepare_census_metadata
from au_ree_v3.documents import DocumentError, PDFDocument
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.sources import ContentStore

PROFILE_DIR = Path(__file__).resolve().parents[1] / "config/research_profiles"
DISPLAY_PROFILE = PROFILE_DIR / "au_ree_census_display_v1.json"


class NativeAttributeBatchTests(unittest.TestCase):
    def test_batch_is_opt_in_without_moving_stage_three_pins(self):
        before = load_profile(PROFILE_DIR / "au_ree_census_evidence_v1.json")
        after = load_profile(DISPLAY_PROFILE)
        self.assertIsNone(before.attribute_batches_path)
        for name in ("profile_id", "registry_path", "census_columns_path", "attribute_bindings_path",
                     "attribute_milestones_path", "oxide_derivation_policy_path", "research_cutoff", "timezone"):
            self.assertEqual(getattr(before, name), getattr(after, name))
        payload = after.attribute_batches_path.read_bytes()
        manifest = json.loads(payload)["source_manifest_sha256"]
        routes = load_routes(payload, manifest)
        self.assertEqual(len(routes), 11)
        self.assertTrue(all("quote" not in case and "term" not in case for _, case in routes))

    def test_work_package_announcement_and_phase_are_distinct_from_project_completion(self):
        expected = _milestone("The DFS on the Common Project was completed in January 2025.")
        self.assertEqual((expected[0], expected[2]), ("DFS", "January 2025"))
        for quote in ("The DFS work package was completed in January 2025.",
                      "The DFS was released on 18 August 2026.",
                      "A DFS was completed in January 2025 for Phase 1 of the project.",
                      "The DFS was not completed in January 2025."):
            with self.subTest(quote=quote), self.assertRaises(DocumentError):
                _milestone(quote)

    def test_region_cannot_select_adjacent_table_column(self):
        source = "b" * 64
        cover = "ASX Release\n1 April 2024\n" + "\n" * 7 + "Named Subject"
        row = ("Geology    Deposit type, geological setting and style of mineralisation.    "
               "Alluvial REE deposit which is hosted by alluvial fans below the ranges.")
        page = "Criteria    JORC Code Explanation    Commentary\n" + row
        document = PDFDocument(source, (cover, page), "test")
        edition = {"project_ids": ["one"], "source_key": "source"}
        binding = {"adapter": "JORC_CLASSIFICATION", "page": 2, "lines": [2, 2], "row_line": 2,
                   "column": row.index("Alluvial") + 1,
                   "subject": {"page": 1, "lines": [10, 10], "alias": "Named Subject", "label": "Named Subject"}}
        self.assertEqual(read_native_claim(binding, edition, document).term, "ALLUVIAL_REE")
        outside = {**binding, "column": row.index("Deposit") + 1}
        with self.assertRaisesRegex(DocumentError, "COMMENTARY_COLUMN_UNBOUND"):
            read_native_claim(outside, edition, document)
        with self.assertRaisesRegex(DocumentError, "MULTIPLE_PROJECT_SUBJECTS"):
            read_native_claim(binding, {**edition, "project_ids": ["one", "two"]}, document)


@unittest.skipUnless(os.environ.get("AU_REE_MILESTONE_MANIFEST") and os.environ.get("AU_REE_MILESTONE_ASSETS"),
                     "fixed 144-SHA CAS required")
class ExactAttributeBatchTests(unittest.TestCase):
    def test_replays_issuer_evidence_and_isolates_missing_capture(self):
        assets = Path(os.environ["AU_REE_MILESTONE_ASSETS"])
        raw = Path(os.environ["AU_REE_MILESTONE_MANIFEST"]).read_bytes()
        manifest = json.loads(raw)
        settings = load_profile(DISPLAY_PROFILE)
        registry = load_registry(settings.registry_path)
        verified = verify_source_manifest(settings, manifest, registry, ContentStore(assets))
        kwargs = {"attribute_assets": assets, "source_manifest_sha256": sha256(raw).hexdigest(), "release_id": "b" * 64}
        metadata = prepare_census_metadata(DISPLAY_PROFILE, manifest, verified, **kwargs)
        records = [row for row in metadata.attribute_receipt["cases"] if row.get("adapter")]
        self.assertEqual(sum(row["selection"] is not None for row in records), 7)
        self.assertEqual(metadata.attribute_receipt["display_cells"], 27)
        self.assertEqual(metadata.attribute_receipt["mcda_inputs_added"], 0)
        self.assertEqual({row["reason"] for row in records if not row["selection"]}, {
            "TENTATIVE_GEOLOGICAL_INTERPRETATION", "REFERENCE_TO_ADJACENT_SCOPE", "NO_UNIQUE_DATED_COMPLETION",
        })
        self.assertTrue(all("review_evidence" in row for row in records if not row["selection"]))
        resolved = next(row for row in records if row["adapter"] == "JORC_STUDY_STATUS" and row["selection"])
        missing = copy.deepcopy(verified)
        missing["verified_capture_sha256"].remove(resolved["capture_sha256"])
        isolated = prepare_census_metadata(DISPLAY_PROFILE, manifest, missing, **kwargs)
        self.assertNotIn((resolved["project_id"], "開發階段代碼"), isolated.cells)
        self.assertEqual(isolated.attribute_receipt["display_cells"], 24)
        self.assertEqual(isolated.attribute_receipt["verdicts"]["SOURCE_UNAVAILABLE"], 1)
