"""Verify separate official point data and the fixed issuer production oracle."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import shutil
import tempfile
import unittest
from zipfile import ZipFile
from zoneinfo import ZoneInfo

from au_ree_v3.forecast import review_production_forecasts
from au_ree_v3.official_geography import _point_index, review_official_point_candidates
from au_ree_v3.sources import ContentStore, IntegrityError, validate_edition_proofs


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config/research_profiles"
ORACLE = ROOT / "tests/fixtures/production_forecast_context_oracle_v1.json"
GOVERNMENT_FIXTURE = ROOT / "tests/fixtures/government_point_expansion_v1.zip"


class NativePointSchemaTest(unittest.TestCase):
    def test_point_role_is_never_inferred_from_an_invalid_crs_or_geometry(self):
        fixture = {"type": "FeatureCollection", "crs": {"properties": {"name": "urn:ogc:def:crs:EPSG::4283"}},
                   "numberMatched": 1, "features": [{"properties": {"ENO": 99},
                                                    "geometry": {"type": "Point", "coordinates": [122, -29]}}]}
        self.assertEqual(set(_point_index(fixture, "GA_OZMIN")), {99})
        for kind in ("projection", "geometry type", "coverage"):
            modified = deepcopy(fixture)
            if kind == "projection":
                modified["crs"]["properties"]["name"] = "arbitrary:4283"
            elif kind == "geometry type":
                modified["features"][0]["geometry"]["type"] = "MultiPoint"
            else:
                modified["numberMatched"] = 2
            with self.subTest(kind=kind), self.assertRaises(IntegrityError):
                _point_index(modified, "GA_OZMIN")


    def test_actual_government_responses_and_captures_replay_offline(self):
        with tempfile.TemporaryDirectory() as folder, ZipFile(GOVERNMENT_FIXTURE) as archive:
            root = Path(folder)
            archive.extractall(root)
            manifest_bytes = (root / "source_manifest.json").read_bytes()
            expected = json.loads((PROFILES / "official_geography_bindings_v1.json").read_bytes())
            self.assertEqual(sha256(manifest_bytes).hexdigest(), expected["expanded_manifest_sha256"])
            manifest = json.loads(manifest_bytes)
            store = ContentStore(root)
            for edition in manifest["editions"]:
                self.assertTrue(validate_edition_proofs(store, edition, ZoneInfo(manifest["timezone"])))
            ozmin, = (edition for edition in manifest["editions"] if edition["source_key"] == "ga-ozmin-mt-weld")
            points = _point_index(json.loads(store.read(ozmin["sha256"])), "GA_OZMIN")
        attributes, geometry = points[763335]
        self.assertEqual((attributes["OTHER_ID"], attributes["ACCURACY_M"], attributes["CLASS"], geometry["type"]),
                         ("S0237005", 5, "mineral deposit", "Point"))


@unittest.skipUnless(all(os.environ.get(key) for key in (
    "AU_REE_CONTEXT_FIXED_MANIFEST", "AU_REE_CONTEXT_FIXED_CAS", "AU_REE_CONTEXT_EXPANDED_MANIFEST",
    "AU_REE_CONTEXT_EXPANDED_CAS",
)), "fixed and separately expanded exact source assets required")
class ExactContextExpansionTest(unittest.TestCase):
    def test_government_shape_role_and_precision_remain_shadow(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = review_official_point_candidates(
                PROFILES / "official_geography_bindings_v1.json",
                Path(os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"]), Path(os.environ["AU_REE_CONTEXT_FIXED_CAS"]),
                Path(os.environ["AU_REE_CONTEXT_EXPANDED_MANIFEST"]), Path(os.environ["AU_REE_CONTEXT_EXPANDED_CAS"]),
                Path(tmp) / "geo.json",
            )
        self.assertEqual(report["status"], "PASS")
        self.assertEqual((report["expanded_unique_sha256"], report["decoded_point_rows"], len(report["cases"])), (7, 91, 11))
        self.assertEqual((report["formal_census"], report["formal_infrastructure"]), (0, 0))
        results = {row["case_id"]: row for row in report["cases"]}
        mine = results["mt-weld-cld-mine"]
        self.assertEqual(mine["location_role"], "OFFICIAL_MINE_POINT")
        self.assertEqual(mine["crs"], "EPSG:7844")
        self.assertEqual(mine["project_link"]["role"], "ISSUER_PROJECT_AND_DISTINCTIVE_SUBSITE_LINK")
        self.assertIsNone(mine["precision"]["positional_accuracy_m"])
        self.assertIsNone(mine["feature_effective_date"])
        self.assertEqual(mine["infrastructure_compatibility"]["status"], "INSUFFICIENT_CONTEXT")
        point = results["mt-weld-resource-group-ga"]
        self.assertEqual((point["location_role"], point["precision"]["positional_accuracy_m"]),
                         ("OFFICIAL_DEPOSIT_POINT", 5))
        self.assertEqual(report["cross_authority_relations"][0]["verdict"], "CROSS_AUTHORITY_ROLE_MISMATCH")
        for name in ("mt-weld-gold-homonym-wa", "mt-weld-road-wa", "jupiter-prospect-wa"):
            with self.subTest(control=name):
                self.assertEqual(results[name]["proposed_site_use"], "REJECTED")
        for name in ("mt-weld-road-wa", "jupiter-prospect-wa"):
            self.assertEqual(results[name]["infrastructure_compatibility"]["status"], "NOT_COMPARABLE")

    def test_tampered_government_body_cannot_supply_a_point(self):
        manifest_path = Path(os.environ["AU_REE_CONTEXT_EXPANDED_MANIFEST"])
        manifest = json.loads(manifest_path.read_bytes())
        point, = [row for row in manifest["editions"] if row["source_key"] == "ga-ozmin-mt-weld"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            replica = folder / "expanded-store"
            shutil.copytree(os.environ["AU_REE_CONTEXT_EXPANDED_CAS"], replica)
            (replica / "objects" / point["sha256"]).write_bytes(b"changed response")
            report = review_official_point_candidates(
                PROFILES / "official_geography_bindings_v1.json",
                Path(os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"]), Path(os.environ["AU_REE_CONTEXT_FIXED_CAS"]),
                manifest_path, replica, folder / "geo.json",
            )
        self.assertEqual(report["status"], "FAIL")
        results = {row["case_id"]: row for row in report["cases"]}
        corrupt = results["mt-weld-resource-group-ga"]
        self.assertEqual(corrupt["state"], "SCIENTIFIC_CONFLICT")
        self.assertNotIn("geometry", corrupt)
        self.assertEqual(results["mt-weld-cld-mine"]["state"], "SHADOW")
        self.assertEqual(report["formal_census"], 0)

    def test_independent_actual_forecast_oracle_checks_annual_material_basis_and_dates(self):
        verify = runpy.run_path(str(ROOT / "tools/verify_context_expansion.py"))["verify_forecast_context"]
        sources, assets = map(Path, (os.environ["AU_REE_CONTEXT_FIXED_MANIFEST"], os.environ["AU_REE_CONTEXT_FIXED_CAS"]))
        with tempfile.TemporaryDirectory() as tmp:
            production = review_production_forecasts(
                PROFILES / "au_ree_2026-11-28.json", sources, assets, PROFILES / "production_forecast_bindings_v1.json",
                Path(tmp) / "production.json",
            )
        receipt = verify(production, sources, assets, ORACLE)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual([(item["amount"], item["role"], item["material"]) for item in receipt["cases"]], [
            ("8,300", "FORECAST", "MREC"), ("9,300", "ENGINEERING_PLAN", "MREC"),
            ("29,000", "FORECAST", "FLOTATION_CONCENTRATE"),
        ])
        production["cases"][0]["raw_observation"]["raw_value"] = "9,300"
        with self.assertRaisesRegex(ValueError, "forecast quantity"):
            verify(production, sources, assets, ORACLE)
