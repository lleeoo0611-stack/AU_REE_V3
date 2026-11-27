"""Verify Fresh Census grid accounting and the formal evidence boundary."""

import csv
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import ContractViolation
from au_ree_v3.fresh_census import CensusAxes, build_census, verify_census
from au_ree_v3.observations import CATEGORY
from au_ree_v3.store import Store
import test_store as fixtures

PROFILE = Path(__file__).resolve().parents[1] / "config/research_profiles/au_ree_2026-11-28.json"
RESOURCE_HEADING = "TREO品位_pct"


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def _seed_release(store, project_id, cutoff, *, mode="FORMAL", variants=(("grade", "0.0000", "estimate-a"),),
                  category=CATEGORY, chemical_basis="TREO", grade_unit="%"):
    project = replace(fixtures.PROJECT, entity_id=project_id)
    scope = replace(fixtures.SCOPE, project_id=project_id, deposit_id=None)
    proof = fixtures.TEMPORAL_EVIDENCE
    entries = []
    for index, (variable, value, statement) in enumerate(variants):
        raw = replace(
            fixtures.RAW, observation_id=f"synthetic-obs-{index}", native_raw_id=None, project_id=project_id,
            deposit_id=None, variable=variable, material_basis="WHOLE_ORE", statement_id=statement,
            category=category, chemical_basis=chemical_basis,
            denominator_basis="WHOLE_ORE" if variable == "grade" else None,
            raw_value=value, raw_unit=grade_unit if variable == "grade" else "Mt",
        )
        candidate = replace(
            fixtures.CANDIDATE, candidate_id=f"synthetic-candidate-{index}",
            observation_id=raw.observation_id, unit=raw.raw_unit, value=value,
            material_basis=raw.material_basis, denominator_basis=raw.denominator_basis,
            chemical_basis=raw.chemical_basis,
        )
        decision = fixtures.evidence_decision(raw, cutoff)
        validation = replace(
            fixtures.VALIDATION, validation_id=f"synthetic-validation-{index}", candidate_id=candidate.candidate_id,
            cutoff=cutoff, temporal_decision_id=decision.decision_id,
        )
        accepted = replace(
            fixtures.ACCEPTED, acceptance_id=f"synthetic-selection-{index}",
            validation_id=validation.validation_id, selection_key=f"{project_id}:{variable}",
        )
        entries.append((raw, candidate, validation, accepted))
    release = replace(
        fixtures.RELEASE, cohort=(project_id,), cutoff=cutoff, release_mode=mode, validation_status="PASS",
        result_fingerprints={"fixture": "a" * 64}, temporal_evidence_ids=(proof.evidence_id,),
        temporal_decision_ids=tuple(sorted({item[2].temporal_decision_id for item in entries})),
    )
    with store.transaction():
        store.add_source(fixtures.SOURCE, fixtures.CONTENT)
        for item in (project, scope, fixtures.ANCHOR, release):
            store.add(item)
        for item in entries:
            for record in item:
                store.add(record)
    return release


class FreshCensusTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.axes = CensusAxes.load(PROFILE)

    def test_fixed_grid_keeps_identity_separate_from_fresh_scientific_values(self):
        with TemporaryDirectory() as scratch:
            out = Path(scratch)
            searches = {self.axes.project_ids[0]: "NO_SOURCE_FOUND"}
            result = build_census(PROFILE, out, project_statuses=searches)
            self.assertEqual(result, verify_census(PROFILE, out, project_statuses=searches))
            self.assertEqual((result["projects"], result["columns"], result["cells"]), (93, 51, 4743))
            self.assertEqual(result["cell_states"], {"PRESENT": 93, "UNKNOWN": 4650, "NOT_APPLICABLE": 0})
            self.assertEqual(result["formal_scientific_cells"], 0)
            cells = read_csv(out / "cell_status.csv")
            self.assertEqual(tuple(cells[0]), tuple(cells[-1]))
            self.assertEqual(cells[0]["value_origin"], "IDENTITY")
            self.assertEqual(cells[0]["registry_sha256"], result["registry_sha256"])
            self.assertEqual(cells[1]["source_search_status"], "NO_SOURCE_FOUND")
            self.assertEqual(cells[1]["value"], "")
            self.assertEqual(cells[1]["value_state"], "UNKNOWN")

    def test_formally_selected_zero_retains_exact_lineage(self):
        proof = fixtures.TEMPORAL_EVIDENCE
        with TemporaryDirectory() as scratch, Store(":memory:", trusted_temporal_evidence_ids=(proof.evidence_id,)) as store:
            project = self.axes.project_ids[0]
            release = _seed_release(store, project, self.axes.cutoff)
            out = Path(scratch)
            result = build_census(PROFILE, out, store=store, release_id=release.release_id)
            self.assertEqual(result["formal_scientific_cells"], 1)
            self.assertEqual(result["cell_states"], {"PRESENT": 94, "UNKNOWN": 4649, "NOT_APPLICABLE": 0})
            rows = read_csv(out / "cell_status.csv")
            cell = next(row for row in rows if row["project_id"] == project and row["column"] == RESOURCE_HEADING)
            self.assertEqual(cell["value"], "0.0000")
            self.assertEqual(cell["source_sha256"], fixtures.SOURCE.sha256)
            self.assertEqual(cell["selection_id"], "synthetic-selection-0")
            self.assertEqual(cell["observation_id"], "synthetic-obs-0")
            self.assertEqual(cell["temporal_decision_id"], release.temporal_decision_ids[0])
            self.assertEqual(cell["temporal_status"], "ELIGIBLE")
            self.assertEqual(cell["comparable"], "YES")

    def test_experimental_selections_remain_unknown(self):
        with TemporaryDirectory() as scratch, Store(":memory:") as store:
            project = self.axes.project_ids[0]
            release = _seed_release(store, project, self.axes.cutoff, mode="EXPERIMENTAL")
            result = build_census(PROFILE, Path(scratch), store=store, release_id=release.release_id)
            self.assertEqual(result["formal_scientific_cells"], 0)
            self.assertEqual(result["unknown_reasons"]["SELECTION_NOT_FORMAL"], 1)

    def test_selected_grade_ppm_projects_to_decimal_percent_with_original_token(self):
        proof = fixtures.TEMPORAL_EVIDENCE
        with TemporaryDirectory() as scratch, Store(
            ":memory:", trusted_temporal_evidence_ids=(proof.evidence_id,)
        ) as store:
            project = self.axes.project_ids[0]
            out = Path(scratch)
            release = _seed_release(
                store, project, self.axes.cutoff, variants=(("grade", "470.0", "estimate-a"),),
                grade_unit="ppm",
            )
            result = build_census(PROFILE, out, store=store, release_id=release.release_id)
            self.assertEqual(result["formal_scientific_cells"], 1)
            cell = next(row for row in read_csv(out / "cell_status.csv")
                        if row["project_id"] == project and row["column"] == RESOURCE_HEADING)
            self.assertEqual((cell["value"], cell["unit"]), ("0.04700", "%"))
            self.assertEqual((cell["raw_value"], cell["raw_unit"]), ("470.0", "ppm"))
            self.assertEqual(cell["reason"], "GOVERNED_SELECTION_EXACT_UNIT_CONVERSION")

    def test_incompatible_formal_statement_components_are_isolated(self):
        proof = fixtures.TEMPORAL_EVIDENCE
        with TemporaryDirectory() as scratch, Store(":memory:", trusted_temporal_evidence_ids=(proof.evidence_id,)) as store:
            variants = (("grade", "0.20", "estimate-a"), ("ore_mt", "40.10", "estimate-b"))
            release = _seed_release(store, self.axes.project_ids[0], self.axes.cutoff, variants=variants)
            result = build_census(PROFILE, Path(scratch), store=store, release_id=release.release_id)
            self.assertEqual(result["formal_scientific_cells"], 0)
            self.assertEqual(result["unknown_reasons"]["CONFLICTING_SELECTED_CONTEXT"], 2)

    def test_resource_column_excludes_other_reported_category_and_chemical_basis(self):
        proof = fixtures.TEMPORAL_EVIDENCE
        for fields in ({"category": "ORE_RESERVE"}, {"chemical_basis": "TREO+Sc2O3"}):
            with self.subTest(fields=fields), TemporaryDirectory() as scratch, Store(
                ":memory:", trusted_temporal_evidence_ids=(proof.evidence_id,)
            ) as store:
                release = _seed_release(store, self.axes.project_ids[0], self.axes.cutoff, **fields)
                result = build_census(PROFILE, Path(scratch), store=store, release_id=release.release_id)
                self.assertEqual(result["formal_scientific_cells"], 0)
                self.assertEqual(result["unknown_reasons"]["NOT_COMPARABLE_FOR_CENSUS_COLUMN"], 1)

    def test_overwriting_unknown_with_zero_or_unproven_not_applicable_fails(self):
        with TemporaryDirectory() as scratch:
            out = Path(scratch)
            build_census(PROFILE, out)
            ledger = out / "cell_status.csv"
            initial = ledger.read_text(encoding="utf-8")
            ledger.write_text(initial.replace(",UNKNOWN,", ",NOT_APPLICABLE,", 1), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "matching evidence or state"):
                verify_census(PROFILE, out)
            ledger.write_text(initial, encoding="utf-8")
            path = out / "census.csv"
            rows = read_csv(path)
            rows[0][RESOURCE_HEADING] = "0"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=tuple(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaisesRegex(ValueError, "ledger value disagree"):
                verify_census(PROFILE, out)

    def test_release_cutoff_must_equal_profile_cutoff(self):
        with TemporaryDirectory() as scratch, Store(":memory:") as store:
            release = _seed_release(store, self.axes.project_ids[0], "2026-11-25", mode="EXPERIMENTAL")
            with self.assertRaisesRegex(ContractViolation, "profile cutoff"):
                build_census(PROFILE, Path(scratch), store=store, release_id=release.release_id)
