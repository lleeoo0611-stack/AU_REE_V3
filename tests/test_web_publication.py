"""Static web release integration, immutable hashes, and public schema counterexamples."""

from copy import deepcopy
import csv
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_census import build_census
from au_ree_v3.fresh_decision import build_decision
from au_ree_v3.fresh_research import BUILD_SCHEMA
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_artifacts import ARTIFACTS
from au_ree_v3.web_publication import (
    CANDIDATE, WebPublicationError, _coverage_view, _evidence, _gis_view, _public_url, _public_value,
    export_web, verify_web_release,
)

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/research_profiles/au_ree_2026-11-28.json"
RELEASE = "a" * 64


def _sha(path):
    return sha256(path.read_bytes()).hexdigest()


def _write(path, value):
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def _read(path):
    return json.loads(path.read_bytes())


def _minimal_build(path):
    settings = load_profile(PROFILE)
    registry = load_registry(settings.registry_path)
    build_census(PROFILE, path / "census")
    build_decision(PROFILE, path / "decision")
    receipt_path = path / "census/census_receipt.json"
    _write(receipt_path, {**_read(receipt_path), "release_id": RELEASE})
    inputs_path = path / "decision/mcda_inputs.json"
    _write(inputs_path, {**_read(inputs_path), "release_id": RELEASE})
    results_path = path / "decision/mcda_results.json"
    _write(results_path, {**_read(results_path), "release_id": RELEASE, "mcda_inputs_sha256": _sha(inputs_path)})
    decision_path = path / "decision/decision_receipt.json"
    _write(decision_path, {**_read(decision_path), "release_id": RELEASE, "mcda_inputs_sha256": _sha(inputs_path),
                           "mcda_results_sha256": _sha(results_path)})
    (path / "observations").mkdir()
    _write(path / "observations/extraction.json", {"release_id": RELEASE})
    (path / "observations/observations.jsonl").touch()
    _write(path / "build_receipt.json", {
        "schema": BUILD_SCHEMA, "profile_id": settings.profile_id, "research_cutoff": settings.research_cutoff.isoformat(),
        "code_commit": "1" * 40,
        "project_registry_hash": registry.sha256, "census_columns_sha256": _sha(settings.census_columns_path),
        "input_artifacts": {"profile": settings.sha256}, "gates": {"F0": "PASS", "F3": "PASS"},
        "scientific_artifacts": {name: _sha(path / name) for name in ARTIFACTS},
        "observation_release_id": RELEASE, "mcda_inputs_hash": _sha(inputs_path),
        "eligible_source_count": 0, "verified_source_count": 0, "comparable_project_criteria": 0,
    })


def _rewrite_with_pin(directory, name, change):
    path = directory / name
    content = _read(path)
    change(content)
    _write(path, content)
    manifest = _read(directory / "manifest.json")
    manifest["artifacts"][name]["sha256"] = _sha(path)
    _write(directory / "manifest.json", manifest)


class WebPublicationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.build = self.root / "research-build"
        _minimal_build(self.build)
        self.out = self.root / "web-data"
        self.target = self.out / "releases" / RELEASE

    def _export(self, out=None, release=RELEASE):
        return export_web(profile=PROFILE, research_build=self.build, release_id=release, out=out or self.out)

    def test_candidate_projects_csv_null_states_and_rebuild_are_exact(self):
        manifest = self._export()
        self.assertEqual(manifest["release_status"], CANDIDATE)
        self.assertFalse((self.out / "latest.json").exists())
        self.assertFalse((self.out / "preview.json").exists())
        self.assertEqual(verify_web_release(self.target, expected_release_id=RELEASE), manifest)
        self.assertEqual((self.target / "census.csv").read_bytes(), (self.build / "census/census.csv").read_bytes())
        census = _read(self.target / "census.json")
        counts = _read(self.target / "coverage.json")
        self.assertEqual((len(census["projects"]), len(census["columns"]), counts["cells"]), (93, 51, 4743))
        self.assertEqual(census["projects"][0]["cells"][0]["value"], census["projects"][0]["id"])
        self.assertEqual(census["projects"][0]["cells"][1]["state"], "UNKNOWN")
        self.assertIsNone(census["projects"][0]["cells"][1]["value"])
        self.assertEqual(_read(self.target / "gis.geojson")["features"], [])
        self.assertTrue(all(panel["status"] == "INSUFFICIENT_COHORT" and not panel["point_rankings"]
                            for panel in _read(self.target / "mcda.json")["panels"]))
        self.assertEqual(self._export(), manifest)
        other = self.root / "another-run"
        self.assertEqual(self._export(other), manifest)
        self.assertEqual(_sha(self.target / "manifest.json"), _sha(other / "releases" / RELEASE / "manifest.json"))

    def test_wrong_release_or_tampered_build_is_never_materialized(self):
        with self.assertRaisesRegex(WebPublicationError, "Release ID differs"):
            self._export(release="b" * 64)
        (self.build / "census/census.csv").write_bytes(b"wrong bytes")
        with self.assertRaisesRegex(WebPublicationError, "digest"):
            self._export()
        self.assertFalse(self.target.exists())

    def test_public_manifest_and_asset_faults_are_rejected(self):
        manifest = self._export()
        with self.assertRaisesRegex(WebPublicationError, "manifest digest"):
            verify_web_release(self.target, expected_manifest_sha256="1" * 64)
        target = self.target / "gis.geojson"
        target.unlink()
        with self.assertRaisesRegex(WebPublicationError, "missing"):
            verify_web_release(self.target)
        with self.assertRaisesRegex(WebPublicationError, "missing"):
            self._export()
        self.assertFalse(target.exists())
        self.assertEqual(manifest["release_id"], RELEASE)

    def test_rehashed_cross_release_cache_payload_still_fails(self):
        self._export()
        _rewrite_with_pin(self.target, "census.json", lambda value: value.update(release_id="b" * 64))
        with self.assertRaisesRegex(WebPublicationError, "mixes release identities"):
            verify_web_release(self.target)

    def test_rehashed_private_evidence_and_fake_rank_are_rejected(self):
        self._export()
        original = (self.target / "census_evidence.json").read_bytes()
        original_manifest = (self.target / "manifest.json").read_bytes()

        def add_private(value):
            next(iter(value["entries"].values()))["private_receipt"] = "/tmp/protected.json"

        _rewrite_with_pin(self.target, "census_evidence.json", add_private)
        with self.assertRaisesRegex(WebPublicationError, "prohibited metadata"):
            verify_web_release(self.target)
        (self.target / "census_evidence.json").write_bytes(original)
        (self.target / "manifest.json").write_bytes(original_manifest)

        def add_rank(value):
            value["panels"][0]["point_rankings"] = {"invented": {}}

        _rewrite_with_pin(self.target, "mcda.json", add_rank)
        with self.assertRaisesRegex(WebPublicationError, "insufficient cohort"):
            verify_web_release(self.target)

    def test_malicious_links_and_private_fields_are_excluded_from_public_evidence(self):
        texts = ["<script>hello</script>", "23.4"]
        self.assertTrue(all(_public_value("專案名稱", value) for value in texts))
        self.assertFalse(_public_value("專案名稱", "=HYPERLINK()"))
        self.assertTrue(_public_value("緯度", "-23.4"))
        self.assertTrue(_public_value("TREO品位_pct", "0"))
        for value in ("javascript:alert(1)", "data:text/html,x", "https://user:secret@public.gov.au/report",
                      "https://127.0.0.1/report", "https://host.internal/report", "https://gov.au/file?token=secret",
                      "https://gov.au/file?file=%2Ftmp%2Fsecret"):
            self.assertFalse(_public_url(value), value)
        self.assertTrue(_public_url("https://www.wa.gov.au/file?inline"))
        self.assertEqual(_evidence({"reason": "HISTORICAL", "source_url": "javascript:alert(1)",
                                    "locator": "/tmp/proof", "internal_seal": "confidential"})["reason"], "HISTORICAL")
        self.assertNotIn("source_url", _evidence({"source_url": "javascript:alert(1)"}))
        self.assertNotIn("locator", _evidence({"locator": "/tmp/proof"}))

    def test_reference_counts_do_not_inflate_metadata_or_formal_science(self):
        project = "AU-" + "a" * 20
        columns = [{"id": name, "column_id": name} for name in ("專案ID", "開發商", "有TREO資源估計")]
        origins = ("IDENTITY", "REGISTRY_SNAPSHOT", "EVIDENCE_REFERENCE")
        cells = [{"state": "PRESENT", "lineage_id": origin, "origin": origin} for origin in origins]
        evidence = {origin: {"reason": "SOURCE_BOUND"} for origin in origins}
        receipt = {"cell_states": {"PRESENT": 3}, "coverage": {"metadata": {"present": 2, "denominator": 2,
                                                                          "rate": "1.000000"}}}
        result = _coverage_view(columns, [{"id": project, "cells": cells}], evidence, receipt, {
            "comparable_project_criteria": 0, "verified_source_count": 1, "eligible_source_count": 1,
        }, {"project_criteria": {}, "panels": []})
        self.assertEqual((result["metadata_cells"], result["evidence_reference_cells"], result["formal_direct_cells"]),
                         (1, 1, 0))
        self.assertEqual(result["other_present_cells"], 0)

    def test_gis_only_projects_matching_site_points_and_never_tenement_centroids(self):
        project = "AU-" + "a" * 20
        columns = [{"id": "經度"}, {"id": "緯度"}]
        cells = [{"value": value, "state": "PRESENT", "origin": "FORMAL_DIRECT", "lineage_id": lineage}
                 for value, lineage in (("118.0", "x"), ("-27.0", "y"))]
        source = {"source_sha256": "c" * 64, "temporal_decision_id": "d" * 64, "temporal_status": "ELIGIBLE",
                  "locator": "Mapped marker 16", "geometry_role": "VERIFIED_DEPOSIT_POINT",
                  "scope_id": "deposit-scope", "crs": "OGC:CRS84"}
        project_data = [{"id": project, "cells": cells}]
        evidence = {"x": source, "y": deepcopy(source)}
        point, = _gis_view(columns, project_data, evidence)["features"]
        self.assertEqual(point["geometry"], {"type": "Point", "coordinates": [118.0, -27.0]})
        evidence["y"]["geometry_role"] = "TENEMENT_POLYGON_CENTROID"
        with self.assertRaisesRegex(WebPublicationError, "qualified site"):
            _gis_view(columns, project_data, evidence)
        cells[1]["state"] = "UNKNOWN"
        self.assertEqual(_gis_view(columns, project_data, evidence)["features"], [])


@unittest.skipUnless(os.environ.get("AU_REE_WEB_RESEARCH_BUILD"), "verified local research build not supplied")
class RealResearchWebTests(unittest.TestCase):
    def test_fixed_real_research_values_and_rankings_preserved(self):
        build = Path(os.environ["AU_REE_WEB_RESEARCH_BUILD"])
        profile = Path(os.environ.get("AU_REE_WEB_RESEARCH_PROFILE", PROFILE))
        receipt = _read(build / "build_receipt.json")
        release_id = os.environ.get("AU_REE_WEB_RELEASE_ID", receipt["observation_release_id"])
        release_root = os.environ.get("AU_REE_WEB_RELEASE_ROOT")
        with TemporaryDirectory() as temporary:
            out = Path(temporary)
            manifest = export_web(profile=profile, research_build=build, release_id=release_id, out=out,
                                  release_root=Path(release_root) if release_root else None)
            target = out / "releases" / release_id
            self.assertEqual(verify_web_release(target, expected_release_id=release_id), manifest)
            self.assertEqual((target / "census.csv").read_bytes(), (build / "census/census.csv").read_bytes())
            coverage = _read(target / "coverage.json")
            self.assertIn(receipt["scientific_output_count"], {12, 16, 18})
            derived = coverage["formal_derived_cells"]
            self.assertEqual(coverage["formal_direct_cells"] + derived, receipt["scientific_output_count"])
            self.assertEqual(derived, 2 if receipt["scientific_output_count"] == 18 else 0)
            self.assertEqual(coverage["comparable_project_criteria"], 6)
            self.assertEqual((coverage["projects"], coverage["columns"], coverage["present_source_count"]), (93, 51, 144))
            real_results = _read(build / "decision/mcda_results.json")["panels"]
            projected = {panel["id"]: panel for panel in _read(target / "mcda.json")["panels"]}
            self.assertEqual(real_results.keys(), projected.keys())
            for key, panel in real_results.items():
                self.assertEqual(projected[key]["point_rankings"], panel["point_rankings"])
                self.assertEqual(projected[key]["leave_one_out"], panel["leave_one_out"])
            self.assertEqual(manifest["release_status"], CANDIDATE)
            self.assertIsNone(manifest["authorization_sha256"])
            census = _read(target / "census.json")
            evidence = _read(target / "census_evidence.json")["entries"]
            columns = {rule["id"]: index for index, rule in enumerate(census["columns"])}
            projects = {row["id"]: row["cells"] for row in census["projects"]}
            with (build / "census/cell_status.csv").open(encoding="utf-8", newline="") as stream:
                rows = tuple(csv.DictReader(stream))
            reference_rows = [row for row in rows if row["value_origin"] == "EVIDENCE_REFERENCE"]
            attributes = [row for row in rows if row.get("evidence_state") == "VALIDATED_DESCRIPTIVE"]
            self.assertEqual(len(attributes), 5 if profile.name == "au_ree_census_attributes_v1.json" else 0)
            for row in attributes:
                cell = projects[row["project_id"]][columns[row["column"]]]
                anchor = evidence[cell["lineage_id"]]
                self.assertEqual((cell["value"], anchor["source_sha256"]), (row["value"], row["source_sha256"]))
                self.assertEqual(anchor["evidence_state"], "VALIDATED_DESCRIPTIVE")
                self.assertIn(anchor["metadata"]["attribute_role"], {"CONFIRMED_DEPOSIT_TYPE", "COMPLETED"})
            self.assertEqual(coverage["evidence_reference_cells"], len(reference_rows))
            self.assertEqual(coverage["other_present_cells"], 0)
            if reference_rows:
                self.assertEqual(len(reference_rows), 4)
                self.assertEqual(coverage["cell_states"]["PRESENT"], 1092 + receipt["scientific_output_count"] + len(attributes))
                self.assertEqual((coverage["identity_cells"], coverage["metadata_cells"]), (93, 995 + len(attributes)))
                for reference in reference_rows:
                    cell = projects[reference["project_id"]][columns["有TREO資源估計"]]
                    anchor = evidence[cell["lineage_id"]]
                    self.assertEqual((cell["value"], cell["origin"]), ("是", "EVIDENCE_REFERENCE"))
                    self.assertEqual(anchor["evidence_state"], "DESCRIPTIVE_ONLY")
                    self.assertEqual(anchor["source_sha256"], reference["source_sha256"])
                    self.assertEqual(anchor["input_observation_ids"], json.loads(reference["input_observation_ids"]))
                    self.assertEqual(len(anchor["input_observation_ids"]), 3)
            if receipt["scientific_output_count"] >= 16:
                self.check_scoped_resource_values(projects, columns, evidence, derived=bool(derived))
            self.assertEqual(_read(target / "gis.geojson")["features"], [])

    def check_scoped_resource_values(self, projects, columns, evidence, *, derived):
        oracle = _read(ROOT / "tests/fixtures/native_resource_v1/expected_census.json")
        for case in oracle["cases"]:
            cells = projects[case["project_id"]]
            contained = cells[columns["總TREO蘊藏量_kt"]]
            if derived:
                ore, grade = (cells[columns[value["column"]]] for value in case["values"])
                provenance = evidence[contained["lineage_id"]]
                point = Decimal(ore["value"]) * Decimal(grade["value"]) * 10
                self.assertEqual(Decimal(contained["value"]), point)
                self.assertEqual(contained["origin"], "FORMAL_DERIVED")
                self.assertIn("formula", provenance)
                self.assertEqual(provenance["input_observation_ids"], [
                    evidence[cell["lineage_id"]]["observation_id"] for cell in (ore, grade)
                ])
                low, high = map(Decimal, contained["precision"]["rounding_interval"])
                self.assertLess(low, point)
                self.assertGreater(high, point)
            else:
                self.assertIsNone(contained["value"])
            for expected in case["values"]:
                with self.subTest(project=case["project_id"], column=expected["column"]):
                    cell = cells[columns[expected["column"]]]
                    anchor = evidence[cell["lineage_id"]]
                    self.assertEqual(Decimal(cell["value"]), Decimal(expected["census_value"]))
                    self.assertEqual(cell["origin"], "FORMAL_SELECTION")
                    self.assertEqual(anchor["physical_page"], case["physical_page"])
                    self.assertEqual(anchor["source_sha256"], case["source_sha256"])
                    self.assertEqual((anchor["raw_value"], anchor["raw_unit"]),
                                     (expected["raw_token"], expected["raw_unit"]))
                    self.assertEqual(list(map(Decimal, cell["precision"]["rounding_interval"])),
                                     list(map(Decimal, expected["raw_rounding_interval"])))
