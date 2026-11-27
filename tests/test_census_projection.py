"""Check the fixed column policy and its metadata/science boundary."""

from dataclasses import replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.census_metadata import load_projection_policy, prepare_census_metadata
from au_ree_v3.contracts import ContractViolation
from au_ree_v3.fresh_census import CensusAxes, build_census, verify_census
from au_ree_v3.store import Store
from test_fresh_census import PROFILE, RESOURCE_HEADING, _seed_release, read_csv
import test_store as fixtures


class CensusProjectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.axes = CensusAxes.load(PROFILE)
        cls.project = cls.axes.project_ids[0]

    def _cells(self, out):
        return {row["column_id"]: row for row in read_csv(out / "cell_status.csv") if row["project_id"] == self.project}

    def _source_metadata(self, *, status="ELIGIBLE", url="https://example.org/a.pdf", verified=True):
        receipt_sha, manifest_sha = "a" * 64, "b" * 64
        event = {
            "receipt_sha256": receipt_sha, "project_ids": [self.project], "status": "PUBLIC_CAPTURED",
            "sha256": "c" * 64, "publisher": "Original public publisher", "requested_url": url,
        }
        edition = {
            "receipt_sha256": receipt_sha, "project_ids": [self.project], "sha256": event["sha256"],
            "kind": "pdf", "source_key": "display-source", "temporal_decision": {
                "status": status, "policy_version": "test-policy", "decision_id": "d" * 64,
                "matching_evidence_ids": ["e" * 64],
            },
        }
        return prepare_census_metadata(
            PROFILE, {"events": [event], "editions": [edition]},
            {"verified_capture_sha256": [receipt_sha] if verified else []}, source_manifest_sha256=manifest_sha,
        )

    def test_ordered_column_contract_defines_each_display_and_evidence_role(self):
        policy = load_projection_policy(self.axes.columns)
        self.assertEqual(policy.version, "census-projection-v1")
        self.assertRegex(policy.sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(tuple(policy.by_name), self.axes.columns)
        self.assertEqual(len({row["column_id"] for row in policy.rows}), 51)
        self.assertTrue(all(row["provenance_required"] and not row["mcda_use"] for row in policy.rows))
        self.assertTrue(all(row["required_evidence"] and row["validation_rule"] for row in policy.rows))
        with self.assertRaisesRegex(ContractViolation, "pinned axes"):
            load_projection_policy(tuple(reversed(self.axes.columns)))

    def test_registry_metadata_is_attributed_and_missing_science_stays_unknown(self):
        metadata = prepare_census_metadata(PROFILE)
        with TemporaryDirectory() as scratch:
            out = Path(scratch)
            result = build_census(PROFILE, out, metadata=metadata)
            self.assertEqual(result, verify_census(PROFILE, out, metadata=metadata))
            self.assertEqual(result["cell_states"], {"PRESENT": 828, "UNKNOWN": 3915, "NOT_APPLICABLE": 0})
            self.assertEqual(result["coverage"]["metadata"]["present"], 828)
            self.assertEqual(result["coverage"]["scientific"]["direct_cells"], 0)
            cells = read_csv(out / "cell_status.csv")
            codes = [row for row in cells if row["column_id"] == "asx_code" and row["value"]]
            self.assertEqual(len(codes), 84)
            self.assertTrue(all(row["reason"] == "HISTORICAL_LISTING_CODE_ONLY" for row in codes))
            self.assertTrue(all(row["registry_sha256"] == self.axes.registry_sha for row in codes))
            self.assertTrue(all(row["value"] == "" for row in cells if row["column_id"] == "unlisted_company"))
            coordinates = [row for row in cells if row["column_id"] in {"longitude", "latitude"}]
            self.assertEqual(len(coordinates), 186)
            self.assertTrue(all((row["value"], row["geometry_role"]) == ("", "") for row in coordinates))
            ore = next(row for row in cells if row["column"] == RESOURCE_HEADING)
            self.assertEqual((ore["value_state"], ore["reason"], ore["value"]), ("UNKNOWN", "NO_SELECTED_MRE", ""))
            state = next(row for row in cells if row["column_id"] == "location_en")
            self.assertEqual(json.loads(state["metadata_evidence"])["mapping_sha256"], metadata.policy.sha256)

    def test_project_display_source_never_overwrites_selected_value_provenance(self):
        metadata = self._source_metadata()
        proof = fixtures.TEMPORAL_EVIDENCE
        with TemporaryDirectory() as scratch, Store(":memory:", trusted_temporal_evidence_ids=(proof.evidence_id,)) as store:
            root = Path(scratch)
            release = _seed_release(store, self.project, self.axes.cutoff)
            build = {"store": store, "release_id": release.release_id, "metadata": metadata}
            result = build_census(PROFILE, root / "one", **build)
            self.assertEqual(result, build_census(PROFILE, root / "two", **build))
            selected = self._cells(root / "one")
            source, grade = selected["source_url"], next(row for row in selected.values() if row["column"] == RESOURCE_HEADING)
            self.assertEqual((source["value"], source["value_origin"]), ("https://example.org/a.pdf", "SOURCE_EVIDENCE"))
            self.assertEqual(json.loads(source["metadata_evidence"])["scope"], "PROJECT_REFERENCE_ONLY")
            self.assertEqual(source["source_sha256"], "c" * 64)
            self.assertEqual((grade["value"], grade["evidence_state"], grade["source_sha256"]),
                             ("0.0000", "FORMAL_DIRECT", fixtures.SOURCE.sha256))
            self.assertEqual(json.loads(grade["precision"])["token"], "0.0000")
            self.assertEqual(result["coverage"]["scientific"]["unique_direct_observations"], 1)

    def test_unverified_late_or_conflicting_sources_have_explicit_null_states(self):
        for options, reason in (
            ({"verified": False}, "NOT_FOUND_AFTER_SEARCH"),
            ({"status": "INELIGIBLE"}, "TEMPORAL_INELIGIBLE"),
            ({"status": "CONFLICT"}, "SCIENTIFIC_CONFLICT"),
        ):
            with self.subTest(options=options), TemporaryDirectory() as scratch:
                out = Path(scratch)
                build_census(PROFILE, out, metadata=self._source_metadata(**options),
                             project_statuses={self.project: "NO_SOURCE_FOUND"})
                cell = self._cells(out)["has_source_document"]
                self.assertEqual((cell["value"], cell["value_state"], cell["reason"]), ("", "UNKNOWN", reason))

    def test_malformed_url_and_metadata_override_are_isolated(self):
        with TemporaryDirectory() as scratch:
            out = Path(scratch)
            metadata = self._source_metadata(url="javascript:alert(1)")
            metadata = replace(metadata, cells={
                **metadata.cells, (self.project, RESOURCE_HEADING): {"value": "1", "value_state": "PRESENT"},
            })
            build_census(PROFILE, out, metadata=metadata)
            cells = self._cells(out)
            self.assertEqual((cells["source_url"]["value"], cells["source_url"]["source_url"]), ("", ""))
            self.assertEqual(cells["has_source_document"]["value"], "是")
            self.assertEqual(next(cell for cell in cells.values() if cell["column"] == RESOURCE_HEADING)["value"], "")

    def test_not_applicable_requires_affirmative_evidence(self):
        with TemporaryDirectory() as scratch:
            out = Path(scratch)
            metadata = prepare_census_metadata(PROFILE)
            build_census(PROFILE, out, metadata=metadata)
            ledger = out / "cell_status.csv"
            ledger.write_bytes(ledger.read_bytes().replace(b",UNKNOWN,", b",NOT_APPLICABLE,", 1))
            with self.assertRaisesRegex(ValueError, "matching evidence or state"):
                verify_census(PROFILE, out, metadata=metadata)
