"""Exercise release-bound temporal readership through the real SQLite store."""

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts import ContractViolation
from au_ree_v3.contracts.temporal import TEMPORAL_POLICY_VERSION, TemporalEvidence
from au_ree_v3.research_inputs import checked_research_inputs, publication_temporal_lineage
from au_ree_v3.store import Store
import test_store as fixtures


class ResearchInputTemporalTests(unittest.TestCase):
    source = replace(
        fixtures.SOURCE,
        available_date="2026-11-22",
        availability_role="REGISTRY_SELECTION",
    )
    proof = TemporalEvidence(
        source_sha256=source.sha256,
        kind="ARCHIVED_PUBLIC_CAPTURE",
        public_on="2026-08-19",
        reference="synthetic frozen publisher capture",
        reference_sha256=sha256(b"synthetic frozen publisher capture").hexdigest(),
    )

    def _seed(self, store, *, mode="FORMAL", evidence=(), release_proofs=None,
              role="HISTORICAL_MEASUREMENT", effective_date=fixtures.RAW.effective_date):
        raw = replace(fixtures.RAW, observation_role=role, effective_date=effective_date)
        decision = fixtures.evidence_decision(
            raw, fixtures.VALIDATION.cutoff, self.source, evidence,
        )
        eligible = decision.status == "ELIGIBLE"
        validation = replace(
            fixtures.VALIDATION, temporal_decision_id=decision.decision_id, temporal_evidence=evidence,
            outcome="VALIDATED" if eligible else "EVIDENCE_INSUFFICIENT", comparable=eligible,
        )
        release = replace(
            fixtures.RELEASE, release_mode=mode, validation_status="PASS",
            result_fingerprints={"census": "e" * 64},
            temporal_policy_version=TEMPORAL_POLICY_VERSION,
            temporal_decision_ids=(decision.decision_id,),
            temporal_evidence_ids=tuple(sorted(
                proof.evidence_id for proof in (evidence if release_proofs is None else release_proofs)
            )),
        )
        with store.transaction():
            store.add_source(self.source, fixtures.CONTENT)
            for record in (fixtures.PROJECT, fixtures.DEPOSIT, fixtures.SCOPE, fixtures.ANCHOR,
                           raw, fixtures.CANDIDATE, validation, release):
                store.add(record)
        return release, decision

    def test_formal_census_mcda_and_seal_share_bound_lineage(self):
        with Store(":memory:", trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
            release, decision = self._seed(store, evidence=(self.proof,))
            with store.transaction():
                store.add(fixtures.ACCEPTED)
            census, = checked_research_inputs(store, release, purpose="CENSUS")
            mcda, = checked_research_inputs(store, release, purpose="MCDA")
            final = publication_temporal_lineage(store, release)
            self.assertEqual(census.lineage(), mcda.lineage())
            self.assertEqual(final["inputs"], [census.lineage()])
            self.assertEqual(final["cutoff"], fixtures.VALIDATION.cutoff)
            self.assertEqual(final["temporal_decision_ids"], [decision.decision_id])
            self.assertEqual(final["temporal_evidence_ids"], [self.proof.evidence_id])
            self.assertEqual(final["accepted_selection_count"], 1)
            self.assertRegex(final["selection_snapshot_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(final, publication_temporal_lineage(store, release))
            self.assertEqual(census.source.sha256, self.proof.source_sha256)
            self.assertEqual(census.temporal_trace["decision_id"], decision.decision_id)
            self.assertEqual(census.temporal_trace["matching_evidence_ids"], [self.proof.evidence_id])
            self.assertEqual(census.temporal_trace["available_date"], "2026-11-22")
            self.assertEqual(census.temporal_trace["earliest_proven_public_date"], "2026-08-19")
            self.assertEqual(census.temporal_trace["acquired_at"], self.source.acquired_at)

    def test_unapproved_public_proof_cannot_be_selected_for_formal_release(self):
        with Store(":memory:") as store:
            with self.assertRaisesRegex(ContractViolation, "temporal evidence"):
                self._seed(store, evidence=(self.proof,))

    def test_unapproved_earlier_evidence_cannot_rewrite_formal_lineage(self):
        unapproved = replace(self.proof, public_on="2026-08-18")
        with Store(":memory:", trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
            release, decision = self._seed(
                store, evidence=(self.proof, unapproved), release_proofs=(self.proof,),
            )
            self.assertEqual(decision.earliest_proven_public_date, "2026-08-18")
            with self.assertRaisesRegex(ContractViolation, "approved by the operator"), store.transaction():
                store.add(fixtures.ACCEPTED)
            self.assertEqual(checked_research_inputs(store, release, purpose="CENSUS"), ())

    def test_formal_consumers_recheck_trust_after_reopen(self):
        with TemporaryDirectory() as folder:
            database = Path(folder) / "research.sqlite"
            with Store(database, trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
                release, _ = self._seed(store, evidence=(self.proof,))
                with store.transaction():
                    store.add(fixtures.ACCEPTED)
            with Store(database) as store:
                for purpose in ("CENSUS", "MCDA", "PUBLICATION"):
                    with self.subTest(purpose=purpose), self.assertRaisesRegex(
                        ContractViolation, "approved by the operator"
                    ):
                        checked_research_inputs(store, release, purpose=purpose)

    def test_unproven_historical_candidate_cannot_enter_release_consumers(self):
        with Store(":memory:", trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
            release, decision = self._seed(store, release_proofs=(self.proof,))
            self.assertEqual(decision.status, "CANDIDATE")
            self.assertEqual(store.validation_temporal_trace(fixtures.VALIDATION.validation_id)["reason"],
                             "PUBLIC_EVIDENCE_MISSING")
            with self.assertRaises(ContractViolation), store.transaction():
                store.add(fixtures.ACCEPTED)
            self.assertEqual(checked_research_inputs(store, release, purpose="CENSUS"), ())
            for purpose in ("MCDA", "PUBLICATION"):
                with self.subTest(purpose=purpose), self.assertRaisesRegex(
                    ContractViolation, "requires accepted selections"
                ):
                    checked_research_inputs(store, release, purpose=purpose)

    def test_incomplete_temporal_pin_coverage_blocks_formal_consumers(self):
        with Store(":memory:", trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
            release, decision = self._seed(store, evidence=(self.proof,))
            incomplete = replace(
                release, release_id="incomplete-coverage-release",
                temporal_decision_ids=tuple(sorted({decision.decision_id, "f" * 64})),
            )
            with store.transaction():
                store.add(incomplete)
                store.add(replace(
                    fixtures.ACCEPTED, acceptance_id="incomplete-coverage-acceptance",
                    release_id=incomplete.release_id,
                ))
            for purpose in ("MCDA", "PUBLICATION"):
                with self.subTest(purpose=purpose), self.assertRaisesRegex(
                    ContractViolation, "must cover the exact temporal decision pins"
                ):
                    checked_research_inputs(store, incomplete, purpose=purpose)

    def test_experimental_value_cannot_enter_formal_mcda_or_seal(self):
        with Store(":memory:") as store:
            release, _ = self._seed(store, mode="EXPERIMENTAL", evidence=(self.proof,))
            with store.transaction():
                store.add(fixtures.ACCEPTED)
            self.assertEqual(len(checked_research_inputs(store, release, purpose="CENSUS")), 1)
            with self.assertRaisesRegex(ContractViolation, "verified formal release"):
                checked_research_inputs(store, release, purpose="MCDA")
            with self.assertRaisesRegex(ContractViolation, "verified formal release"):
                publication_temporal_lineage(store, release)

    def test_forecast_is_not_an_actual_history_input(self):
        with Store(":memory:", trusted_temporal_evidence_ids=(self.proof.evidence_id,)) as store:
            release, decision = self._seed(
                store, evidence=(self.proof,), role="FORECAST", effective_date="2027-01",
            )
            self.assertEqual(decision.status, "ELIGIBLE")
            with store.transaction():
                store.add(fixtures.ACCEPTED)
            for purpose in ("CENSUS", "MCDA", "PUBLICATION"):
                with self.subTest(purpose=purpose), self.assertRaisesRegex(ContractViolation, "actual results"):
                    checked_research_inputs(store, release, purpose=purpose)

    def test_projection_cannot_override_registered_cutoff(self):
        with Store(":memory:") as store:
            release, _ = self._seed(store, mode="EXPERIMENTAL", evidence=(self.proof,))
            changed = replace(release, cutoff="2026-11-13")
            with self.assertRaisesRegex(ContractViolation, "exact stored release"):
                checked_research_inputs(store, changed, purpose="CENSUS")
