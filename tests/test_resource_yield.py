"""Verify added source-native layouts against manually transcribed cells."""

from pathlib import Path
import json
import os
import shutil
import tempfile
import unittest

from au_ree_v3.contracts import ResearchRelease, canonical_json
from au_ree_v3.fresh_decision import build_decision
from au_ree_v3.fresh_observations import extract_sources
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"
YIELD = FIXTURES / "resource_yield_v1"
BINDINGS = ROOT / "config/research_profiles/resource_bindings_v2.json"
KEYS = ("variable", "raw_value", "raw_unit", "value", "unit", "page")


@unittest.skipUnless(os.environ.get("AU_REE_RESOURCE_YIELD_ASSETS") and shutil.which("pdftotext"),
                     "locked resource-yield issuer originals and Poppler required")
class ResourceYieldIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cas = ContentStore(self.directory / "cas")
        asset_root = Path(os.environ["AU_REE_RESOURCE_YIELD_ASSETS"])
        self.acquisition = json.loads((YIELD / "acquisition_manifest.json").read_bytes())
        for folder in (FIXTURES / "fresh_resource_v1/captures", YIELD / "captures"):
            for item in folder.glob("*.json"):
                self.assertEqual(self.cas.put(item.read_bytes()), item.stem)
        for edition in self.acquisition["editions"]:
            sha = edition["sha256"]
            paths = (asset_root / folder / sha for folder in ("objects", "assets", ""))
            path = next((candidate for candidate in paths if candidate.is_file()), None)
            self.assertIsNotNone(path, f"Missing locked real source {sha}")
            self.assertEqual(self.cas.put(path.read_bytes()), sha)

    def run_extraction(self, name, acquisition=None, *, bindings=BINDINGS):
        home = self.directory / name
        home.mkdir()
        manifest = home / "source_manifest.json"
        manifest.write_text(canonical_json(acquisition or self.acquisition), encoding="utf-8")
        database = home / "observations.sqlite"
        outcome = extract_sources(bindings, manifest, self.cas.root, database, home, code_commit="d" * 40)
        observations = [json.loads(line) for line in (home / "observations.jsonl").read_bytes().splitlines()]
        return outcome, observations, database

    def test_independent_original_checks_cover_both_new_layouts_and_refusals(self):
        result, observations, database = self.run_extraction("first")
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["selected_count"], 16)
        expected = json.loads((YIELD / "expected.json").read_bytes())
        cases = {case["case_id"]: case for case in result["cases"]}
        with Store(database, trusted_temporal_evidence_ids=result["trusted_temporal_evidence_ids"]) as store:
            release = store.get(ResearchRelease, result["release_id"])
            selected = checked_research_inputs(store, release, purpose="CENSUS")
        for case_id, target in expected["measurements"].items():
            with self.subTest(case=case_id):
                self.assertEqual(cases[case_id]["source_integrity"], "EXACT_SHA")
                self.assertEqual(cases[case_id]["temporal_decision"]["status"], "ELIGIBLE")
                own_source = [row for row in observations if row["source_sha256"] == target["source_sha256"]]
                self.assertEqual({row["effective_date"] for row in own_source}, {target["effective_date"]})
                self.assertEqual({row["category"] for row in own_source}, {target["category"]})
                basis = {(row["cutoff_grade"], row["cutoff_unit"], row["cutoff_analyte"]) for row in own_source}
                self.assertEqual(basis, {tuple(target["cutoff"])})
                current = [[row[key] for key in KEYS] for row in own_source if row["selected_for_census"]]
                self.assertCountEqual(current, target["selected"])
                if "held" in target:
                    self.assertCountEqual([[row[key] for key in KEYS] for row in own_source], target["held"])
                    self.assertEqual({row["outcome"] for row in own_source}, {"EVIDENCE_INSUFFICIENT"})
                    self.assertEqual({row["reason"] for row in own_source}, {target["reason"]})
                else:
                    accepted = [row for row in selected if row.raw.source_sha == target["source_sha256"]]
                    self.assertEqual({row.scope.name for row in accepted}, {target["scope"]})
        component_expectations = expected["wolverine_companion_oxide_values_ppm"]
        components = [row for row in selected if row.raw.variable in component_expectations]
        self.assertEqual({row.raw.variable: row.raw.raw_value for row in components}, component_expectations)
        self.assertEqual({row.anchor.page for row in components}, {6})
        self.assertEqual({row.raw.statement_id for row in components}, {
            row.raw.statement_id for row in selected
            if row.raw.project_id == "AU-9bd8e5ed8408cd84d876" and row.raw.variable == "grade"
        })
        repeat, repeat_rows, _ = self.run_extraction("repeat")
        self.assertEqual(repeat, result)
        self.assertEqual(repeat_rows, observations)

    def test_missing_new_original_does_not_withdraw_independent_selection(self):
        missing = next(edition for edition in self.acquisition["editions"]
                       if edition["source_key"] == "seed-f09f74b43a7e20826fb9eda9")
        self.cas.path(missing["sha256"]).unlink()
        result, observations, _ = self.run_extraction("missing")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["selected_count"], 13)
        self.assertEqual({row["project_id"] for row in observations if row["selected_for_census"]},
                         {"AU-efff91a06cf344122d88", "AU-9bd8e5ed8408cd84d876", "AU-6d807eb7caec9db0f35e"})


    @unittest.skipUnless(shutil.which("pdfimages"), "native PDF image readback required")
    def test_reported_mreo_is_bound_to_the_reviewed_image_and_native_total(self):
        bindings = ROOT / "config/research_profiles/resource_bindings_v3.json"
        result, _, database = self.run_extraction("composition", bindings=bindings)
        self.assertEqual(result["selected_count"], 17)
        with Store(database, trusted_temporal_evidence_ids=result["trusted_temporal_evidence_ids"]) as store:
            release = store.get(ResearchRelease, result["release_id"])
            inputs = checked_research_inputs(store, release, purpose="MCDA")
            decision_out = self.directory / "decision"
            decision = build_decision(ROOT / "config/research_profiles/au_ree_2026-11-28.json", decision_out,
                                      store=store, release_id=release.release_id)
        self.assertEqual(decision["comparable_project_criteria"], 6)
        composition = decision["panels"]["issuer_resource_composition"]
        self.assertEqual((composition["status"], composition["eligible_count"]), ("PASS", 2))
        results = json.loads((decision_out / "mcda_results.json").read_bytes())
        panel = results["panels"]["issuer_resource_composition"]
        for scenario in panel["point_rankings"].values():
            for rankings in scenario.values():
                self.assertEqual(rankings["AU-0098e17a63e7c05c213f"]["rank"], 1)
                self.assertEqual(rankings["AU-9bd8e5ed8408cd84d876"]["rank"], 2)
        wudinna = [item for item in inputs if item.raw.project_id == "AU-0098e17a63e7c05c213f"]
        mreo, = (item for item in wudinna if item.raw.variable == "mreo_ppm")
        grade, = (item for item in wudinna if item.raw.variable == "grade")
        self.assertEqual(mreo.raw.raw_value, "112")
        self.assertEqual(mreo.candidate.unit, "ppm")
        self.assertEqual(mreo.candidate.denominator_basis, "WHOLE_ORE")
        self.assertEqual(mreo.raw.statement_id, grade.raw.statement_id)
        proof = json.loads(mreo.raw.original_payload)
        self.assertEqual(set(proof["oxide_species"]), {"Nd2O3", "Pr6O11", "Dy2O3", "Tb4O7"})
        self.assertEqual(proof["oxide_definition"]["page"], 3)
        self.assertEqual(proof["reviewed_companion_image"]["physical_page"], 15)
        self.assertEqual(proof["reviewed_companion_image"]["nd_pr_token_ppm"], "101")
        self.assertEqual(proof["reviewed_companion_image"]["dy_tb_token_ppm"], "10.5")

        tampered = json.loads(bindings.read_bytes())
        wudinna_binding = next(row for row in tampered["bindings"] if row["case_id"] == "wudinna-maiden-estimate")
        wudinna_binding["oxide_aggregate_review"]["image_sha256"] = "0" * 64
        tampered_path = self.directory / "invalid_review.json"
        tampered_path.write_text(canonical_json(tampered), encoding="utf-8")
        failed, observations, _ = self.run_extraction("invalid-proof", bindings=tampered_path)
        self.assertEqual(failed["selected_count"], 16)
        self.assertNotIn("mreo_ppm", {row["variable"] for row in observations})
        wudinna_case = next(case for case in failed["cases"] if case["case_id"] == "wudinna-maiden-estimate")
        self.assertEqual(wudinna_case["selected_count"], 3)
        self.assertEqual(wudinna_case["composition_extraction"], {
            "outcome": "EVIDENCE_INSUFFICIENT", "reason": "REVIEWED_COMPOSITION_IMAGE_FINGERPRINT_MISMATCH",
        })

    def test_exact_resource_page_does_not_authorize_other_chemical_quantity(self):
        spec = json.loads(BINDINGS.read_bytes())
        goschen = next(row for row in spec["bindings"] if row["case_id"] == "goschen-area-one-bulk-oxides")
        goschen["reported_chemical_label"] = "Nb2O5"
        path = self.directory / "wrong_chemistry.json"
        path.write_text(canonical_json(spec), encoding="utf-8")
        receipt, _, _ = self.run_extraction("wrong-chemistry", bindings=path)
        case = next(row for row in receipt["cases"] if row["case_id"] == goschen["case_id"])
        self.assertEqual(case["reason"], "NON_CANONICAL_TREO_VALUE_COLUMNS")
        self.assertEqual(case["selected_count"], 0)
        self.assertEqual(receipt["selected_count"], 13)
