"""Check evidence-bound research delta and replay readback behavior."""

from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
import runpy
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from au_ree_v3.decision import leave_one_out, rank_panel
from au_ree_v3.fresh_decision import _score_panel
from au_ree_v3.research_artifacts import ARTIFACTS
from au_ree_v3.research_delta import (
    ResearchBuild, _census_delta, _checked_criterion, _checked_panel, _comparability_delta, _replay_readback,
    _fixed_cohort, _same_census_quantity,
)
from tools.verify_panel_cohorts import _match, _verify_effects, decimal_scenario


PROJECT = "synthetic-project"
OTHER = "synthetic-other"
COLUMN = "TREO品位_pct"
UNKNOWN = {"state": "UNKNOWN", "reason": "NO_SOURCE", "accepted_value": None, "utility": None, "lineage": []}
KNOWN = {**UNKNOWN, "state": "COMPARABLE", "reason": "CHECKED", "accepted_value": "0.00", "utility": 0.0}


class ResearchDeltaTest(unittest.TestCase):
    def test_precise_census_projection_preserves_raw_source_units(self):
        observation = {"variable": "grade", "value": "470", "unit": "ppm", "raw_value": "470", "raw_unit": "ppm"}
        cell = {"column": COLUMN, "value": "0.0470", "unit": "%", "raw_value": "470", "raw_unit": "ppm"}
        self.assertTrue(_same_census_quantity(cell, observation))
        self.assertFalse(_same_census_quantity({**cell, "value": "0.470"}, observation))
        self.assertFalse(_same_census_quantity({**cell, "raw_unit": "%"}, observation))

    def test_census_separates_exact_reported_zero_from_unknown_and_identifiers(self):
        common = {
            "value": "", "value_state": "UNKNOWN", "reason": "NO_ACCEPTED_OBSERVATION",
            "value_origin": "", "source_sha256": "", "selection_id": "", "release_id": "",
        }
        known = {
            **common, "value": "0.00", "value_state": "PRESENT", "value_origin": "FORMAL_SELECTION",
            "reason": "GOVERNED_SELECTION", "selection_id": "selection", "release_id": "release-1",
            "source_sha256": "f" * 64,
        }

        def build(cell):
            return ResearchBuild({}, "", {(PROJECT, COLUMN): cell}, {}, {}, {}, (COLUMN,), (PROJECT,))

        added, = _census_delta(build(common), build(known))
        self.assertEqual((added["change"], added["before_value"], added["after_value"]),
                         ("FORMAL_ADDED", "", "0.00"))
        removed, = _census_delta(build(known), build(common))
        self.assertEqual(removed["change"], "FORMAL_RETURNED")
        rekeyed, = _census_delta(build(known), build({**known, "release_id": "release-2", "selection_id": "new"}))
        self.assertEqual(rekeyed["change"], "PROVENANCE_RECEIPT_CHANGED")

    def test_comparability_gain_counts_unique_keys_and_records_returns(self):
        def build(cells):
            return ResearchBuild(
                {}, "", {}, {
                    "criteria_by_project": cells, "policy_version": "synthetic-v1", "policy_sha256": "a" * 64,
                }, {}, {}, (), (),
            )
        before = build({PROJECT: {"R1": KNOWN, "S1": UNKNOWN}, OTHER: {"R1": UNKNOWN, "S1": UNKNOWN}})
        after = build({PROJECT: {"R1": UNKNOWN, "S1": KNOWN}, OTHER: {"R1": KNOWN, "S1": UNKNOWN}})
        delta = _comparability_delta(before, after)
        self.assertEqual(delta["comparison_counts"], {"ADDED": 2, "RETURNED": 1, "UNCHANGED": 1})
        self.assertEqual(delta["net_comparable_project_criteria"], 1)
        self.assertEqual(delta["count_by_criterion"], {
            "R1": {"before": 1, "after": 1, "net": 0}, "S1": {"before": 0, "after": 1, "net": 1},
        })

    def test_comparable_criterion_rejects_missing_temporal_proof_and_unknown_zero(self):
        receipt = {"observation_release_id": "release", "research_cutoff": "2026-11-28"}
        observations = {"observation": {"project_id": PROJECT, "scope_id": "scope", "source_sha256": "f" * 64}}
        lineage = {
            "release_id": "release", "selection_id": "selection", "validation_id": "validated",
            "observation_id": "observation", "source_sha256": "f" * 64, "scope_id": "scope", "anchor_id": "anchor",
            "project_id": PROJECT, "temporal_decision": {
                "source_sha256": "f" * 64, "cutoff": receipt["research_cutoff"], "status": "ELIGIBLE",
                "decision_id": "e" * 64, "matching_evidence_ids": [],
            },
        }
        eligible = {**KNOWN, "unit": "kt TREO", "scientific_context": {"scope_id": "scope"}, "lineage": [lineage]}
        with self.assertRaisesRegex(ValueError, "unverified evidence"):
            _checked_criterion(PROJECT, "R1", eligible, receipt, observations)
        lineage["temporal_decision"]["matching_evidence_ids"] = ["d" * 64]
        _checked_criterion(PROJECT, "R1", eligible, receipt, observations)
        with self.assertRaisesRegex(ValueError, "Unavailable criterion has a value"):
            _checked_criterion(PROJECT, "R1", {**UNKNOWN, "accepted_value": "0"}, receipt, observations)

    def test_panel_recomputes_same_scores_and_blocks_rank_for_incomplete_cohort(self):
        criteria = {PROJECT: {"R1": {**KNOWN, "utility": 0.2}, "S1": {**KNOWN, "utility": 0.8}},
                    OTHER: {"R1": {**KNOWN, "utility": 0.65}, "S1": {**KNOWN, "utility": 0.5}}}
        codes = ["R1", "S1"]
        domains = {"R1": "Resource", "S1": "Strategic"}
        weights = {"R1": 0.5, "S1": 0.5}
        policy = {
            "criteria": {code: {"domain": domain} for code, domain in domains.items()},
            "panels": {"panel": codes}, "minimum_cohort": 2, "promethee_threshold": 0.2,
            "scenarios": {"balanced": {"Resource": 0.5, "Strategic": 0.5}},
        }
        matrix = {project: {code: values[code]["utility"] for code in codes} for project, values in criteria.items()}
        panel = {
            "criterion_codes": codes, "eligible_projects": sorted(matrix), "benefit_matrix": matrix,
            "project_exclusions": {}, "scenario_weights": {"balanced": weights},
        }
        rankings = {
            method: {project: asdict(score) for project, score in scores.items()}
            for method, scores in rank_panel(matrix, weights, 0.2).items()
        }
        sensitivity = [{
            "type": kind, "removed": removed, "projects": {project: asdict(change) for project, change in changes.items()},
        } for (kind, removed), changes in leave_one_out(matrix, weights, domains, 0.2).items()]
        result = {
            "status": "PASS", "eligible_count": 2, "minimum_cohort": 2,
            "point_rankings": {"balanced": rankings}, "leave_one_out": {"balanced": sensitivity},
        }
        _checked_panel(panel, result, criteria, policy, "panel")
        fabricated = deepcopy(result)
        fabricated["point_rankings"]["balanced"]["saw"][PROJECT]["score"] = 0.999
        with self.assertRaisesRegex(ValueError, "disagree with recomputation"):
            _checked_panel(panel, fabricated, criteria, policy, "panel")
        swapped = {**panel, "scenario_weights": {"balanced": {"R1": 0.6, "S1": 0.4}}}
        with self.assertRaisesRegex(ValueError, "weights diverge"):
            _checked_panel(swapped, result, criteria, policy, "panel")
        criteria[OTHER]["S1"] = UNKNOWN
        incomplete = {**panel, "eligible_projects": [PROJECT], "benefit_matrix": {PROJECT: matrix[PROJECT]},
                      "project_exclusions": {OTHER: {"S1": UNKNOWN["reason"]}}}
        with self.assertRaisesRegex(ValueError, "must contain no rankings"):
            _checked_panel(incomplete, {**result, "status": "INSUFFICIENT_COHORT", "eligible_count": 1},
                           criteria, policy, "panel")

    def test_fixed_cohort_separates_input_stability_from_membership_effect(self):
        codes = ["R1", "S1"]
        policy = {"criteria": {"R1": {"domain": "Resource"}, "S1": {"domain": "Strategic"}},
                  "promethee_threshold": 0.2}
        weights = {"balanced": {"R1": 0.5, "S1": 0.5}}
        matrix = {"A": {"R1": 0.2, "S1": 0.8}, "B": {"R1": 0.7, "S1": 0.4}}

        def bundle(rows):
            rankings, sensitivity = _score_panel(rows, weights, codes, policy)
            return {"benefit_matrix": rows, "criterion_codes": codes, "scenario_weights": weights}, {
                "status": "PASS", "point_rankings": rankings, "leave_one_out": sensitivity,
            }

        old, before = bundle(matrix)
        new, after = bundle({**matrix, "C": {"R1": 0.95, "S1": 0.45}})
        report = _fixed_cohort(old, new, before, after, policy)
        self.assertEqual(report["current_benefit_matrix"], matrix)
        self.assertEqual(report["status"], "PASS")
        self.assertTrue(all(not rows for rows in report["same_cohort_input_effect"].values()))
        impact = {(row["project_id"], row["method"]): row for row in report["cohort_membership_effect"]["ranking_changes"]}
        self.assertEqual(impact["B", "promethee"]["before"], {"score": 0, "rank": 1.5})
        self.assertAlmostEqual(impact["B", "promethee"]["after"]["score"], -0.3125)
        self.assertEqual(impact["B", "saw"]["before"]["score"], impact["B", "saw"]["after"]["score"])
        self.assertEqual(impact["B", "saw"]["after"]["rank"], 2)
        self.assertTrue(report["cohort_membership_effect"]["sensitivity_changes"])
        _verify_effects(report, after)
        tampered = deepcopy(report)
        tampered["cohort_membership_effect"]["ranking_changes"].pop()
        with self.assertRaisesRegex(ValueError, "cohort effect differs"):
            _verify_effects(tampered, after)
        restricted, result = bundle({"A": matrix["A"], "C": new["benefit_matrix"]["C"]})
        blocked = _fixed_cohort(old, restricted, before, result, policy)
        self.assertEqual((blocked["status"], blocked["missing_current_projects"]), ("BLOCKED", ["B"]))
        self.assertNotIn("current_point_rankings", blocked)

    def test_independent_decimal_three_case_oracle(self):
        matrix = {"A": {"R1": 0.2, "S1": 0.8}, "B": {"R1": 0.7, "S1": 0.4},
                  "C": {"R1": 0.95, "S1": 0.45}}
        weights = {"R1": 0.5, "S1": 0.5}
        domains = {"R1": "Resource", "S1": "Strategic"}
        scored, sensitivity = decimal_scenario(matrix, weights, domains, 0.2)
        self.assertEqual(scored["promethee"]["B"], {"score": Decimal("-0.3125"), "rank": 3})
        self.assertEqual(scored["saw"]["C"], {"score": Decimal("0.70"), "rank": 1})
        self.assertEqual(sensitivity["domain", "Resource"]["A"], {"rank": 1, "rank_change": -1})
        actual = {method: {project: asdict(point) for project, point in points.items()}
                  for method, points in rank_panel(matrix, weights, 0.2).items()}
        _match(actual, scored)
        actual["topsis"]["A"]["score"] += 0.001
        with self.assertRaisesRegex(ValueError, "numerical result differs"):
            _match(actual, scored)

    def test_independent_ore_percentage_formula_checks_both_precision_bounds(self):
        tool_path = Path(__file__).resolve().parents[1] / "tools"
        with patch.object(sys, "path", [str(tool_path), *sys.path]):
            verify = runpy.run_path(str(tool_path / "verify_scientific_yield.py"))["verify_formula"]
        scope = {"project_id": PROJECT, "statement_id": "statement", "scope_id": "scope", "source_sha": "a" * 64}
        selected = []
        proofs = []
        for identifier, variable, amount, unit, bounds in (
            ("ore", "ore_mt", "56", "Mt", ["55.5", "56.5"]),
            ("grade", "grade", "2.6", "%", ["2.55", "2.65"]),
        ):
            selected.append({"raw": {**scope, "observation_id": identifier, "native_raw_id": None,
                                     "variable": variable, "raw_value": amount, "raw_unit": unit},
                             "normalized": {"raw_record_id": None, "value": amount, "unit": unit}})
            proofs.append({"variable": variable, "raw_token": amount, "raw_unit": unit, "normalized_unit": unit,
                           "normalized_value": amount, "normalized_rounding_interval": bounds})
        value = {"project_id": PROJECT, "value": "1456", "input_observation_ids": ["ore", "grade"],
                 "assessment": {"status": "APPROVED", "formula_id": "contained-treo-from-mt-pct-v1",
                                "statement_id": "statement", "scope_id": "scope", "source_sha256": "a" * 64,
                                "inputs": proofs, "rounding_interval": ["1415.25", "1497.25"]}}
        self.assertEqual(verify(value, selected)["rounding_interval"], ["1415.25", "1497.25"])
        value["assessment"]["rounding_interval"][1] = "1497.26"
        with self.assertRaisesRegex(ValueError, "formula value or interval differs"):
            verify(value, selected)

    def test_replay_pass_requires_actual_rebuilt_bytes(self):
        with TemporaryDirectory() as folder:
            run = Path(folder)
            outputs = {}
            for name in ARTIFACTS:
                path = run / "rebuilt" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                payload = ("synthetic:" + name).encode()
                path.write_bytes(payload)
                outputs[name] = sha256(payload).hexdigest()
            report = {
                "release_id": "release", "offline": True, "F4": "PASS", "status": "PASS", "reason": "TEST",
                "expected": outputs, "actual": outputs, "differences": [], "build_receipt_difference_fields": [],
                "compared_artifacts": len(outputs),
            }
            (run / "replay_receipt.json").write_text(json.dumps(report), encoding="utf-8")
            current = ResearchBuild({"scientific_artifacts": outputs}, "", {}, {}, {}, {}, (), ())
            result = _replay_readback(run, current, "release")
            self.assertEqual(result["verified_output_count"], len(outputs))
            (run / "rebuilt" / ARTIFACTS[0]).write_bytes(b"purported replay output")
            with self.assertRaisesRegex(ValueError, "does not match"):
                _replay_readback(run, current, "release")
