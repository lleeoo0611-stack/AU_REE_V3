"""Fixed shadow semantic positive and rejection contracts with immutable Store lineage."""

from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import AcceptedResearchValue, ContractViolation, Entity, RawObservation, ResearchRelease
from au_ree_v3.semantics import (
    ConceptMapping, IdentityContext, ProcessContext, SemanticAssessment, SemanticContext, SemanticStatus,
    assess_semantic_use, compare_semantic_use, resolve_concept, vocabulary,
)
from au_ree_v3.store import SCHEMA_VERSION, Store
from test_store import ACCEPTED, ANCHOR, RAW, RELEASE, SOURCE, VALIDATION, persist_observation

RESOURCE = SemanticContext(
    quantity_kind="CONTAINED_OXIDE_MASS", project_id=RAW.project_id, scope_id=RAW.scope_id, deposit_id=RAW.deposit_id,
    statement_id=RAW.statement_id, resource_class="MINERAL_RESOURCE", jorc_categories=("MEASURED",),
    cutoff_grade="0.2", cutoff_unit="%", cutoff_analyte="TREO", effective_date=RAW.effective_date,
    includes_stockpiles=False, material="WHOLE_ORE", denominator_basis="ORE_MASS",
    treo_members=("Nd2O3", "Pr6O11", "Dy2O3", "Tb4O7", "Y2O3"),
    temporal_status="ELIGIBLE", temporal_decision_id=VALIDATION.temporal_decision_id, research_cutoff=VALIDATION.cutoff,
)
TECHNICAL = SemanticContext(
    quantity_kind="PROCESS_RECOVERY", project_id="project-test-1", scope_id="pilot-feed",
    material="WHOLE_ORE", denominator_basis="DRY_FEED", chemical_species=("Nd2O3", "Pr6O11"),
    temporal_status="ELIGIBLE", temporal_decision_id="5" * 64, effective_date="2025-06-01",
    process=ProcessContext(
        experiment_id="pilot-1", test_date="2025-06-01", feed="WHOLE_ORE", product="MIXED_REO",
        step="FULL_FLOWSHEET", start="ORE_FEED", end="MIXED_REO_PRODUCT", scale="PILOT",
        target_species=("Nd2O3", "Pr6O11"), procedure="declared standardized protocol",
        conditions=(("acid", "180 g/L"), ("temperature", "85 C")), sample_representativeness="replicate representative blend",
    ),
)


def evaluate(context=RESOURCE, *, purpose="R1", transforms=(), anchors=(ANCHOR.anchor_id,), ids=(RAW.observation_id,)):
    return assess_semantic_use(
        context, purpose=purpose, observation_ids=ids, source_sha256=SOURCE.sha256,
        evidence_anchor_ids=anchors, transforms=transforms,
    )


class SemanticContextTests(unittest.TestCase):
    def test_resource_class_scope_cutoff_and_chemical_unknowns_remain_separate(self):
        self.assertEqual(evaluate().status, SemanticStatus.COMPARABLE_DIRECT)
        variants = (
            ({"resource_class": "ORE_RESERVE"}, "NOT_COMPARABLE", "resource_class"),
            ({"resource_class": "EXPLORATION_TARGET"}, "NOT_COMPARABLE", "resource_class"),
            ({"material": "HEAVY_MINERAL"}, "NOT_COMPARABLE", "material"),
            ({"treo_members": ()}, "INSUFFICIENT_CONTEXT", "treo_members"),
            ({"treo_members": ("TREO",)}, "NOT_COMPARABLE", "treo_members"),
            ({"chemical_species": ("Nb2O5",), "treo_members": ()}, "NOT_COMPARABLE", "chemical_species"),
            ({"cutoff_analyte": None}, "INSUFFICIENT_CONTEXT", "cutoff_analyte"),
            ({"effective_date": None}, "INSUFFICIENT_CONTEXT", "effective_date"),
        )
        for update, status, field in variants:
            with self.subTest(update=update):
                result = evaluate(replace(RESOURCE, **update))
                self.assertEqual(result.status, status)
                self.assertIn(field, result.missing_dimensions + result.incompatible_dimensions)

    def test_same_dimension_or_same_name_does_not_merge_contexts(self):
        left = evaluate()
        right = replace(RESOURCE, project_id="another-project", scope_id="second-scope")
        self.assertEqual(compare_semantic_use(left, evaluate(right)).status, "COMPARABLE_DIRECT")
        for field, new_value in (
            ("cutoff_analyte", "TREO + Sc2O3"), ("cutoff_grade", "0.5"), ("includes_stockpiles", True),
            ("jorc_categories", ("INFERRED",)), ("treo_members", ("Nd2O3", "Sc2O3")),
        ):
            with self.subTest(field=field):
                actual = compare_semantic_use(left, evaluate(replace(right, **{field: new_value})))
                self.assertEqual(actual.status, "NOT_COMPARABLE")
                self.assertIn(field, actual.incompatible_dimensions)
        uncertain = compare_semantic_use(left, evaluate(replace(right, includes_stockpiles=None)))
        self.assertEqual(uncertain.status, "INSUFFICIENT_CONTEXT")
        self.assertIn("includes_stockpiles", uncertain.missing_dimensions)
        self.assertIn("project_id", compare_semantic_use(left, left).incompatible_dimensions)

    def test_purposes_separate_extraction_recovery_scale_and_known_context(self):
        baseline = evaluate(TECHNICAL, purpose="T1A")
        other = replace(TECHNICAL, project_id="different", scope_id="different scope")
        self.assertEqual(compare_semantic_use(baseline, evaluate(other, purpose="T1A")).status, "COMPARABLE_DIRECT")
        uncertain = replace(other, process=replace(other.process, conditions=()))
        self.assertIn("process.conditions", evaluate(uncertain, purpose="T1A").missing_dimensions)
        incompatible = replace(uncertain, quantity_kind="PROCESS_EXTRACTION", process=replace(
            uncertain.process, scale="BENCH", step="LEACHING_ONLY", end="LEACH_LIQUOR",
        ))
        compared = compare_semantic_use(baseline, evaluate(incompatible, purpose="T1A"))
        self.assertEqual(compared.status, "NOT_COMPARABLE")
        self.assertIn("process.end", compared.incompatible_dimensions)
        self.assertIn("process.conditions", compared.missing_dimensions)
        self.assertEqual(evaluate(replace(TECHNICAL, observation_role="FORECAST"), purpose="T1A").status, "NOT_COMPARABLE")
        self.assertEqual(evaluate(replace(TECHNICAL, value_state="CONFLICT"), purpose="T1A").status, "SCIENTIFIC_CONFLICT")
        self.assertEqual(evaluate(replace(TECHNICAL, quantity_kind="DRILL_CORE_RECOVERY"), purpose="T1A").status, "NOT_COMPARABLE")
        evidence = replace(TECHNICAL, quantity_kind="EVIDENCE_APPLICABILITY")
        self.assertEqual(evaluate(evidence, purpose="T1D").status, "DESCRIPTIVE_ONLY")

    def test_exact_skos_mapping_requires_evidence_and_scope(self):
        mapping = ConceptMapping(
            original="issuer metric tonnes", canonical="qudt-unit:TONNE", relation="exactMatch",
            mapping_version="synthetic-mapping-v1", source_sha256=SOURCE.sha256, evidence_anchor_ids=(ANCHOR.anchor_id,),
            applicable_context=(("quantity_kind", "ORE_MASS"), ("material", "WHOLE_ORE")),
        )
        intended = {"quantity_kind": "ORE_MASS", "material": "WHOLE_ORE"}
        self.assertEqual(resolve_concept(mapping, context=intended), "qudt-unit:TONNE")
        self.assertIsNone(resolve_concept(mapping, context={**intended, "material": "CONCENTRATE"}))
        self.assertIsNone(resolve_concept(replace(mapping, evidence_anchor_ids=()), context=intended))
        for relation in ("closeMatch", "broadMatch", "relatedMatch"):
            self.assertIsNone(resolve_concept(replace(mapping, relation=relation), context=intended))
        self.assertEqual(set(vocabulary()["standards"]), {
            "SKOS", "QUDT", "JORC", "CRIRSCO", "EarthResourceML", "SOSA/SSN", "PROV-O", "SHACL",
        })

    def test_missing_and_zero_are_distinct_and_derivation_needs_formula(self):
        for state in ("NOT_LOCATED", "NOT_DISCLOSED", "UNKNOWN"):
            self.assertEqual(evaluate(replace(RESOURCE, value_state=state)).reason_codes, (state,))
        self.assertEqual(evaluate(replace(RESOURCE, value_state="NOT_APPLICABLE")).status, "NOT_APPLICABLE")
        self.assertEqual(evaluate(replace(RESOURCE, cutoff_grade="0")).status, SemanticStatus.COMPARABLE_DIRECT)
        self.assertEqual(evaluate(transforms=({"kind": "DERIVATION"},)).status, "INSUFFICIENT_CONTEXT")
        self.assertEqual(evaluate(transforms=({
            "kind": "DERIVATION", "formula": "ore_mt * grade_percent * 10", "policy_version": "fixture-v1",
        },)).status, "COMPARABLE_DERIVED")

    def test_gis_point_and_completed_development_event_use_actual_roles(self):
        gis = replace(RESOURCE, quantity_kind="INFRASTRUCTURE_DISTANCE", native_feature_id="located:id", crs="EPSG:4326")
        self.assertEqual(evaluate(replace(gis, geometry_role="TENEMENT_CENTROID"), purpose="GIS").status, "NOT_COMPARABLE")
        self.assertEqual(evaluate(replace(gis, geometry_role="VERIFIED_DEPOSIT_POINT"), purpose="GIS").status, "COMPARABLE_DIRECT")
        event = replace(RESOURCE, quantity_kind="DEVELOPMENT_MILESTONE", status_role="COMPLETED", event_date="2025-06-01")
        self.assertEqual(evaluate(event, purpose="D1").status, "NOT_COMPARABLE")
        self.assertEqual(evaluate(replace(event, observation_role="HISTORICAL_EVENT"), purpose="D1").status, "COMPARABLE_DIRECT")

    def test_identity_and_policy_identity_roundtrip_do_not_merge_projects(self):
        identity = IdentityContext(
            subject_id=RESOURCE.project_id, subject_kind="PROJECT", object_id="another-tenement",
            object_kind="TENEMENT", relation_type="OBSERVED_TENURE", confidence="DOCUMENTED",
            source_sha256=SOURCE.sha256, evidence_anchor_ids=(ANCHOR.anchor_id,),
        )
        record = evaluate(replace(RESOURCE, identities=(identity,)))
        self.assertEqual(SemanticAssessment.from_dict(record.to_dict()), record)
        with self.assertRaisesRegex(ContractViolation, "policy"):
            replace(record, policy_version="unreviewed")
        with self.assertRaisesRegex(ContractViolation, "assessment ID"):
            replace(record, normalized_context=replace(RESOURCE, treo_members=("Sc2O3",)))
        self.assertNotEqual(record.assessment_id, evaluate().assessment_id)


class SemanticStoreTests(unittest.TestCase):
    def test_persistence_exact_lineage_and_no_promotion(self):
        with Store(":memory:") as store, store.transaction():
            persist_observation(store)
            store.add(VALIDATION)
            assessment = evaluate()
            self.assertTrue(store.add(assessment))
            self.assertFalse(store.add(assessment))
            self.assertEqual(store.records(SemanticAssessment), [assessment])
            self.assertEqual(store.records(AcceptedResearchValue), [])
            self.assertEqual(store.records(ResearchRelease), [])
            self.assertEqual(store.records(RawObservation), [RAW])
            self.assertEqual(store.integrity(), {"integrity_check": ["ok"], "foreign_key_check": []})

    def test_bad_scope_decision_digest_and_alias_cannot_write_partial_records(self):
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store)
                store.add(VALIDATION)
            failures = (
                (evaluate(replace(RESOURCE, project_id="unrelated-project")), "scope"),
                (evaluate(replace(RESOURCE, temporal_decision_id="a" * 64)), "temporal decision"),
                (evaluate(replace(RESOURCE, research_cutoff="2026-01-01")), "temporal decision"),
                (evaluate(ids=(RAW.observation_id, RAW.raw_record_id)), "alias"),
                (evaluate(anchors=()), "original anchors"),
                (evaluate(transforms=({"kind": "MAPPING", "input_observation_ids": ["unknown"]},)), "outside"),
                (replace(evaluate(), status=SemanticStatus.DESCRIPTIVE_ONLY, assessment_id=None), "use policy"),
            )
            for bad, message in failures:
                with self.subTest(message=message), self.assertRaisesRegex(ContractViolation, message), store.transaction():
                    store.add(Entity(
                        entity_id="new_mine", native_id="M1", native_namespace="synthetic",
                        kind="MINE", name="separate named facility",
                    ))
                    store.add(bad)
            self.assertFalse(store.records(SemanticAssessment))
            self.assertIsNone(store.get(Entity, "new_mine"))

    def test_v2_migration_adds_only_shadow_tables_and_keeps_frozen_rows(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "replay.sqlite"
            with Store(path) as store, store.transaction():
                persist_observation(store)
                for record in (VALIDATION, RELEASE, ACCEPTED):
                    store.add(record)
            with sqlite3.connect(path) as db:
                for table in ("semantic_assessment_anchors", "semantic_assessment_inputs", "semantic_assessments"):
                    db.execute(f"DROP TABLE {table}")
                db.execute("DROP INDEX temporal_decision_by_raw_and_id")
                db.execute("PRAGMA user_version = 2")
                old = {table: db.execute(f"SELECT payload FROM {table}").fetchall()
                       for table in ("raw_observations", "temporal_decisions", "accepted_values", "releases")}
            with Store(path) as store, store.transaction():
                store.add(evaluate())
                self.assertEqual(store.accepted_for_release(RELEASE.release_id), [ACCEPTED])
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("PRAGMA user_version").fetchone(), (SCHEMA_VERSION,))
                self.assertEqual(old, {table: db.execute(f"SELECT payload FROM {table}").fetchall() for table in old})


if __name__ == "__main__":
    unittest.main()
