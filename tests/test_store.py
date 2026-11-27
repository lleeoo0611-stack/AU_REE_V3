"""Synthetic cases for contract behavior and SQLite integrity."""

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

from au_ree_v3.contracts import (
    AcceptedResearchValue,
    ContractViolation,
    DerivedValue,
    Entity,
    EvidenceAnchor,
    NormalizedObservation,
    RawObservation,
    ResearchRelease,
    ResourceScope,
    Source,
    ValidatedObservation,
    ValidationOutcome,
)
from au_ree_v3.store import RecordConflict, SourceDigestMismatch, Store

CONTENT = b"synthetic source bytes"
SOURCE = Source(
    source_key="synthetic-paper",
    url="https://example.org/synthetic",
    sha256=sha256(CONTENT).hexdigest(),
    kind="synthetic",
    source_version="v1",
    publication_date="2026-08-17",
    available_date="2026-08-19",
    acquired_at="2026-11-27T12:00:00Z",
)
PROJECT = Entity(
    entity_id="synthetic-project", native_namespace="synthetic", native_id="prj", kind="PROJECT", name="Same Name",
)
DEPOSIT = Entity(
    entity_id="synthetic-deposit", native_namespace="synthetic", native_id="d", kind="DEPOSIT",
    name="Same Name", parent_id=PROJECT.entity_id,
)
SCOPE = ResourceScope(
    scope_id="synthetic-scope", project_id=PROJECT.entity_id, deposit_id=DEPOSIT.entity_id, name="Same Name",
)
ANCHOR = EvidenceAnchor(
    anchor_id="synthetic-anchor", source_key=SOURCE.source_key, source_sha=SOURCE.sha256,
    page=1, locator="page 1, table 2, row 3", table=2, row=3, column=4, bbox=(2, 1, 3, 9),
)
TOKEN = "1.001250000000000000000000"
RAW_PAYLOAD = '{ "raw" : 1.001250000000000000000000, "unreported" : null, "extreme" : 1e309 }'
RAW = RawObservation(
    observation_id="v2-native-observation-id",
    native_raw_id="v2-native-raw-id",
    anchor_id=ANCHOR.anchor_id,
    source_key=SOURCE.source_key,
    source_sha=SOURCE.sha256,
    project_id=PROJECT.entity_id,
    scope_id=SCOPE.scope_id,
    deposit_id=DEPOSIT.entity_id,
    variable="grade.treo",
    raw_value=TOKEN,
    raw_unit="ppm",
    material_basis="ore",
    denominator_basis="total-ore-mass",
    chemical_basis="TREO",
    statement_id="synthetic-statement",
    category="JORC Mineral Resource",
    effective_date="2026-07",
    acquisition_date="2026-11-27T12:00:00Z",
    status="REPORTED",
    transform_version="synthetic-extractor-v1",
    original_payload=RAW_PAYLOAD,
)
CANDIDATE = NormalizedObservation(
    candidate_id="synthetic-candidate", observation_id=RAW.observation_id, value=TOKEN, unit="ppm",
    material_basis=RAW.material_basis, denominator_basis=RAW.denominator_basis, chemical_basis=RAW.chemical_basis,
    transform_version="synthetic-transform-v1",
)
VALIDATION = ValidatedObservation(
    validation_id="synthetic-validation", candidate_id=CANDIDATE.candidate_id,
    cutoff="2026-11-05", rules_sha="c" * 64, outcome="VALIDATED", reason="synthetic_fixture",
    evidence_anchor_ids=(ANCHOR.anchor_id,), comparable=True,
)
RELEASE = ResearchRelease(
    release_id="synthetic-release", input_sha="a" * 64, code_commit="b" * 40, rules_sha=VALIDATION.rules_sha,
    cutoff=VALIDATION.cutoff, cohort=(PROJECT.entity_id,), validation_status="NOT_RUN",
)
ACCEPTED = AcceptedResearchValue(
    acceptance_id="synthetic-acceptance", validation_id=VALIDATION.validation_id, release_id=RELEASE.release_id,
    selection_key="synthetic-project:grade.treo", reason="synthetic-selection",
)


def persist_observation(store, *, raw=RAW, candidate=CANDIDATE, verify_source=True):
    if verify_source:
        store.add_source(SOURCE, CONTENT)
    else:
        store.add(SOURCE)
    for record in (PROJECT, DEPOSIT, SCOPE, ANCHOR, raw, candidate):
        store.add(record)


class StoreTests(unittest.TestCase):
    def test_duplicate_replay_and_lossless_roundtrip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.sqlite"
            with Store(path) as store, store.transaction():
                persist_observation(store)
                for record in (VALIDATION, RELEASE, ACCEPTED):
                    self.assertTrue(store.add(record))
                self.assertEqual(store.integrity(), {"integrity_check": ["ok"], "foreign_key_check": []})

            with Store(path) as store, store.transaction():
                self.assertEqual(store.records(RawObservation), [RAW])
                actual = store.get(RawObservation, RAW.observation_id)
                self.assertEqual(actual.raw_value, TOKEN)
                self.assertEqual(actual.original_payload, RAW_PAYLOAD)
                self.assertEqual(actual.id_version, "observation-v1")
                self.assertEqual(actual.native_raw_id, RAW.native_raw_id)
                self.assertEqual(store.get(EvidenceAnchor, ANCHOR.anchor_id), ANCHOR)
                self.assertFalse(store.add_source(SOURCE, CONTENT))
                for record in (PROJECT, DEPOSIT, SCOPE, ANCHOR, RAW, CANDIDATE, VALIDATION, RELEASE, ACCEPTED):
                    self.assertFalse(store.add(record))
                self.assertEqual(store.records(AcceptedResearchValue), [ACCEPTED])

    def test_conflict_rolls_back_whole_transaction(self):
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store)
                for record in (VALIDATION, RELEASE, ACCEPTED):
                    store.add(record)
            with self.assertRaises(RecordConflict), store.transaction():
                store.add(replace(PROJECT, entity_id="new-project", native_id="new"))
                store.add(replace(ACCEPTED, acceptance_id="conflict"))
            self.assertIsNone(store.get(Entity, "new-project"))
            self.assertEqual(store.records(AcceptedResearchValue), [ACCEPTED])
            with self.assertRaises(RuntimeError):
                store.add(PROJECT)
            self.assertEqual(store.integrity(), {"integrity_check": ["ok"], "foreign_key_check": []})

    def test_source_versions_and_missing_bytes_are_isolated(self):
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store, verify_source=False)
            self.assertFalse(store.source_verified(SOURCE.source_key, SOURCE.sha256))
            with self.assertRaisesRegex(ContractViolation, "verified source bytes"), store.transaction():
                store.add(VALIDATION)
            with self.assertRaises(SourceDigestMismatch), store.transaction():
                store.add_source(SOURCE, b"different bytes")
            next_content = b"different bytes"
            next_version = replace(SOURCE, sha256=sha256(next_content).hexdigest())
            with self.assertRaises(RecordConflict), store.transaction():
                store.add_source(next_version, next_content)
            self.assertEqual(store.records(Source), [SOURCE])

            with store.transaction():
                store.add_source(replace(next_version, source_version="v2"), next_content)
                self.assertFalse(store.source_verified(SOURCE.source_key, SOURCE.sha256))
                store.add_source(SOURCE, CONTENT)
                store.add(VALIDATION)
            self.assertEqual(store.records(ValidatedObservation), [VALIDATION])
            self.assertEqual(store.get(Source, (SOURCE.source_key, SOURCE.sha256)), SOURCE)
            self.assertEqual(len(store.records(Source)), 2)

    def test_same_name_different_scopes_cannot_be_combined(self):
        other_project = replace(PROJECT, entity_id="another-project", native_id="prj-2")
        other_deposit = replace(DEPOSIT, entity_id="another-deposit", native_id="d-2", parent_id=other_project.entity_id)
        other_scope = replace(SCOPE, scope_id="another-scope", project_id=other_project.entity_id, deposit_id=other_deposit.entity_id)
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store)
                for record in (other_project, other_deposit, other_scope):
                    store.add(record)
            with self.assertRaisesRegex(ContractViolation, "same project and deposit"), store.transaction():
                store.add(replace(
                    RAW, observation_id="cross-project", native_raw_id="cross-project-raw", scope_id=other_scope.scope_id,
                ))
            with self.assertRaisesRegex(ContractViolation, "scope deposit"), store.transaction():
                store.add(replace(SCOPE, scope_id="cross-deposit", deposit_id=other_deposit.entity_id))
            with self.assertRaises(RecordConflict), store.transaction():
                store.add(replace(PROJECT, entity_id="duplicate-project-identity"))
            self.assertEqual(len(store.records(ResourceScope)), 2)
            self.assertEqual(store.records(RawObservation), [RAW])

    def test_raw_editions_preserve_logical_observation_id_and_candidate_bindings(self):
        updated_content = b"synthetic new edition"
        updated_source = replace(SOURCE, sha256=sha256(updated_content).hexdigest(), source_version="v2")
        updated_anchor = replace(ANCHOR, anchor_id="updated-anchor", source_sha=updated_source.sha256)
        updated_raw = replace(
            RAW, native_raw_id="updated-v2-raw-id", source_sha=updated_source.sha256,
            anchor_id=updated_anchor.anchor_id, raw_value="2.300",
        )
        updated_candidate = replace(
            CANDIDATE, candidate_id="updated-candidate", raw_record_id=updated_raw.raw_record_id, value="2.300",
        )
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store)
                store.add_source(updated_source, updated_content)
                for record in (updated_anchor, updated_raw, updated_candidate, VALIDATION, RELEASE, ACCEPTED):
                    store.add(record)
            self.assertEqual(store.get(RawObservation, RAW.native_raw_id), RAW)
            self.assertEqual(store.get(RawObservation, updated_raw.native_raw_id), updated_raw)
            self.assertEqual(store.raw_for_candidate(CANDIDATE.candidate_id), RAW)
            self.assertEqual(store.raw_for_candidate(updated_candidate.candidate_id), updated_raw)
            with self.assertRaisesRegex(RecordConflict, "multiple editions"), store.transaction():
                store.add(replace(CANDIDATE, candidate_id="ambiguous-candidate"))
            self.assertEqual(store.records(AcceptedResearchValue), [ACCEPTED])

    def test_unbound_scientific_variations_are_rejected(self):
        cases = (
            ("exploration-target", replace(RAW, category="JORC Exploration_Target"), CANDIDATE, VALIDATION),
            ("whole-ore/heavy-mineral", RAW, replace(CANDIDATE, material_basis="heavy minerals"), VALIDATION),
            ("mixed-denominator", RAW, replace(CANDIDATE, denominator_basis="fine material"), VALIDATION),
            ("TREO/TREO+Sc2O3", RAW, replace(CANDIDATE, chemical_basis="TREO+Sc2O3"), VALIDATION),
            ("cross-page", RAW, CANDIDATE, replace(VALIDATION, evidence_anchor_ids=(ANCHOR.anchor_id, "different-page"))),
            ("missing-anchor", replace(RAW, anchor_id=None), CANDIDATE, replace(VALIDATION, evidence_anchor_ids=())),
            ("post-cutoff", RAW, CANDIDATE, replace(VALIDATION, cutoff="2026-08-18")),
            ("partial-effective-date", replace(RAW, effective_date="2026-11"), CANDIDATE, VALIDATION),
        )
        for name, raw, candidate, validation in cases:
            with self.subTest(name=name), Store(":memory:") as store:
                with store.transaction():
                    persist_observation(store, raw=raw, candidate=candidate)
                    store.add(replace(ANCHOR, anchor_id="different-page", page=2))
                with self.assertRaises(ContractViolation), store.transaction():
                    store.add(validation)
                self.assertEqual(store.records(NormalizedObservation), [candidate])
                self.assertEqual(store.records(ValidatedObservation), [])
                self.assertEqual(store.records(AcceptedResearchValue), [])

    def test_evidence_from_other_source_version_cannot_bind_observation(self):
        other_content = b"synthetic other version"
        other = replace(SOURCE, source_version="v2", sha256=sha256(other_content).hexdigest())
        with Store(":memory:") as store, store.transaction():
            persist_observation(store)
            store.add_source(other, other_content)
            with self.assertRaisesRegex(ContractViolation, "another source version"):
                store.add(replace(RAW, native_raw_id="other-raw-id", source_sha=other.sha256))
            self.assertEqual(store.records(RawObservation), [RAW])

    def test_rejected_candidates_never_promote(self):
        reasons = (
            ValidationOutcome.EVIDENCE_INSUFFICIENT,
            ValidationOutcome.SCIENTIFIC_CONFLICT,
            ValidationOutcome.SOURCE_UNAVAILABLE,
            ValidationOutcome.CODE_ERROR,
        )
        raw = replace(RAW, raw_value=None, status="UNKNOWN", null_reason="UNREPORTED")
        candidate = replace(CANDIDATE, value=None)
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store, raw=raw, candidate=candidate, verify_source=False)
                store.add(RELEASE)
                for reason in reasons:
                    store.add(replace(VALIDATION, validation_id=str(reason), outcome=reason, comparable=False, reason=str(reason)))
            for reason in reasons:
                with self.subTest(reason=reason), self.assertRaises(ContractViolation), store.transaction():
                    store.add(replace(ACCEPTED, validation_id=str(reason)))
            self.assertEqual(store.records(RawObservation), [raw])
            self.assertIsNone(store.get(RawObservation, raw.observation_id).raw_value)
            self.assertEqual(store.get(RawObservation, raw.observation_id).null_reason, "UNREPORTED")
            self.assertEqual(len(store.records(ValidatedObservation)), len(reasons))
            self.assertEqual(store.records(AcceptedResearchValue), [])

    def test_versioned_selection_and_bound_derivation(self):
        newer_release = replace(RELEASE, release_id="new-synthetic-release", rules_sha="d" * 64)
        derived = DerivedValue(
            derived_id="synthetic-derived", release_id=RELEASE.release_id,
            project_id=PROJECT.entity_id, scope_id=SCOPE.scope_id, variable="grade.treo.percent",
            value="0.000100125000000000000000", unit="%", formula="grade_ppm * factor",
            transform_version="synthetic-formula-v1", denominator_basis=RAW.denominator_basis,
            input_observation_ids=(RAW.observation_id,), factor="0.0001",
        )
        with Store(":memory:") as store:
            with store.transaction():
                persist_observation(store)
                for record in (VALIDATION, RELEASE, newer_release):
                    store.add(record)
            with self.assertRaisesRegex(ContractViolation, "rules must match"), store.transaction():
                store.add(replace(ACCEPTED, release_id=newer_release.release_id))
            with self.assertRaisesRegex(ContractViolation, "accepted for the same scope and release"), store.transaction():
                store.add(derived)
            with store.transaction():
                store.add(ACCEPTED)
                store.add(derived)
                store.add(replace(VALIDATION, validation_id="second-validation", rules_sha=newer_release.rules_sha))
                store.add(replace(
                    ACCEPTED, acceptance_id="second-selection", validation_id="second-validation",
                    release_id=newer_release.release_id,
                ))
            self.assertEqual(store.records(DerivedValue), [derived])
            self.assertEqual(len(store.records(AcceptedResearchValue)), 2)
            self.assertEqual(store.get(ResearchRelease, RELEASE.release_id), RELEASE)

    def test_foreign_keys_and_foreign_database_guard(self):
        with Store(":memory:") as store:
            with self.assertRaisesRegex(ContractViolation, "FOREIGN KEY"), store.transaction():
                store.add(ANCHOR)
            self.assertEqual(store.records(EvidenceAnchor), [])
            self.assertEqual(store.integrity(), {"integrity_check": ["ok"], "foreign_key_check": []})

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "unrelated.sqlite"
            connection = sqlite3.connect(path)
            try:
                connection.execute("CREATE TABLE other (value TEXT)")
                connection.commit()
                with self.assertRaisesRegex(ContractViolation, "does not belong"):
                    Store(path)
                self.assertEqual(connection.execute("PRAGMA application_id").fetchone(), (0,))
                self.assertEqual(connection.execute("SELECT name FROM sqlite_schema").fetchall(), [("other",)])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
