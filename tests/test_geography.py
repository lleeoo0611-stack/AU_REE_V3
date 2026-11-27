import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from zipfile import ZipFile

from au_ree_v3.geography import ROLE, _read_layer, inventory_tenements
from au_ree_v3.sources import IntegrityError


@unittest.skipUnless(shutil.which("ogrinfo") and shutil.which("ogr2ogr"), "GDAL tools unavailable")
class TenementLayerTest(unittest.TestCase):
    def test_exact_native_id_and_crs_preserve_polygon(self):
        geojson = {
            "type": "FeatureCollection", "features": [{
                "type": "Feature", "properties": {
                    "TENID": "E  7005037", "FMT_TENID": "E 70/5037", "TYPE": "EXPLORATION LICENCE",
                    "SURVSTATUS": "UNSURVEYED", "TENSTATUS": "LIVE", "EXTRACT_DA": "2026/09/10",
                    "HOLDER1": "Irrelevant to spatial evidence",
                },
                "geometry": {"type": "Polygon", "coordinates": [[[116, -34], [117, -34],
                                                                  [117, -35], [116, -34]]]},
            }],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            geojson_file, zip_file = root / "fixture.geojson", root / "parcel.zip"
            geojson_file.write_text(json.dumps(geojson), encoding="utf-8")
            subprocess.run(["ogr2ogr", "-a_srs", "EPSG:7844", "-f", "ESRI Shapefile",
                            str(root / "CurrentTenements.shp"), str(geojson_file)], check=True)
            (root / "CurrentTenements.prj").write_text(
                'GEOGCS["GDA2020",DATUM["GDA2020",SPHEROID["GRS_1980",6378137.0,298.257222101]],'
                'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433],AUTHORITY["EPSG",7844]]',
                encoding="utf-8",
            )
            with ZipFile(zip_file, "w") as output:
                for path in sorted(root.glob("CurrentTenements.*")):
                    output.write(path, path.name)
            payload = zip_file.read_bytes()
        crs, features = _read_layer(payload, {"E 70/5037", "E 70/503"}, "EPSG:7844")
        self.assertEqual(crs, "EPSG:7844")
        self.assertEqual(set(features), {"E 70/5037"})
        feature, = features["E 70/5037"]
        self.assertEqual(feature["properties"]["TENID"], "E  7005037")
        self.assertNotIn("HOLDER1", feature["properties"])
        self.assertEqual(feature["geometry"]["type"], "Polygon")
        with self.assertRaises(IntegrityError):
            _read_layer(payload, {"E 70/5037"}, "EPSG:4283")


@unittest.skipUnless(os.environ.get("AU_REE_FULL_SOURCE_MANIFEST") and os.environ.get("AU_REE_FRESH_ASSETS")
                     and shutil.which("ogrinfo") and shutil.which("pdftotext"), "locked GIS sources unavailable")
class ExactGovernmentSourceTest(unittest.TestCase):
    def test_boundaries_only_with_issuer_and_cutoff_fingerprints(self):
        with tempfile.TemporaryDirectory() as directory:
            receipt = inventory_tenements(
                Path("config/research_profiles/wa_tenement_bindings_v1.json"),
                Path(os.environ["AU_REE_FULL_SOURCE_MANIFEST"]), Path(os.environ["AU_REE_FRESH_ASSETS"]),
                Path(directory) / "geography.json",
            )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["counts"], {"verified_boundaries": 7, "candidates": 5, "formal_indicators": 0})
        self.assertEqual(len(receipt["unbound_projects"]), 2)
        self.assertEqual({row["crs"] for row in receipt["locations"]}, {"EPSG:4283", "EPSG:7844"})
        for row in receipt["locations"]:
            self.assertEqual(row["location_role"], ROLE)
            self.assertIsNone(row["formal_indicator"])
            self.assertEqual(row["project_link"]["temporal_decision"]["status"], "ELIGIBLE")
            if row["status"] == "VERIFIED_BOUNDARY":
                self.assertEqual(row["temporal_decision"]["status"], "ELIGIBLE")
                self.assertEqual(len(row["geometry_sha256"]), 64)
                self.assertIsNone(row["precision"]["positional_accuracy_m"])
