"""Check independently transcribed native values through both authorized research uses."""

from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from au_ree_v3.contracts import ResearchRelease, canonical_json
from au_ree_v3.fresh_observations import extract_sources
from au_ree_v3.research_inputs import CENSUS_RESOURCE_RULE, checked_research_inputs
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
