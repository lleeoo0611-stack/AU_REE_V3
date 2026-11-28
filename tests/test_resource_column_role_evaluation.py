"""Resource column research stays on exact original positions and held groups."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase

from tools.evaluate_resource_column_roles import (
    Example, Features, RoleModel, _additional_groups, _claim_role, _deduplicate_development, _scores,
    _unique_claim_cell,
)


def _features(header, unit="ppm", position=0.25):
    return Features(header, unit, position, position, None, ())


class ResourceColumnRoleEvaluationTest(TestCase):
    def test_exact_numeric_format_equivalence_and_no_rounding_guess(self):
        row = SimpleNamespace(printed_tokens=(("4,389", (1, 2, 3, 4)), ("341", (7, 2, 9, 4))))
        match, reason = _unique_claim_cell(row, {"value": 4389, "page_markers": ["Total 4,389 341"]})
        self.assertEqual(match, ("4,389", (1, 2, 3, 4)))
        self.assertEqual(reason, "EXACT_NUMERIC_MARKER")
        self.assertEqual(_unique_claim_cell(row, {"value": 4390, "page_markers": ["Total 4,389 341"]}),
                         (None, "NO_EXACT_VALUE_MARKER"))

    def test_equal_literals_are_ambiguous_even_with_a_claimed_column(self):
        row = SimpleNamespace(printed_tokens=(("25", (1, 2, 3, 4)), ("25.0", (7, 2, 9, 4))))
        claim = {"original_token": "25", "cell_binding": {"column_bbox": [0, 0, 4, 4]}}
        self.assertEqual(_unique_claim_cell(row, claim), (None, "AMBIGUOUS_VALUE_OCCURRENCE"))

    def test_source_only_column_features_cannot_create_transformed_weak_roles(self):
        self.assertIsNone(_claim_role({"variable": "grade", "transform": {"formula": "mass / ore"}}))
        self.assertIsNone(_claim_role({"variable": "reported_resource_pr_ppm", "basis": "Pr"}))
        self.assertEqual(_claim_role({"variable": "reported_resource_pr_ppm", "basis": "Pr6O11"}), "pr6o11_ppm")

    def test_duplicate_positions_and_conflicting_roles_are_removed(self):
        grade = Example("A", 1, (0, 0, 3, 3), "development", "grade", _features("TREO"))
        ore = Example("A", 1, grade.box, "development", "ore_mt", _features("Tonnes"))
        retained, statuses = _deduplicate_development([(0, grade), (1, grade)])
        self.assertEqual(retained, [grade])
        self.assertEqual(statuses, {0: "TRAINING_POSITION", 1: "DUPLICATE_POSITION"})
        self.assertEqual(_deduplicate_development([(0, grade), (1, ore)]),
                         ([], {0: "CONFLICTING_WEAK_ROLES", 1: "CONFLICTING_WEAK_ROLES"}))

    def test_tf_idf_handles_formatted_headers_and_false_positives_are_counted(self):
        grade = Example("A", 1, (0, 0, 3, 3), "development", "grade", _features("TREO grade"))
        ore = Example("B", 1, (0, 0, 3, 3), "development", "ore_mt", _features("Tonnes", "Mt", 0))
        model = RoleModel([grade, ore])
        self.assertEqual(model.predict(_features("ＴＲＥＯ GRADE")), "grade")
        unknown = Example("C", 1, (0, 0, 3, 3), "held", "OTHER", _features("TREO grade"))
        self.assertEqual(_scores([unknown], model.predict)["supported_resource_roles"]["FP"], 1)
        self.assertIsNone(model.predict(_features("")))

    def test_oracle_cannot_include_a_development_group(self):
        rows = [{"source_sha256": "A", "group_sha256": "a", "partition": "DEVELOPMENT", "physical_page": 1,
                 "cells": [{"bbox": [1, 2, 3, 4], "role": "grade"}]}]
        data = json.dumps({"schema": "resource-column-oracle-v1", "rows": rows}).encode()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "holdout.json"
            path.write_bytes(data)
            args = SimpleNamespace(oracle=(path, sha256(data).hexdigest()))
            with self.assertRaisesRegex(ValueError, "overlaps a development"):
                _additional_groups(args, {"A": {"group_sha256": "a", "partition": "DEVELOPMENT"}})
