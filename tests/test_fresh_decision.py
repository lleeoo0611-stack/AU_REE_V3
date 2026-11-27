"""Independently check decision rules on release-bound synthetic observations."""

from dataclasses import replace
from hashlib import sha256
import json
from math import log1p
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import (
    AcceptedResearchValue, ContractViolation, Entity, EvidenceAnchor, NormalizedObservation,
    RawObservation, ResearchRelease, ResourceScope, Source, ValidatedObservation,
)
from au_ree_v3.contracts.temporal import TEMPORAL_POLICY_VERSION, TemporalEvidence, evaluate_temporal
from au_ree_v3.comparability import MREO_BASIS, MREO_VARIABLE, OXIDE_VARIABLES
from au_ree_v3.decision import average_ranks, rank_panel
from au_ree_v3.fresh_decision import build_decision, load_policy
from au_ree_v3.store import Store

PROJECT_IDS = ("AU-" + "1" * 20, "AU-" + "2" * 20, "AU-" + "3" * 20)
CUTOFF = "2026-11-28"
RULES_SHA = "a" * 64


def _write(path, data):
    content = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
    path.write_bytes(content)
    return {"path": path.name, "sha256": sha256(content).hexdigest()}


def _profile(folder):
    registry = _write(folder / "registry.json", {
        "schema_version": "project-registry-v1",
        "projects": [
            {"project_id": pid, "name": f"Synthetic deposit {index}", "state": "WA",
             "developer": None, "listing_code": None, "identity_links": []}
            for index, pid in enumerate(PROJECT_IDS, 1)
        ],
    })
    path = folder / "research.json"
    _write(path, {
        "schema_version": "fresh-research-baseline-v1",
        "profile_id": "synthetic-fresh-decision",
        "research_cutoff": CUTOFF,
        "timezone": "Asia/Taipei",
        "project_count": len(PROJECT_IDS),
        "census_column_count": 1,
        "inputs": {
            "project_registry": registry,
            "source_seed_catalog": _write(folder / "source.json", {"schema_version": "source-seed-catalog-v1"}),
            "census_columns": _write(folder / "columns.json", {
                "schema_version": "census-column-contract-v1", "columns": ["專案ID"],
            }),
        },
    })
    return path


def _source(pid):
    content = f"Synthetic exact-edition {pid}".encode()
    source = Source(
        source_key=pid, url=f"https://example.org/{pid}", sha256=sha256(content).hexdigest(),
        kind="pdf", source_version="fixture-v1", publication_date="2026-08-17",
        available_date="2026-08-19", acquired_at="2026-11-29T02:00:00+08:00",
    )
    proof = TemporalEvidence(
        source_sha256=source.sha256, kind="ARCHIVED_PUBLIC_CAPTURE",
        public_on="2026-08-19", reference=f"synthetic proof {pid}",
        reference_sha256=sha256(f"proof {pid}".encode()).hexdigest(),
    )
    return source, content, proof


def _observation(project, scope, source, anchor, proof, variable, value, unit, basis, *,
                 shift_composition, wrong_definition=False, different_cutoff=False,
                 different_treo=False):
    identifier = f"{project.entity_id}:{variable}"
    statement = scope.scope_id + ":statement"
    payload = {
        "reporting_standard": "JORC 2012", "cutoff_grade": "0.15",
        "cutoff_unit": "%", "cutoff_analyte": "TREO",
        "statement_context": [{
            "physical_page": 2,
            "quote": ("TREO = Total Rare Earth Oxides (La2O3, CeO2, Pr6O11, Nd2O3, Sm2O3, "
                      "Eu2O3, Gd2O3, Tb4O7, Dy2O3, Ho2O3, Er2O3, Tm2O3, Yb2O3, Lu2O3, Y2O3)"),
        }],
    }
    if variable == MREO_VARIABLE:
        declared = list(OXIDE_VARIABLES)
        if wrong_definition:
            declared[-1] = "Sc2O3"
        payload.update(oxide_species=list(OXIDE_VARIABLES), oxide_definition={
            "page": anchor.page, "quote": f"MREO = Magnet Rare Earth Oxide({', '.join(declared)})",
        })
    if different_cutoff:
        payload["cutoff_grade"] = "0.20"
    if different_treo:
        payload["statement_context"][0]["quote"] = payload["statement_context"][0]["quote"].replace("Y2O3", "Sc2O3")
    raw = RawObservation(
        observation_id=identifier, project_id=project.entity_id, scope_id=scope.scope_id,
        source_key=source.source_key, source_sha=source.sha256, variable=variable,
        raw_value=value, raw_unit=unit, status="REPORTED", transform_version="synthetic-v1",
        original_payload=payload,
        anchor_id=anchor.anchor_id, material_basis="WHOLE_ORE", chemical_basis=basis,
        denominator_basis=("WHOLE_ORE" if variable in {"grade", MREO_VARIABLE, *OXIDE_VARIABLES.values()} else None),
        statement_id=statement + ":other" if shift_composition else statement,
        category="MEASURED_INDICATED_INFERRED_TOTAL", effective_date="2026-06-30",
        reported_publication_date=source.publication_date,
    )
    candidate = NormalizedObservation(
        candidate_id=identifier + ":candidate", observation_id=raw.observation_id, value=value,
        unit=unit, material_basis=raw.material_basis, chemical_basis=raw.chemical_basis,
        denominator_basis=raw.denominator_basis,
        transform_version="synthetic-v1", raw_record_id=raw.raw_record_id,
    )
    decision = evaluate_temporal(
        source_sha256=source.sha256, cutoff=CUTOFF, publication_date=source.publication_date,
        available_date=source.available_date, acquired_at=source.acquired_at,
        effective_date=raw.effective_date, reported_publication_date=raw.reported_publication_date,
        evidence=(proof,),
    )
    validation = ValidatedObservation(
        validation_id=identifier + ":valid", candidate_id=candidate.candidate_id,
        cutoff=CUTOFF, rules_sha=RULES_SHA, outcome="VALIDATED",
        reason="synthetic source-bound evidence", evidence_anchor_ids=(anchor.anchor_id,),
        comparable=True, temporal_decision_id=decision.decision_id, temporal_evidence=(proof,),
    )
    return raw, candidate, validation


def _seed(store, sources, *, mismatched_composition=False, experimental=False,
          invalid_inventory=False, omit_component=False, opaque_composition=False,
          reported_mreo=False, wrong_mreo_definition=False, missing_mreo_unit=False,
          different_cutoff=False, different_treo=False):
    validations = []
    with store.transaction():
        for i, pid in enumerate(PROJECT_IDS[:2]):
            source, content, proof = sources[pid]
            project = Entity(entity_id=pid, native_namespace="synthetic-registry", native_id=str(i),
                             name=f"Project {i + 1}", kind="PROJECT")
            scope = ResourceScope(scope_id=pid + ":scope", name="Synthetic JORC resource", project_id=pid)
            anchor = EvidenceAnchor(anchor_id=pid + ":page", source_key=source.source_key,
                                    source_sha=source.sha256, page=2, locator="page 2, table 1")
            store.add_source(source, content)
            for record in (project, scope, anchor):
                store.add(record)
            ore, grade, inventory = (("100.0", "2.00", "2000") if i == 0
                                     else ("50.0", "1.0", "500"))
            if invalid_inventory and i == 0:
                inventory = "3000"
            measurements = (
                ("ore_mt", ore, "Mt", "TREO"), ("grade", grade, "%", "TREO"),
                ("contained_treo_kt", inventory, "kt TREO", "TREO"),
            )
            oxides = ("1000", "5000", "1000", "1000") if i == 0 else ("200", "400", "300", "100")
            if reported_mreo and i == 1:
                measurements += ((MREO_VARIABLE, "1000", "ppm", MREO_BASIS),)
            elif not opaque_composition:
                measurements += tuple(
                    (key, value, "ppm", species)
                    for (species, key), value in zip(OXIDE_VARIABLES.items(), oxides, strict=True)
                    if not (omit_component and i == 0 and species == "Tb4O7")
                )
            else:
                measurements += (("magnet_reo_share_pct", "40", "%", "PrNdDyTb_REO_DIV_TREO"),)
            for variable, value, unit, basis in measurements:
                evidence_anchor = anchor
                if variable == MREO_VARIABLE and not missing_mreo_unit:
                    evidence_anchor = replace(anchor, anchor_id=anchor.anchor_id + ":mreo",
                                              column="MREO", quote=f"MREO {value} ppm")
                    store.add(evidence_anchor)
                raw, candidate, validation = _observation(
                    project, scope, source, evidence_anchor, proof, variable, value, unit, basis,
                    shift_composition=mismatched_composition and i == 0 and variable == "dy2o3_ppm",
                    wrong_definition=wrong_mreo_definition,
                    different_cutoff=different_cutoff and i == 0 and variable == "nd2o3_ppm",
                    different_treo=different_treo and i == 0 and variable == "grade",
                )
                for record in (raw, candidate, validation):
                    store.add(record)
                validations.append(validation)
        release = ResearchRelease(
            release_id="synthetic-fresh-release", input_sha="b" * 64, code_commit="c" * 40,
            cutoff=CUTOFF, cohort=PROJECT_IDS[:2], rules_sha=RULES_SHA, validation_status="PASS",
            release_mode="EXPERIMENTAL" if experimental else "FORMAL",
            result_fingerprints={"synthetic": "d" * 64},
            temporal_policy_version=TEMPORAL_POLICY_VERSION,
            temporal_decision_ids=tuple(sorted({item.temporal_decision_id for item in validations})),
            temporal_evidence_ids=tuple(sorted(proof.evidence_id for _, _, proof in sources.values())),
        )
        store.add(release)
        for validation in validations:
            store.add(AcceptedResearchValue(
                acceptance_id=validation.validation_id + ":acceptance", validation_id=validation.validation_id,
                release_id=release.release_id, selection_key=validation.candidate_id,
                reason="synthetic declared selection",
            ))
    return release


class FreshDecisionTests(unittest.TestCase):
    def setUp(self):
        work = TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        self.profile = _profile(self.root)
        self.sources = {pid: _source(pid) for pid in PROJECT_IDS[:2]}
        self.approved = tuple(proof.evidence_id for _, _, proof in self.sources.values())

    def test_pinned_method_scope(self):
        policy = load_policy()
        self.assertEqual(policy["panels"]["issuer_resource_composition"], ["R1", "S1"])
        self.assertEqual(policy["s1_admission"]["basis"], ["PrNdDyTb_REO_DIV_TREO"])
        self.assertEqual(policy["criteria"]["R1"]["high"], 5000)
        self.assertEqual(policy["promethee_threshold"], 0.2)
        self.assertEqual(policy["minimum_cohort"], 2)

    def test_formal_input_build_computes_complete_case_rankings(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources)
            report = build_decision(self.profile, self.root / "first", store=store, release_id=release.release_id)
            repeated = build_decision(self.profile, self.root / "second", store=store, release_id=release.release_id)
        self.assertEqual(report, repeated)
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["comparable_project_criteria"], 4)
        inputs = json.loads((self.root / "first/mcda_inputs.json").read_bytes())
        results = json.loads((self.root / "first/mcda_results.json").read_bytes())
        matrix = inputs["panels"]["issuer_resource_composition"]["benefit_matrix"]
        self.assertEqual(set(matrix), set(PROJECT_IDS[:2]))
        self.assertAlmostEqual(matrix[PROJECT_IDS[0]]["R1"], log1p(2000) / log1p(5000))
        self.assertEqual(matrix[PROJECT_IDS[0]]["S1"], 0.4)
        evidence = inputs["criteria_by_project"][PROJECT_IDS[0]]["R1"]["lineage"]
        self.assertEqual(len(evidence), 3)
        self.assertTrue(all(row["source_sha256"] == self.sources[PROJECT_IDS[0]][0].sha256 for row in evidence))
        composition = inputs["criteria_by_project"][PROJECT_IDS[0]]["S1"]
        self.assertEqual(len(composition["derivation"]["input_observation_ids"]), 5)
        self.assertEqual(len(composition["lineage"]), 7)
        self.assertEqual(len(composition["scientific_context"]["treo_denominator"]["chemical_species"]), 15)
        panel = results["panels"]["issuer_resource_composition"]
        for scores in panel["point_rankings"].values():
            for method in ("saw", "topsis", "promethee"):
                self.assertEqual(scores[method][PROJECT_IDS[0]]["rank"], 1)
                self.assertEqual(scores[method][PROJECT_IDS[1]]["rank"], 2)
        self.assertEqual(len(panel["leave_one_out"]["resource_assumption"]), 4)
        self.assertEqual(inputs["criteria_by_project"][PROJECT_IDS[2]]["R1"]["state"], "UNKNOWN")
        self.assertEqual(results["panels"]["six_domain"]["point_rankings"], {})

    def test_different_resource_statement_leaves_panel_insufficient(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, mismatched_composition=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        results = json.loads((self.root / "work/mcda_results.json").read_bytes())
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["comparable_project_criteria"], 3)
        self.assertEqual(report["panels"]["issuer_resource_composition"]["eligible_count"], 1)
        missing = inputs["criteria_by_project"][PROJECT_IDS[0]]["S1"]
        self.assertEqual(missing["reason"], "COMPOSITION_SCOPE_STATEMENT_OR_EDITION_MISMATCH")
        self.assertIsNone(missing["utility"])
        self.assertEqual(results["panels"]["issuer_resource_composition"]["point_rankings"], {})

    def test_reported_contained_conflict_is_never_scored(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, invalid_inventory=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(report["status"], "BLOCKED")
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        self.assertEqual(inputs["criteria_by_project"][PROJECT_IDS[0]]["R1"]["reason"],
                         "REPORTED_CONTAINED_OUTSIDE_ROUNDING_INTERVAL")

    def test_unreported_constituent_cannot_be_interpreted_as_zero(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, omit_component=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["panels"]["issuer_resource_composition"]["eligible_count"], 1)
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        cell = inputs["criteria_by_project"][PROJECT_IDS[0]]["S1"]
        self.assertEqual(cell["reason"], "NO_COMPOSITION_SELECTION")
        self.assertIsNone(cell["accepted_value"])

    def test_opaque_magnet_ratio_cannot_supply_formula_proof(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, opaque_composition=True)
            result = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(result["comparable_project_criteria"], 2)
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        self.assertEqual(inputs["criteria_by_project"][PROJECT_IDS[0]]["S1"]["reason"],
                         "COMPOSITION_FORMULA_EVIDENCE_MISSING")

    def test_exact_reported_four_oxide_aggregate_remains_a_measured_numerator(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, reported_mreo=True)
            result = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(result["panels"]["issuer_resource_composition"]["eligible_count"], 2)
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        composition = inputs["criteria_by_project"][PROJECT_IDS[1]]["S1"]
        derivation = composition["derivation"]
        self.assertEqual(composition["accepted_value"], "10.0")
        self.assertEqual(derivation["component_tokens"], {"MREO": "1000"})
        self.assertEqual(len(derivation["input_observation_ids"]), 2)
        self.assertIn("Tb4O7", derivation["reported_definition"]["quote"])

    def test_wrong_reported_oxide_definition_is_not_accepted(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, reported_mreo=True, wrong_mreo_definition=True)
            result = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(result["panels"]["issuer_resource_composition"]["status"], "INSUFFICIENT_COHORT")
        inputs = json.loads((self.root / "work/mcda_inputs.json").read_bytes())
        self.assertEqual(inputs["criteria_by_project"][PROJECT_IDS[1]]["S1"]["reason"],
                         "MREO_DEFINITION_OR_WHOLE_ORE_BASIS_UNBOUND")

    def test_missing_mreo_unit_evidence_holds_the_aggregate(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, reported_mreo=True, missing_mreo_unit=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(report["panels"]["issuer_resource_composition"]["eligible_count"], 1)
        cells = json.loads((self.root / "work/mcda_inputs.json").read_bytes())["criteria_by_project"]
        self.assertEqual(cells[PROJECT_IDS[1]]["S1"]["reason"], "MREO_UNIT_OR_COMPANION_CONTEXT_UNBOUND")

    def test_matching_statement_id_does_not_hide_cutoff_conflict(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, different_cutoff=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(report["panels"]["issuer_resource_composition"]["eligible_count"], 1)
        cells = json.loads((self.root / "work/mcda_inputs.json").read_bytes())["criteria_by_project"]
        self.assertEqual(cells[PROJECT_IDS[0]]["S1"]["reason"], "COMPOSITION_SCOPE_STATEMENT_OR_EDITION_MISMATCH")

    def test_wrong_treo_oxide_set_blocks_an_otherwise_linked_numerator(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, different_treo=True)
            report = build_decision(self.profile, self.root / "work", store=store, release_id=release.release_id)
        self.assertEqual(report["panels"]["issuer_resource_composition"]["eligible_count"], 1)
        cells = json.loads((self.root / "work/mcda_inputs.json").read_bytes())["criteria_by_project"]
        self.assertEqual(cells[PROJECT_IDS[0]]["S1"]["reason"], "COMPOSITION_TREO_CHEMICAL_DEFINITION_UNBOUND")

    def test_absent_release_or_experimental_store_never_yields_ranks(self):
        absent = build_decision(self.profile, self.root / "absent")
        self.assertEqual(absent["reason"], "FORMAL_RELEASE_NOT_SUPPLIED")
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources, experimental=True)
            result = build_decision(self.profile, self.root / "experimental", store=store, release_id=release.release_id)
        self.assertEqual(result["reason"], "FORMAL_RELEASE_UNAVAILABLE")
        self.assertTrue(all(panel["status"] == "INSUFFICIENT_COHORT" for panel in result["panels"].values()))

    def test_release_cutoff_must_match_fresh_profile(self):
        with Store(":memory:", trusted_temporal_evidence_ids=self.approved) as store:
            release = _seed(store, self.sources)
            with store.transaction():
                store.add(replace(release, release_id="wrong-cutoff", cutoff="2026-11-13"))
            with self.assertRaisesRegex(ContractViolation, "cutoff or cohort disagrees"):
                build_decision(self.profile, self.root / "work", store=store, release_id="wrong-cutoff")

    def test_pure_solver_retains_average_ties_and_fixed_topsis_anchors(self):
        self.assertEqual(average_ranks((0.5, 0.5000000000000001, 0.5)), (2.5, 1, 2.5))
        matrix = {"a": {"x": 1.0, "y": 0.0}, "b": {"x": 0.0, "y": 1.0}}
        points = rank_panel(matrix, {"x": 1.0, "y": 1.0}, 0.2)
        self.assertEqual(points["saw"]["a"].rank, 1.5)
        self.assertEqual(points["topsis"]["a"].score, 0.5)
        self.assertEqual(points["promethee"]["a"].score, 0.0)
