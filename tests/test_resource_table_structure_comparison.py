"""Regression checks for independent extraction scoring and edition groups."""

from unittest import TestCase

from tools.compare_resource_table_structure import (
    LocatedRow, Prediction, _claim_field, _native_column, _probe_result, _same_row_matches, _source_partitions,
)


class ResourceStructureComparisonTest(TestCase):
    def test_numeric_markers_must_remain_on_one_row(self):
        caption = "Table 4 Mineral Resource"
        page = f"{caption}\nTotal 56 2.6 1,456\nInferred 26 4.2 1,092"
        assembled = (
            LocatedRow("Total 56 2.6 1,456", "original row", ()),
            LocatedRow("Inferred 26 4.2 1,092", "different row", ()),
        )
        hits, status = _same_row_matches(page, [caption, "56", "2.6"], "56", assembled)
        self.assertIsNone(status)
        self.assertEqual(hits, assembled[:1])
        hits, status = _same_row_matches(page, [caption, "56", "4.2"], "56", assembled)
        self.assertEqual(hits, ())
        self.assertEqual(status, "MARKERS_NOT_BOUND_TO_NUMERIC_TABLE_ROW")

    def test_same_literal_in_two_columns_is_scored_by_printed_occurrence(self):
        original = "Total   62   62"
        first = (9.0, 1.0, 11.0, 3.0)
        second = (15.0, 1.0, 17.0, 3.0)
        predicted = Prediction("ore_mt", "Mt", "62", "later box", "Tonnes", bbox=second)
        row = LocatedRow(original, "same row", (predicted,), printed_tokens=(("62", first), ("62", second)))
        probe = {
            "row": {"line": 15, "positioned_text": original},
            "expected_fields": [{"variable": "ore_mt", "unit": "Mt", "literal": "62", "start_column": 9}],
            "forbidden_bindings": [],
            "target_resource_row": True,
        }
        result = _probe_result(probe, [row])
        self.assertEqual(_native_column(original, row, predicted), 14)
        self.assertEqual({key: result["automatic"][key] for key in ("TP", "FP", "FN")},
                         {"TP": 0, "FP": 1, "FN": 1})

    def test_other_positive_row_roles_remain_unscored_unless_forbidden(self):
        original = "Total 64 21 2.1"
        grade = Prediction("grade", "ppm", "21", "grade", "TREO", start_column=10)
        oxide = Prediction("nd2o3_ppm", "ppm", "2.1", "oxide", "Nd2O3", start_column=13)
        rows = [LocatedRow(original, "annotated row", (grade, oxide), line=15)]
        probe = {
            "row": {"line": 15, "positioned_text": original},
            "expected_fields": [{"variable": "grade", "unit": "ppm", "literal": "21", "start_column": 10}],
            "forbidden_bindings": [],
            "target_resource_row": True,
        }
        result = _probe_result(probe, rows)["automatic"]
        self.assertEqual((result["TP"], result["FP"], result["unscored_fields"]), (1, 0, 1))
        probe["forbidden_bindings"].append({"variable": "nd2o3_ppm", "literal": "2.1"})
        result = _probe_result(probe, rows)["automatic"]
        self.assertEqual((result["TP"], result["FP"], result["unscored_fields"]), (1, 1, 0))

    def test_report_family_exclusion_propagates_to_related_editions(self):
        claims = {
            "issuer-peer": [({"project_id": "p1"}, {})],
            "project-peer": [({"project_id": "p1"}, {})],
            "independent": [({"project_id": "p2"}, {})],
        }
        holdout = {"fixed": [({"project_id": "p3", "issuer": "Mixed Case LTD", "report_family": "report"}, {})]}
        registry = {"p1": {"developer": "Mixed Case Pty. Limited"}, "p2": {"developer": "Other"}}
        split = _source_partitions(claims, holdout, registry, {"editions": []})
        self.assertEqual(split["issuer-peer"], split["fixed"])
        self.assertEqual(split["project-peer"], split["fixed"])
        self.assertEqual(split["fixed"]["partition"], "FIXED_HOLDOUT_EXCLUDED")
        self.assertNotEqual(split["independent"]["group_sha256"], split["fixed"]["group_sha256"])

    def test_transformed_oxide_never_used_as_original_role_label(self):
        claim = {"variable": "reported_resource_pr_ppm", "basis": "Pr", "original_token": "523",
                 "cell_binding": {"mode": "native_cell", "original_unit": "ppm"}}
        self.assertIsNone(_claim_field(claim))
        claim["basis"] = "Pr6O11"
        self.assertEqual(_claim_field(claim)[:3], ("pr6o11_ppm", "ppm", "523"))
        claim["species_conversion"] = {"original": "Pr"}
        self.assertIsNone(_claim_field(claim))
