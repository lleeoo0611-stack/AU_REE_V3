"""Independent expectations for historical cohort triage, not a selection authority."""

from unittest import TestCase

from au_ree_v3.cohort_admission import _best, _check, _purpose, _route_state, _text_checks


class CohortAdmissionTest(TestCase):
    def setUp(self):
        self.original = "a" * 64
        self.locator = "PDF page 3, line 5, columns 30-32"
        self.empty = {"selection_id": "", "locator": "", "value_origin": "", "source_sha256": "",
                      "value_state": "UNKNOWN"}
        self.numeric = "NUMERIC", self.original, [self.locator], "candidate", {
            "ORIGINAL": _check("PASS", "EXACT_ORIGINAL_TOKEN_ANCHOR"),
            "JORC_CATEGORY": _check("FAIL", "INFERRED_ONLY_OUTSIDE_ADMISSION"),
        }

    def test_resolving_display_does_not_mark_resource_formally_selected(self):
        display = {"source_sha256": self.original, "locator": self.locator}
        result = _route_state(self.numeric, self.empty, display)
        self.assertEqual(result["display"]["status"], "PRESENT")
        self.assertEqual(result["formal"]["first_open_gate"], "JORC_CATEGORY")
        self.assertEqual(result["formal"]["status"], "FAIL")

    def test_matching_project_and_value_without_exact_locator_is_insufficient(self):
        different_locator = "PDF page 3, line 8, columns 30-32"
        archived = {**self.empty, "locator": different_locator, "selection_id": "selection",
                    "source_sha256": self.original, "value_origin": "FORMAL_SELECTION", "value_state": "PRESENT"}
        display = {"source_sha256": self.original, "locator": different_locator}
        result = _route_state(self.numeric, archived, display)
        self.assertIsNone(result["selection_id"])
        self.assertNotEqual(result["display"]["status"], "PRESENT")

    def test_considers_best_path_and_preserves_failed_alternatives(self):
        unavailable = _purpose({"CUTOFF": _check("BLOCKED", "NO_EDITION_PROOF")}, "PASS")
        conflicted = _purpose({"SCOPE": _check("FAIL", "SCOPE_CONFLICT")}, "PASS")
        not_run = _purpose({"CUTOFF": _check("NOT_RUN", "NOT_TESTED")}, "PASS")
        self.assertEqual(_best([{"formal": row} for row in (unavailable, conflicted)], "formal"), conflicted)
        self.assertEqual(not_run["status"], "NOT_RUN")

    def test_verified_same_edition_moves_text_to_typed_role_check(self):
        text = {"temporal_decision": {
            "status": "ELIGIBLE", "reason": "EXACT_EDITION_PUBLIC_BY_CUTOFF",
            "decision_id": "temporal", "policy_version": "temporal-evidence-v1",
        }}
        self.assertEqual(_purpose(_text_checks(text), "PASS")["first_open_gate"], "COLUMN_ROLE")
        text["temporal_decision"]["status"] = "INELIGIBLE"
        self.assertEqual(_purpose(_text_checks(text), "PASS")["status"], "FAIL")
