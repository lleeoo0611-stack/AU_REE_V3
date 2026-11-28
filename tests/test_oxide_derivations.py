"""Validate Stage 3's separately pinned reported-group derivation."""

from dataclasses import replace
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from au_ree_v3.census_derivations import REPORTED_MREO_COLUMN
from au_ree_v3.contracts import ContractViolation, DerivedValue, ResearchRelease
from au_ree_v3.documents import DocumentCache
from au_ree_v3.observations import digest
from au_ree_v3.oxide_derivations import (
    FORMULA, OXIDE_FORMULA_REGISTRY_SHA256, OXIDE_RELEASE_PIN, OXIDE_VARIABLE, REVIEW,
    assess_reported_mreo, checked_census_oxide_derivations, load_oxide_derivation_policy, persist_census_oxide,
    registered_oxide_formula, verify_oxide_derivation_assessment, verify_reviewed_mreo_document,
)
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.store import Store
from au_ree_v3.web_publication import WebPublicationError, _check_derived_references

ROOT = Path(__file__).resolve().parents[1]


class RegistryTests(unittest.TestCase):
    def test_exact_policy_pin_and_no_other_formula(self):
        policy_path = ROOT / "config/research_profiles/census_oxide_derivations_v1.json"
        policy = load_oxide_derivation_policy(policy_path)
        self.assertEqual(policy["formula_registry_sha256"], OXIDE_FORMULA_REGISTRY_SHA256)
        self.assertEqual(registered_oxide_formula(FORMULA["id"])["input_role"], "REPORTED_NAMED_GROUP")
        self.assertIsNone(registered_oxide_formula("complete_group_mass"))
        self.assertFalse(verify_oxide_derivation_assessment({"formula_id":FORMULA["id"]}))
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "bad.json"
            file.write_text(json.dumps({**policy,"formula_id":"complete_group_mass"}))
            with self.assertRaisesRegex(ValueError, "does not match"):
                load_oxide_derivation_policy(file)


    def test_web_reported_group_requires_all_three_governed_inputs(self):
        identity = {
            "project_id": "project", "scope_id": "scope", "statement_id": "statement",
            "effective_date": "2026-10-22", "category": "INDICATED_INFERRED_TOTAL",
            "source_sha256": "f" * 64, "temporal_decision_id": "d" * 64,
        }
        ids = [token * 64 for token in "123"]
        columns = ("礦石蘊藏量_Mt", "TREO品位_pct", "mreo_ppm")
        formulas = registered_oxide_formula(FORMULA["id"])
        inputs = {
            ("project", obs): {
                **identity, "column_id": column, "material_basis": "WHOLE_ORE",
                "chemical_basis": "PrNdDyTb_REO" if column == "mreo_ppm" else "TREO",
            } for obs, column in zip(ids, columns, strict=True)
        }
        entry = {
            **identity, "column_id": REPORTED_MREO_COLUMN, "evidence_state": "FORMAL_DERIVED",
            "input_observation_ids": ids, "formula_id": FORMULA["id"], "formula": FORMULA["expression"],
            "formula_sha256": formulas["sha256"], "formula_registry_sha256": OXIDE_FORMULA_REGISTRY_SHA256,
            "assessment_id": "a" * 64, "derived_value_id": "b" * 64,
        }
        _check_derived_references([entry], inputs)
        for mutations in ({"chemical_basis": "Dy2O3"}, {"scope_id": "other"}, {"source_sha256": "e" * 64}):
            with self.subTest(mutations=mutations):
                revised = deepcopy(inputs)
                revised["project", ids[-1]].update(mutations)
                with self.assertRaises(WebPublicationError):
                    _check_derived_references([entry], revised)
        with self.assertRaises(WebPublicationError):
            _check_derived_references([{**entry, "input_observation_ids": ids[:2]}], inputs)


@unittest.skipUnless(os.environ.get("AU_REE_MREO_PDF_ASSETS") and shutil.which("pdftotext"),
                     "exact Wudinna original and Poppler required")
class OriginalMreoPdfTests(unittest.TestCase):
    def test_exact_wudinna_original_has_reported_group_and_dry_basis(self):
        source = REVIEW["source_sha256"]
        assets = Path(os.environ["AU_REE_MREO_PDF_ASSETS"])
        original = next(path for path in (assets / source, assets / "objects" / source) if path.is_file()).read_bytes()
        self.assertEqual(sha256(original).hexdigest(), source)
        full_context = DocumentCache().pdf(original, source, REVIEW["dry_basis"]["physical_page"])
        self.assertTrue(verify_reviewed_mreo_document(full_context))
        self.assertFalse(verify_reviewed_mreo_document(replace(full_context, pages=full_context.pages[:3])))
        self.assertFalse(verify_reviewed_mreo_document(replace(full_context, source_sha256="f" * 64)))


@unittest.skipUnless(os.environ.get("AU_REE_STAGE3_OXIDE_BASELINE"), "exact governed Stage 3 baseline SQLite required")
class OriginalMreoStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        baseline = Path(os.environ["AU_REE_STAGE3_OXIDE_BASELINE"])
        db = Path(temporary) / "policy.sqlite"
        shutil.copy2(baseline / "observations/research.sqlite", db)
        extraction = json.loads((baseline / "observations/extraction.json").read_bytes())
        self.store = self.enterContext(Store(db, trusted_temporal_evidence_ids=extraction["trusted_temporal_evidence_ids"]))
        self.old = self.store.get(ResearchRelease, extraction["release_id"])
        self.release = replace(self.old, release_id=digest([self.old.release_id,OXIDE_FORMULA_REGISTRY_SHA256]),
                               result_fingerprints={**self.old.result_fingerprints,OXIDE_RELEASE_PIN:OXIDE_FORMULA_REGISTRY_SHA256})
        prior = checked_research_inputs(self.store, self.old, purpose="CENSUS")
        with self.store.transaction():
            self.store.add(self.release)
            for item in prior:
                selected = item.accepted
                generated = digest(["fresh-resource-selection-v1",self.release.release_id, selected.validation_id])
                self.store.add(replace(selected, release_id=self.release.release_id, acceptance_id=generated))
        self.inputs = checked_research_inputs(self.store, self.release, purpose="CENSUS")

    def test_exact_original_group_and_preserved_historical_release(self):
        with self.store.transaction():
            self.assertEqual(persist_census_oxide(self.store, self.old), ())
            approved, = persist_census_oxide(self.store, self.release)
        self.assertEqual(checked_census_oxide_derivations(self.store,self.old), ())
        self.assertEqual(checked_census_oxide_derivations(self.store,self.release), (approved,))
        self.assertEqual((approved.variable,approved.value), (OXIDE_VARIABLE,"16.8"))
        self.assertEqual(approved.assessment["rounding_interval"], ["16.66925","16.93125"])
        self.assertTrue(verify_oxide_derivation_assessment(approved.assessment))
        with self.store.transaction():
            self.assertEqual(persist_census_oxide(self.store, self.release), (approved,))
        self.assertEqual(self.store.integrity(), {"integrity_check":["ok"],"foreign_key_check":[]})

    def test_mass_does_not_require_r1_inventory_or_the_mcda_policy(self):
        restricted = tuple(item for item in self.inputs if item.raw.variable != "contained_treo_kt")
        with patch("au_ree_v3.fresh_decision.load_policy", side_effect=AssertionError("MCDA policy accessed")):
            assessment = assess_reported_mreo(restricted, self.release)
        self.assertEqual((assessment["status"], assessment["value"]), ("APPROVED", "16.8"))

    def test_dimensional_scope_and_definitions_fail_closed(self):
        position = next(i for i,x in enumerate(self.inputs) if x.raw.variable=="mreo_ppm")
        item = self.inputs[position]
        payload = json.loads(item.raw.original_payload)
        cases = {key:replace(item, raw=replace(item.raw, **edits)) for key,edits in {
            "same report different SHA":{"source_sha":"f"*64}, "different date":{"effective_date":"2025-10-22"},
            "other statement":{"statement_id":"different"}, "concentrate":{"material_basis":"CONCENTRATE"},
            "heavy mineral denominator":{"denominator_basis":"HEAVY_MINERAL"},
            "aggregate treated as element":{"chemical_basis":"Dy2O3"},
            "ore reserve":{"category":"PROBABLE_ORE_RESERVE"},
        }.items()}
        for name, edits in {
            "other cutoff":{"cutoff_analyte":"TREO"}, "missing member":{"oxide_species":["Pr6O11","Nd2O3","Tb4O7"]},
            "incorrect image":{"reviewed_companion_image":{**payload["reviewed_companion_image"],"image_sha256":"f"*64}},
        }.items():
            raw = replace(item.raw, original_payload={**payload, **edits})
            cases[name] = replace(item, raw=raw)
        for key, variant in cases.items():
            with self.subTest(key=key):
                revised = (*self.inputs[:position], variant, *self.inputs[position+1:])
                self.assertNotEqual(assess_reported_mreo(revised,self.release)["status"], "APPROVED")
        self.assertNotEqual(assess_reported_mreo((*self.inputs[:position],*self.inputs[position+1:]),self.release)["status"], "APPROVED")

    def test_signed_rewrite_and_sqlite_write_are_refused(self):
        original_count = len(self.store.records(DerivedValue))
        with self.assertRaisesRegex(ContractViolation, "lacks the exact"), self.store.transaction():
            approved, = persist_census_oxide(self.store, self.release)
            self.store.add(replace(approved, derived_id="forged", value="112"))
        self.assertEqual(len(self.store.records(DerivedValue)), original_count)
        self.assertEqual(checked_census_oxide_derivations(self.store,self.release), ())
        for key,edits in {"incomplete chemical set":{"oxide_species":["Dy2O3","Nd2O3"]},
                          "different source":{"source_sha256":"f"*64},"altered formula":{"factor":"0.002"},
                          "different value":{"value":"17"}}.items():
            with self.subTest(key=key):
                payload = deepcopy(approved.assessment)
                del payload["assessment_id"]
                payload.update(edits)
                payload["assessment_id"] = digest(payload)
                self.assertFalse(verify_oxide_derivation_assessment(payload))
        payload = deepcopy(approved.assessment)
        del payload["assessment_id"]
        payload["group_evidence"]["reported_unit_evidence"]["source_sha256"] = "f"*64
        payload["assessment_id"] = digest(payload)
        self.assertFalse(verify_oxide_derivation_assessment(payload))
