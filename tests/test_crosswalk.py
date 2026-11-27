import csv
import json
import tempfile
import unittest
from pathlib import Path

from au_ree_v3.migration.crosswalk import (
    _census_round_trip, _check_contract, _field_differences, digest, load_spec,
    observation_raw_id, project_fields, repository_file, verify_crosswalk,
)


class CrosswalkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.spec = load_spec()

    def test_machine_and_human_maps_agree(self):
        path = repository_file("docs/migration/schema_crosswalk.csv")
        with path.open(encoding="utf-8", newline="") as stream:
            _check_contract(self.spec, list(csv.DictReader(stream)))
        deferred = [row for row in self.spec["fields"] if row["status"] == "LOSSY_OR_UNRESOLVED"]
        self.assertEqual([(r["v2_table_or_type"], r["v2_field"]) for r in deferred],
                         [("observations", "value")])

    def test_dropped_or_type_changed_source_column_fails(self):
        rows = self.spec["fields"]
        self.assertEqual(_field_differences(rows, {"entities": {
            "entity_id": "TEXT", "kind": "TEXT", "native_namespace": "TEXT", "native_id": "TEXT",
        }}), [])
        issues = _field_differences(rows, {"entities": {
            "entity_id": "TEXT", "kind": "TEXT", "native_namespace": "TEXT", "native_id": "INTEGER",
            "future_field": "TEXT",
        }})
        self.assertEqual([row["field"] for row in issues], ["future_field", "native_id"])

    def test_precision_null_scope_and_id_mapping(self):
        original = {
            row["v2_field"]: (
                "native-project" if row["v2_null_semantics"] == "REQUIRED" else None
            )
            for row in self.spec["fields"]
            if row["v2_table_or_type"] == "RawObservation" and row["mapping_rule"] == "COPY"
        }
        original.update(observation_id="AU-observation", original_value="1.002300", original_unit="ppm",
                        scope_id="deposit-north", denominator_basis="HEAVY_MINERAL", effective_date="2025-09")
        result = project_fields(self.spec["fields"], "RawObservation", original)
        self.assertEqual(result["raw_value"], "1.002300")
        self.assertEqual(result["observation_id"], "AU-observation")
        self.assertEqual(result["scope_id"], "deposit-north")
        self.assertEqual(result["denominator_basis"], "HEAVY_MINERAL")
        self.assertEqual(result["effective_date"], "2025-09")
        self.assertIsNone(result["chemical_basis"])
        with self.assertRaisesRegex(ValueError, "original text"):
            project_fields(self.spec["fields"], "RawObservation", {**original, "original_value": 1.0023})

    def test_raw_id_binding_separate_from_published_id(self):
        anchor = {"sha256": "a" * 64, "page": 2, "table": 0, "row": 4, "column": 7,
                  "bbox": [1.4, 2, 3, 4], "offset": 0}
        record = {"evidence": anchor, "observation_id": "published-observation",
                  "variable": "grade", "extractor_version": "v2-extractor", "extraction_fingerprint": "b" * 64}
        expected = digest(["observation-v1", "published-observation", "a" * 64, 2, 0, 4, 7,
                           [1.4, 2, 3, 4], 0, "grade", "v2-extractor", "b" * 64])
        self.assertEqual(observation_raw_id(record), expected)
        self.assertNotEqual(observation_raw_id({**record, "evidence": {**anchor, "page": 3}}), expected)

    def test_blank_and_reported_zero_have_distinct_states(self):
        census = [{"專案ID": "AU-one", "x": "", "y": "0.00", "z": ""}]
        cells = [
            ("專案ID", "AU-one", "PRESENT", "VALUE_PRESENT"),
            ("x", "", "BLANK", "NOT_APPLICABLE_NO_MRE"),
            ("y", "0.00", "PRESENT", "VALUE_PRESENT"),
            ("z", "", "BLANK", "NO_SOURCE_VALUE"),
        ]
        states = [
            {"project_id": "AU-one", "column": name, "value_state": state, "status": reason}
            for name, _, state, reason in cells
        ]
        provenance = [{"project_id": "AU-one", "column": name, "value": val} for name, val, _, _ in cells]
        self.assertEqual(_census_round_trip(census, states, provenance)["states"],
                         {"PRESENT": 2, "NOT_APPLICABLE": 1, "APPLICABLE_MISSING": 1})
        with self.assertRaisesRegex(ValueError, "disagrees"):
            _census_round_trip([{**census[0], "x": "0"}], states, provenance)

    def test_missing_frozen_git_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = verify_crosswalk(root / "missing", root)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(json.loads((root / "schema_diff.json").read_text()), result)
