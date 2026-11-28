"""Check the isolated government geography and native issuer forecast evidence."""

from copy import deepcopy
import json
import os
from pathlib import Path
import runpy
import shutil
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import ContractViolation
from au_ree_v3.forecast import _select_shadows, review_production_forecasts
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.store import Store

import test_research_inputs as actual_inputs


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/research_profiles"


class ForecastRoleTests(unittest.TestCase):
    def test_actual_research_consumers_reject_both_future_roles(self):
        harness = actual_inputs.ResearchInputTemporalTests("test_forecast_is_not_an_actual_history_input")
        for role in ("FORECAST", "ENGINEERING_PLAN"):
            with self.subTest(role=role), Store(":memory:", trusted_temporal_evidence_ids=(harness.proof.evidence_id,)) as store:
                release, decision = harness._seed(store, evidence=(harness.proof,), role=role, effective_date="2027-01")
                self.assertEqual(decision.status, "ELIGIBLE")
                with store.transaction():
                    store.add(actual_inputs.fixtures.ACCEPTED)
                for purpose in ("CENSUS", "MCDA", "PUBLICATION"):
                    with self.subTest(purpose=purpose), self.assertRaisesRegex(ContractViolation, role):
                        checked_research_inputs(store, release, purpose=purpose)

    def test_duplicate_rate_is_deduplicated_and_conflicting_rate_is_quarantined(self):
        original = {"status": "VERIFIED_SHADOW", "raw_observation":
                    {"source_sha": "source", "scope_id": "scope", "raw_value": "8,300", "observation_id": "a"}}
        duplicate = deepcopy(original)
        self.assertEqual(_select_shadows([original, duplicate]), ["a"])
        alternate = deepcopy(original)
        alternate["raw_observation"].update(raw_value="9,300", observation_id="b")
        self.assertEqual(_select_shadows([original, alternate]), [])
        self.assertEqual(original["status"], "SCIENTIFIC_CONFLICT")
        self.assertEqual(alternate["reason"], "SAME_EDITION_SCENARIO_RATES_DISAGREE")


@unittest.skipUnless(
    os.environ.get("AU_REE_CONTEXT_MANIFEST") and os.environ.get("AU_REE_CONTEXT_CAS")
    and shutil.which("ogrinfo") and shutil.which("pdftotext"),
    "fixed exact source corpus and native PDF/GDAL parsers required",
)
class ContextShadowReplayTests(unittest.TestCase):
    def setUp(self):
        self.profile = CONFIG / "au_ree_2026-11-28.json"
        self.sources = Path(os.environ["AU_REE_CONTEXT_MANIFEST"])
        self.assets = Path(os.environ["AU_REE_CONTEXT_CAS"])
        self.forecasts = CONFIG / "production_forecast_bindings_v1.json"

    def test_frozen_study_and_geography_replay_is_identical_and_remains_shadow(self):
        run = runpy.run_path(str(ROOT / "tools/review_context_shadows.py"))["build_context_shadows"]
        with TemporaryDirectory() as folder:
            first, repeat = Path(folder) / "first", Path(folder) / "repeat"
            for path in (first, repeat):
                summary = run(profile=self.profile, sources=self.sources, assets=self.assets,
                              geography_bindings=CONFIG / "wa_tenement_bindings_v1.json",
                              forecast_bindings=self.forecasts, out=path)
                self.assertEqual((summary["geography"]["status"], summary["forecast"]["status"]), ("PASS", "PASS"))
                self.assertEqual((summary["formal_selections"], summary["formal_census_cells"],
                                  summary["formal_mcda_criteria"]), (0, 0, 0))
            for name in ("context_shadow_replay.json", "tenements.json", "government_geography_role_shadow.json",
                         "infrastructure_compatibility.json", "production_forecast_shadow.json"):
                self.assertEqual((first / name).read_bytes(), (repeat / name).read_bytes())
            geo = json.loads((first / "government_geography_role_shadow.json").read_bytes())
            self.assertEqual(geo["counts_by_state"], {"CANDIDATE": 5, "VERIFIED_BOUNDARY": 7})
            self.assertEqual(geo["criteria_reviewed"], 372)
            self.assertEqual(geo["verified_mine_or_deposit_points"], 0)
            self.assertEqual({case["crs"] for case in geo["actual_source_centroid_controls"]}, {"EPSG:4283", "EPSG:7844"})
            self.assertTrue(all(case["assessment"]["status"] == "NOT_COMPARABLE"
                                for case in geo["actual_source_centroid_controls"]))
            values = json.loads((first / "production_forecast_shadow.json").read_bytes())
            self.assertEqual(values["counts_by_state"], {"VERIFIED_SHADOW": 3, "REJECTED": 1})
            self.assertEqual((values["native_document_count"], values["native_pages_decoded"]), (2, 83))
            raw = [case["raw_observation"] for case in values["cases"] if case["status"] == "VERIFIED_SHADOW"]
            self.assertEqual([(item["raw_value"], item["raw_unit"], item["observation_role"]) for item in raw],
                             [("8,300", "tonnes per annum", "FORECAST"),
                              ("9,300", "tonnes per annum", "ENGINEERING_PLAN"),
                              ("29,000", "wet tonnes per annum", "FORECAST")])
            self.assertEqual(len({item["anchor_id"] for item in raw}), 3)
            rejected, = [case for case in values["cases"] if case["status"] == "REJECTED"]
            self.assertEqual(rejected["reason"], "LIFETIME_PRODUCT_MASS_IS_NOT_ANNUAL_CONTAINED_TREO")

    def test_shared_pdf_edition_cannot_transfer_study_to_parent_project(self):
        cases = json.loads(self.forecasts.read_bytes())
        cases["cases"][0]["project_id"] = "AU-47d12ebe6b66df8e5936"
        with TemporaryDirectory() as folder:
            config, receipt = Path(folder) / "bindings.json", Path(folder) / "receipt.json"
            config.write_text(json.dumps(cases))
            report = review_production_forecasts(self.profile, self.sources, self.assets, config, receipt)
        shared, nominal, _, north = report["cases"]
        self.assertEqual((shared["status"], shared["reason"]),
                         ("SCIENTIFIC_CONFLICT", "NATIVE_STUDY_PROJECT_IDENTITY_MISMATCH"))
        self.assertEqual(nominal["status"], "VERIFIED_SHADOW")
        self.assertEqual(north["status"], "VERIFIED_SHADOW")
        self.assertEqual(len(report["shadow_selected_observation_ids"]), 2)
        self.assertEqual(report["formal_observations"], 0)
