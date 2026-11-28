"""Keep government tenement boundaries outside the site-distance input role."""

from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from au_ree_v3.contracts import canonical_json
from au_ree_v3.geography import SCHEMA, evaluate_infrastructure_candidates, infrastructure_site_compatibility
from au_ree_v3.sources import IntegrityError, SCHEMA as SOURCE_SCHEMA

DIGEST = "a" * 64
CUTOFF = "2026-11-28"
GOVERNMENT_URL = "https://example.gov.au/wa-tenement.zip"
DECISION = {"status": "ELIGIBLE", "source_sha256": DIGEST, "cutoff": CUTOFF, "decision_id": "valid-test-decision"}


class InfrastructureCompatibilityTests(unittest.TestCase):
    def test_polygon_and_centroid_cannot_supply_the_point_role(self):
        for role, geometry in (
            ("LEGAL_TENEMENT_BOUNDARY", {"type": "Polygon", "coordinates": []}),
            ("TENEMENT_CENTROID", {"type": "Point", "coordinates": [123, -25]}),
            ("OFFICIAL_DEPOSIT_POINT", {"type": "Polygon", "coordinates": []}),
        ):
            with self.subTest(role=role, geometry=geometry):
                result = infrastructure_site_compatibility({
                    "location_role": role, "geometry": geometry, "source_sha256": DIGEST,
                    "crs": "EPSG:7844", "locator": {"native_id": "W123"},
                    "precision": {"positional_accuracy_m": 20}, "temporal_decision": DECISION,
                    "project_link": {"source_sha256": DIGEST},
                })
                self.assertEqual(result["status"], "NOT_COMPARABLE")

    def test_verified_role_and_fields_reach_candidate_review_results(self):
        feature_date = "2025-08-29"
        feature_decision = {**DECISION, "observation_role": "HISTORICAL_MEASUREMENT", "effective_date": feature_date}
        valid = {
            "location_role": "OFFICIAL_DEPOSIT_POINT",
            "geometry": {"type": "Point", "coordinates": [123, -25]}, "source_sha256": DIGEST,
            "crs": "EPSG:7844", "locator": {"native_id": "W123"},
            "precision": {"positional_accuracy_m": 20}, "temporal_decision": feature_decision,
            "feature_effective_date": feature_date,
            "project_link": {"source_sha256": DIGEST},
        }
        self.assertEqual(infrastructure_site_compatibility(valid)["status"], "SITE_CANDIDATE")
        for decision in ({**feature_decision, "source_sha256": "wrong"},
                         {**feature_decision, "effective_date": "2025-08-28"},
                         {**feature_decision, "observation_role": "DOCUMENT"}, DECISION):
            with self.subTest(decision=decision):
                contaminated = {**valid, "temporal_decision": decision}
                self.assertIn("eligible_temporal_decision",
                              infrastructure_site_compatibility(contaminated)["missing_dimensions"])
        undated = infrastructure_site_compatibility({**valid, "feature_effective_date": None})
        self.assertEqual(undated["status"], "INSUFFICIENT_CONTEXT")
        self.assertIn("feature_effective_date", undated["missing_dimensions"])
        late_date = "2026-11-29"
        late = {**valid, "feature_effective_date": late_date,
                "temporal_decision": {**feature_decision, "effective_date": late_date}}
        self.assertEqual(infrastructure_site_compatibility(late)["status"], "INSUFFICIENT_CONTEXT")
        for month, expected in (("2025-08", "SITE_CANDIDATE"), ("2026-11", "INSUFFICIENT_CONTEXT")):
            with self.subTest(month=month):
                precise_as_reported = {**valid, "feature_effective_date": month,
                                       "temporal_decision": {**feature_decision, "effective_date": month}}
                self.assertEqual(infrastructure_site_compatibility(precise_as_reported)["status"], expected)
        unknown = infrastructure_site_compatibility({})
        self.assertEqual(unknown["status"], "INSUFFICIENT_CONTEXT")

    def test_role_gate_retains_actual_boundary_fields_and_populates_missing_for_all_criteria(self):
        geometry = {"type": "Polygon", "coordinates": [[[122, -26], [123, -26], [123, -25], [122, -26]]]}
        record = {
            "project_id": "A", "status": "VERIFIED_BOUNDARY", "location_role": "LEGAL_TENEMENT_BOUNDARY",
            "source_sha256": DIGEST, "source_url": GOVERNMENT_URL, "licence": "E 70/1234", "crs": "EPSG:7844",
            "geometry": geometry, "geometry_sha256": sha256(canonical_json(geometry).encode()).hexdigest(),
            "locator": {"native_id": "W123"}, "temporal_decision": DECISION,
        }
        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            manifest = {
                "schema": SOURCE_SCHEMA, "cutoff": CUTOFF, "timezone": "Asia/Taipei",
                "projects": [{"project_id": "A"}, {"project_id": "B"}],
                "editions": [{"kind": "zip", "sha256": DIGEST, "project_ids": ["A"], "final_url": GOVERNMENT_URL}],
            }
            sources = root / "manifest.json"
            sources.write_text(canonical_json(manifest), encoding="utf-8")
            inventory = {
                "schema": SCHEMA, "status": "PASS", "cutoff": CUTOFF, "timezone": "Asia/Taipei",
                "sources_sha256": sha256(sources.read_bytes()).hexdigest(),
                "locations": [record], "unbound_projects": {},
            }
            features = root / "tenements.json"
            features.write_text(canonical_json(inventory), encoding="utf-8")
            result = evaluate_infrastructure_candidates(features, sources, root / "site_review.json")
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["project_count"], 2)
            self.assertEqual(result["formal_indicators"], 0)
            self.assertEqual(result["legal_boundaries"], 1)
            entries = result["project_criteria"]
            self.assertEqual(entries[0]["geometry_evidence"][0]["native_locator"], {"native_id": "W123"})
            self.assertTrue(all(
                item["value"] is None and item["status"] == "INSUFFICIENT_CONTEXT"
                for entry in entries for item in entry["criteria"].values()
            ))
            record["geometry"] = {"type": "Point", "coordinates": [123, -25]}
            features.write_text(canonical_json(inventory), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                evaluate_infrastructure_candidates(features, sources, root / "tampered.json")


if __name__ == "__main__":
    unittest.main()
