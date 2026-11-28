"""Replay exact forecast and official location evidence under the display-only contract."""

from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from zipfile import ZipFile

from au_ree_v3.census_display import display_csv, display_document, load_display_columns, verify_display_document
from au_ree_v3.forecast import forecast_display_facts
from au_ree_v3.official_geography import official_geography_evidence
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.sources import HttpResult, IntegrityError, SCHEMA as SOURCE_SCHEMA

ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config/research_profiles"
PROFILE = PROFILES / "au_ree_2026-11-28.json"
FIXTURES = ROOT / "tests/fixtures"


@unittest.skipUnless(all(os.environ.get(key) for key in ("AU_REE_CONTEXT_FIXED_MANIFEST", "AU_REE_CONTEXT_FIXED_CAS")),
                     "fixed exact source manifest and CAS required")
class CensusContextDisplayTest(unittest.TestCase):
    def test_issuer_rates_and_government_proofs_join_without_authorizing_site_values(self):
        sources, assets = map(Path, (os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"], os.environ["AU_REE_CONTEXT_FIXED_CAS"]))
        forecasts = forecast_display_facts(PROFILE, sources, assets, PROFILES / "production_forecast_bindings_v1.json",
                                           oracle_path=FIXTURES / "production_forecast_context_oracle_v1.json")
        with tempfile.TemporaryDirectory() as folder, ZipFile(FIXTURES / "government_point_expansion_v1.zip") as package:
            expanded = Path(folder)
            package.extractall(expanded)
            reviews = official_geography_evidence(PROFILES / "official_geography_bindings_v1.json", sources, assets,
                                                  expanded / "source_manifest.json", expanded)
        fields = load_display_columns(PROFILES / "census_display_columns_v2.json")
        projects = tuple(row.project_id for row in load_registry(load_profile(PROFILE).registry_path).projects)
        release_id = "a" * 64
        display = display_document(release_id=release_id, columns=fields, project_ids=projects, facts=forecasts,
                                   review_only=reviews)
        verification = verify_display_document(display, display_csv(display), release_id=release_id, project_ids=projects)
        self.assertEqual(verification, display)
        rates = [fact for fact in forecasts if fact.field_id == "forecast_annual_rate_as_reported"]
        self.assertEqual([(fact.raw_token, fact.raw_unit, fact.data_role) for fact in rates], [
            ("8,300", "tonnes per annum", "FORECAST"), ("9,300", "tonnes per annum", "ENGINEERING_PLAN"),
            ("29,000", "wet tonnes per annum", "FORECAST"),
        ])
        self.assertTrue(all(fact.raw_token in fact.quote for fact in forecasts))
        self.assertEqual(len(forecasts), 9)
        self.assertEqual(len({(fact.project_id, fact.field_id) for fact in forecasts}), 6)
        self.assertNotEqual(rates[0].subject_id, rates[1].subject_id)
        self.assertEqual(len(display["review_only"]), 8)
        self.assertTrue(all(item["publication_date"] == "" and item["state"] == "SHADOW"
                            and item["context"]["feature_effective_date"] == "UNKNOWN_CONTEXT"
                            and item["context"]["capture_date"] == "2026-11-28"
                            and "coordinates" not in item["context"] for item in reviews))
        self.assertEqual({item["context"]["native_role"] for item in reviews
                          if "cross_authority_comparison" in item["context"]}, {"mineral deposit", "Mine"})

    def test_unregistered_period_oracle_cannot_supply_display_context(self):
        sources, assets = map(Path, (os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"], os.environ["AU_REE_CONTEXT_FIXED_CAS"]))
        with tempfile.TemporaryDirectory() as folder:
            oracle = Path(folder) / "wrong.json"
            original = json.loads((FIXTURES / "production_forecast_context_oracle_v1.json").read_bytes())
            original["cases"][0]["period"] = "CURRENT_ANNUAL_PRODUCTION"
            oracle.write_text(json.dumps(original))
            with self.assertRaisesRegex(ValueError, "registered independent native context oracle"):
                forecast_display_facts(PROFILE, sources, assets, PROFILES / "production_forecast_bindings_v1.json",
                                       oracle_path=oracle)

    def test_ndpr_issuer_study_rates_preserve_product_period_and_prior_display(self):
        sources, assets = map(Path, (os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"], os.environ["AU_REE_CONTEXT_FIXED_CAS"]))
        old = forecast_display_facts(PROFILE, sources, assets, PROFILES / "production_forecast_bindings_v1.json",
                                     oracle_path=FIXTURES / "production_forecast_context_oracle_v1.json")
        expanded = forecast_display_facts(PROFILE, sources, assets, PROFILES / "production_forecast_bindings_v2.json",
                                          oracle_path=FIXTURES / "production_forecast_context_oracle_v2.json")
        self.assertEqual(expanded[:len(old)], old)
        new = expanded[len(old):]
        rates = [fact for fact in new if fact.field_id == "forecast_annual_rate_as_reported"]
        self.assertEqual([(fact.raw_token, fact.raw_unit, fact.context["period"]) for fact in rates], [
            ("4,357", "tonnes per annum", "ANNUAL_AVERAGE_AFTER_THREE_YEAR_RAMP_UP_EXCLUDING_PARTIAL_FINAL_YEAR"),
            ("1,157", "tpa", "ANNUAL_TARGET_YEARS_3_15"),
            ("1,037", "tpa", "LIFE_OF_MINE_ANNUAL_TARGET"),
        ])
        self.assertEqual(len({fact.subject_id for fact in rates}), 3)
        self.assertEqual(len({(fact.project_id, fact.field_id) for fact in new}), 6)
        self.assertTrue(all(fact.data_role == "FORECAST" and fact.context["material_basis"] == "NDPR_OXIDE"
                            and fact.raw_token in fact.quote for fact in new))
        dubbo_product = [fact for fact in new if fact.field_id == "forecast_product_and_basis"
                         and fact.project_id == "AU-2cfeb25aefee0c4f95fb"]
        self.assertTrue(all("PDF page 17" in fact.locator and "1,157 1,037" in fact.quote for fact in dubbo_product))

    def test_original_rate_cannot_authorize_a_different_study_title(self):
        sources, assets = map(Path, (os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"], os.environ["AU_REE_CONTEXT_FIXED_CAS"]))
        with tempfile.TemporaryDirectory() as folder:
            bindings = Path(folder) / "wrong.json"
            altered = json.loads((PROFILES / "production_forecast_bindings_v2.json").read_bytes())
            row = next(row for row in altered["cases"] if row["case_id"] == "nolans-dfs-average-ndpr-oxide")
            row["study"] = "Nolans Bankable Feasibility Study (2025)"
            bindings.write_text(json.dumps(altered))
            with self.assertRaisesRegex(ValueError, "Original forecast project, study, scenario or edition disagrees"):
                forecast_display_facts(PROFILE, sources, assets, bindings,
                                       oracle_path=FIXTURES / "production_forecast_context_oracle_v2.json")


class EditionRecoveryTest(unittest.TestCase):
    def test_exact_bytes_are_required_and_tampered_capture_cannot_replay(self):
        functions = runpy.run_path(str(ROOT / "tools/recover_source_editions.py"))
        captured = {"exact": b"%PDF-1.4\nexact", "different": b"%PDF-1.4\ndistinct", "late": b"%PDF-1.4\nlate"}
        project = load_registry(load_profile(PROFILE).registry_path).projects[0].project_id
        base_url = "https://example.edu/"
        now = {"exact": "2026-11-28T13:00:00+00:00", "different": "2026-11-28T13:00:00+00:00",
               "late": "2026-11-28T17:00:00+00:00"}

        def fetcher(url, method, timeout, max_bytes):
            key = url.removeprefix(base_url)
            return HttpResult("FETCHED", url, now[key], 200, "application/pdf", captured[key])

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = load_profile(PROFILE)
            fixed = {"schema": SOURCE_SCHEMA, "profile_id": profile.profile_id, "cutoff": "2026-11-28",
                     "timezone": "Asia/Taipei", "projects": [{"project_id": project}],
                     "editions": [{"sha256": f"{index:064x}"} for index in range(144)]}
            fixed_manifest = root / "fixed.json"
            fixed_manifest.write_text(json.dumps(fixed))
            bindings = root / "bindings.json"
            cases = [{"case_id": key, "project_id": project, "kind": "pdf",
                      "expected_sha256": sha256((b"historical" if key == "different" else captured[key])).hexdigest(),
                      "routes": [{"url": base_url + key, "publisher": "Public research document"}]}
                     for key in captured]
            bindings.write_text(json.dumps({"schema": functions["SCHEMA"], "cases": cases,
                                            "fixed_manifest_sha256": sha256(fixed_manifest.read_bytes()).hexdigest()}))
            paths = dict(profile_path=PROFILE, fixed_manifest=fixed_manifest, bindings=bindings,
                         store=root / "cas", out=root / "new")
            result = functions["recover_public_editions"](**paths, fetcher=fetcher)
            self.assertEqual([row["result"] for row in result["cases"]],
                             ["RECOVERED_EXACT", "SOURCE_UNAVAILABLE", "SOURCE_AFTER_CUTOFF"])
            self.assertEqual(result["cases"][1]["attempts"][0]["disposition"], "DISTINCT_EDITION_ONLY")
            self.assertEqual(functions["verify_recovered_editions"](**paths)["exact_requested_editions_recovered"], 1)
            (paths["store"] / "objects" / cases[0]["expected_sha256"]).write_bytes(b"changed")
            with self.assertRaises(IntegrityError):
                functions["verify_recovered_editions"](**paths)

    def test_new_versions_preserve_native_identity_and_never_count_as_historical_recovery(self):
        functions = runpy.run_path(str(ROOT / "tools/recover_source_editions.py"))
        body = b"%PDF-1.4\nnew public statement"
        url = "https://example.edu/statement.pdf"
        profile = load_profile(PROFILE)
        project = load_registry(profile.registry_path).projects[0].project_id
        case = {"case_id": "native-assay", "project_id": project, "kind": "pdf", "native_namespace": "PUBLISHER_REPORT",
                "native_id": "NATIVE-1", "access_basis": "PUBLIC_ANONYMOUS",
                "routes": [{"url": url, "publisher": "Public Scientific Publisher"}]}
        fixed = {"schema": SOURCE_SCHEMA, "profile_id": profile.profile_id, "cutoff": "2026-11-28",
                 "timezone": "Asia/Taipei", "projects": [{"project_id": project}],
                 "editions": [{"sha256": f"{index:064x}"} for index in range(144)]}

        def fetcher(locator, method, timeout, max_bytes):
            self.assertEqual((locator, method), (url, "GET"))
            return HttpResult("FETCHED", url, "2026-11-28T13:00:00+00:00", 200, "application/pdf", body)

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            fixed_manifest, bindings = root / "fixed.json", root / "bindings.json"
            fixed_manifest.write_text(json.dumps(fixed))
            frozen_sha = sha256(fixed_manifest.read_bytes()).hexdigest()
            bindings.write_text(json.dumps({"schema": functions["VERSION_SCHEMA"], "cases": [case],
                                            "fixed_manifest_sha256": frozen_sha}))
            paths = dict(profile_path=PROFILE, fixed_manifest=fixed_manifest, bindings=bindings,
                         store=root / "cas", out=root / "new")
            acquired = functions["recover_public_editions"](**paths, fetcher=fetcher)
            result = functions["verify_recovered_editions"](**paths)
            self.assertEqual(json.loads(json.dumps(acquired)), result)
            self.assertEqual(result["exact_requested_editions_recovered"], 0)
            self.assertEqual(result["cases"][0]["result"], "NEW_PUBLIC_EDITION")
            self.assertEqual(sha256(fixed_manifest.read_bytes()).hexdigest(), frozen_sha)
            expansion = json.loads((paths["out"] / "source_manifest.json").read_bytes())
            native, = expansion["native_route_bindings"]
            self.assertEqual((native["native_namespace"], native["native_id"], native["scope_status"]),
                             ("PUBLISHER_REPORT", "NATIVE-1", "PROJECT_AND_STATEMENT_REVIEW_REQUIRED"))
            self.assertEqual(native["source_sha256"], sha256(body).hexdigest())

    def test_historical_gap_input_requires_the_exact_prior_diagnostic(self):
        diagnostic = runpy.run_path(str(ROOT / "tools/census_v2_coverage_diagnostic.py"))
        classify = diagnostic["_historical_gap_type"]
        self.assertEqual(classify("總TREO蘊藏量_kt", "SOURCE_REPORTED"), "resource")
        self.assertEqual(classify("總TREO蘊藏量_kt", "ORE_MT_X_GRADE_PERCENT_X_10"), "resource_formula")
        self.assertEqual(classify("Dy2O3_氧化鏑_kt", "SOURCE_REPORTED"), "composition")
        self.assertEqual(classify("經度", "REVIEWED_OFFICIAL_PROJECT_FEATURE"), "location")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            altered = root / "altered.csv"
            altered.write_text("project_id,column\nfiction,經度\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact 1,396-slot diagnostic"):
                diagnostic["historical_source_gaps"](root, altered, root / "missing-manifest", root / "out")
