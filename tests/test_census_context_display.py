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
