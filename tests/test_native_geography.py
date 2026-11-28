from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import shutil
import tempfile
import unittest
from zipfile import ZipFile

from au_ree_v3.documents import DocumentCache, DocumentError
from au_ree_v3.native_geography import _nt_modat, _sarig_mindep, review_native_locations
from au_ree_v3.official_geography import replay_historical_points
from au_ree_v3.sources import ContentStore, HttpResult, IntegrityError


def _sarig_page(*, longitude="140.1", latitude="-36.5", role="Deposit"):
    raw = ("Deposit Number:", "123", "Deposit Name:", "Example", "Deposit Type:", role, "Status:", "Exploration",
           "Datum", "GDA2020", "Coordinate Source", "Imagery", "MGA Zone", "54", "MGA Easting", "410000",
           "MGA Northing", "5950000", "Long Degrees Decimal", longitude, "Lat Degrees Decimal", latitude,
           "Horizontal Accuracy", "20")
    return "".join(f"<div>{part}</div>" for part in raw).encode()


def _read_sarig(raw, identity="123"):
    return _sarig_mindep(raw, DocumentCache(), {"sha256": sha256(raw).hexdigest()}, identity)


class NativePointAdaptersTest(unittest.TestCase):
    def test_read_sarig_native_axes_and_distinct_raw_accuracy(self):
        point = _read_sarig(_sarig_page(role="Prospect"))
        self.assertEqual(point["location_role"], "PROSPECT")
        self.assertEqual(point["geometry"]["coordinates"], [140.1, -36.5])
        self.assertEqual([axis["crs"] for axis in point["axes"]], ["EPSG:7844", "EPSG:7854"])
        self.assertEqual(point["positional_accuracy"], {"raw": "20", "unit": None, "method": "Imagery"})
        self.assertIn("HTML fragment", point["native_fields"]["accuracy"]["locator"])

    def test_sarig_rejects_wrong_native_identity_and_swapped_axes(self):
        with self.assertRaisesRegex(IntegrityError, "different native deposit"):
            _read_sarig(_sarig_page(), identity="124")
        with self.assertRaisesRegex(IntegrityError, "axes"):
            _read_sarig(_sarig_page(longitude="-36.5", latitude="140.1"))

    def test_sarig_rejects_ambiguous_labels(self):
        with self.assertRaisesRegex(DocumentError, "accuracy_ABSENT_OR_AMBIGUOUS"):
            _read_sarig(_sarig_page() + b"<div>Horizontal Accuracy</div><div>88</div>")

    def test_modat_retains_projected_axes_without_inventing_datum_or_accuracy(self):
        payload = (b"MODAT_ID,NAME,STATUS,COM_MAJOR,EASTING,NORTHING,ZONE_MGA,LOC_METHOD,LOC_ACCURY\n"
                   b"2641,Nolans,Deposit,REE,319048,7501671,53,Unknown,100 to 200\n")
        package = BytesIO()
        with ZipFile(package, "w") as archive:
            archive.writestr("MINERALOCCURRENCES.csv", payload)
        point = _nt_modat(package.getvalue(), DocumentCache(), {}, "2641")
        self.assertEqual(point["native_locator"]["member_sha256"], sha256(payload).hexdigest())
        self.assertEqual(point["location_role"], "OFFICIAL_DEPOSIT_POINT")
        self.assertEqual(point["native_fields"]["zone"]["text"], "53")
        self.assertEqual(point["positional_accuracy"]["raw"], "100 to 200")
        self.assertIsNone(point["crs"])
        self.assertIsNone(point["geometry"])


class HistoricalPointRecoveryTest(unittest.TestCase):
    def test_native_layer_and_printed_precision_replay_with_verified_raw_capture(self):
        url = ("https://services.ga.gov.au/gis/rest/services/"
               "AustralianCriticalMineralsOperatingMinesAndDeposits/MapServer/5/query")
        digest = "a" * 64
        rows = ("project_id,dataset,native_feature_id,source_sha256,locator,export_crs,longitude,latitude\n"
                f"project-a,GA2025,2275,{digest},objectid=2275,GDA94,142.7815,-36.4994\n").encode()
        legacy_assets = json.dumps([{"sha256": digest, "url": url + "?where=legacy", "role": "snapshot"}]).encode()
        pins = {"asset_manifest.json": ("", sha256(legacy_assets).hexdigest())}
        response = json.dumps({
            "spatialReference": {"wkid": 4283}, "geometryType": "esriGeometryPoint",
            "features": [{"attributes": {"objectid": 2275, "status": "Prospect"},
                          "geometry": {"x": 142.78151, "y": -36.49942}}],
        }).encode()

        def fetch(request, method, *_):
            self.assertEqual(urlsplit(request).path, urlsplit(url).path)
            self.assertEqual(method, "GET")
            params = parse_qs(urlsplit(request).query)
            self.assertEqual(params["where"], ["objectid IN (2275)"])
            self.assertEqual(params["outSR"], ["4283"])
            return HttpResult("FETCHED", request, "2026-11-29T00:10:00+00:00", 200, "application/json", response)

        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "store"
            v2 = Path(directory) / "readonly"
            with (
                patch("au_ree_v3.migration.baseline.PinnedRepository.read", side_effect=lambda _, name:
                      rows if name.endswith("spatial_point_quality.csv") else legacy_assets),
                patch("au_ree_v3.migration.project_registry.V2_ARCHIVE_FILES", pins),
                patch("au_ree_v3.official_geography._HISTORICAL_POINTS_SHA", sha256(rows).hexdigest()),
            ):
                live = replay_historical_points(v2, store, fetcher=fetch)
                point, = live["points"]
                capture, = live["captures"]
                self.assertEqual(live["status"], "PASS")
                self.assertEqual(live["counts"]["formal_coordinates"], 0)
                self.assertEqual(point["location_role"], "PROSPECT")
                self.assertEqual(point["state"], "CANDIDATE")
                self.assertEqual(capture["dataset"], "GA_CRITICAL_LAYER_5")
                self.assertEqual(capture["historical_sha256"], digest)
                self.assertEqual(capture["source_sha256"], sha256(response).hexdigest())
                self.assertEqual(capture["temporal_decision"]["status"], "CANDIDATE")

                def no_network(*_):
                    self.fail("Offline capture replay attempted network access")

                self.assertEqual(live, replay_historical_points(v2, store, prior=live, fetcher=no_network))
                ContentStore(store).path(capture["source_sha256"]).write_bytes(b"a different source edition")
                with self.assertRaisesRegex(IntegrityError, "SHA-256 mismatch"):
                    replay_historical_points(v2, store, prior=live, fetcher=no_network)


_VARS = ("AU_REE_GEO_BASE_MANIFEST", "AU_REE_GEO_BASE_ASSETS", "AU_REE_GEO_PROBE_MANIFEST", "AU_REE_GEO_PROBE_ASSETS")


@unittest.skipUnless(all(os.environ.get(key) for key in _VARS), "Exact state government source editions unavailable")
class ExactNativeGeographyTest(unittest.TestCase):
    def test_originals_scope_date_and_cutoff_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            binding = Path("config/research_profiles/native_geography_bindings_v1.json")
            paths = [Path(os.environ[key]) for key in _VARS]
            receipt = review_native_locations(binding, *paths, target / "first.json")
            review_native_locations(binding, *paths, target / "second.json")
            self.assertEqual((target / "first.json").read_bytes(), (target / "second.json").read_bytes())
            rows = {r["case_id"]: r for r in receipt["cases"]}
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["project_count"], 93)
            self.assertEqual(receipt["formal_coordinates_added"], 0)
            self.assertEqual(receipt["formal_infrastructure_indicators_added"], 0)
            self.assertTrue(all(r["issuer_context"]["temporal_decision"]["status"] == "ELIGIBLE" for r in rows.values()))
            self.assertTrue(all(r["document_temporality"]["reason"] == "PRE_CUTOFF_PUBLIC_EVIDENCE_MISSING" for r in rows.values()))
            self.assertTrue(all(r["feature_effective_date"] is None for r in rows.values()))
            self.assertEqual(rows["koppamurra-mindep-11871"]["geometry"]["coordinates"], [140.9244556, -37.1999813])
            self.assertEqual(rows["franklyn-mindep-10098"]["compatibility"]["status"], "NOT_COMPARABLE")
            self.assertIsNone(rows["nolans-modat-2641"]["datum"])
            self.assertIsNone(rows["dubbo-toongi-locality"]["geometry"])
            changed = json.loads(paths[2].read_bytes())
            changed["cutoff"] = "2026-11-29"
            different = target / "modified.json"
            different.write_text(json.dumps(changed))
            with self.assertRaisesRegex(IntegrityError, "exact separate"):
                review_native_locations(binding, *paths[:2], different, paths[3], target / "should-not-exist.json")

    def test_wrong_original_hash_isolated_from_other_government_sources(self):
        binding = Path("config/research_profiles/native_geography_bindings_v1.json")
        paths = [Path(os.environ[key]) for key in _VARS]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            copied = target / "sources"
            shutil.copytree(paths[3], copied)
            corrupt = copied / "objects" / "e4dab91a51b9e97c8a0d7b51c4268f04c86934f159f97be536472b91c936ceca"
            corrupt.write_bytes(corrupt.read_bytes() + b"\n")
            receipt = review_native_locations(binding, *paths[:3], copied, target / "result.json")
            self.assertEqual(receipt["status"], "FAIL")
            rows = {r["case_id"]: r["status"] for r in receipt["cases"]}
            self.assertEqual(rows.pop("koppamurra-mindep-11871"), "FAIL")
            self.assertEqual(set(rows.values()), {"REVIEWED_CANDIDATE"})
