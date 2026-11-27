"""Check evidence-bound research delta and replay readback behavior."""

from copy import deepcopy
from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.decision import leave_one_out, rank_panel
from au_ree_v3.research_artifacts import ARTIFACTS
from au_ree_v3.research_delta import (
    ResearchBuild, _census_delta, _checked_criterion, _checked_panel, _comparability_delta, _replay_readback,
    _same_census_quantity,
)


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
        criteria[OTHER]["S1"] = UNKNOWN
        incomplete = {**panel, "eligible_projects": [PROJECT], "benefit_matrix": {PROJECT: matrix[PROJECT]},
                      "project_exclusions": {OTHER: {"S1": UNKNOWN["reason"]}}}
        with self.assertRaisesRegex(ValueError, "must contain no rankings"):
            _checked_panel(incomplete, {**result, "status": "INSUFFICIENT_COHORT", "eligible_count": 1},
                           criteria, policy, "panel")

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
