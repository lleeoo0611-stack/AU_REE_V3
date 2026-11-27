"""Independent date and identity boundaries for the common temporal policy."""

from dataclasses import replace
from hashlib import sha256
import unittest

from au_ree_v3.contracts.temporal import TemporalEvidence, evaluate_temporal


class TemporalGateTests(unittest.TestCase):
    source_sha = sha256(b"exact edition").hexdigest()
    other_sha = sha256(b"alternate edition served at the same URL").hexdigest()
    receipt = TemporalEvidence(
        source_sha, "ARCHIVED_PUBLIC_CAPTURE", "2026-10-28", "frozen receipt#record",
        sha256(b"pinned capture receipt").hexdigest(),
    )

    def decision(self, **overrides):
        params = {
            "source_sha256": self.source_sha, "cutoff": "2026-11-13", "publication_date": "2026-10-26",
            "available_date": "2026-10-28", "acquired_at": "2026-10-28T09:00:00+00:00",
            "reacquired_at": "2026-11-20T10:00:00Z", "effective_date": "2026-10",
            "evidence": (self.receipt,),
        }
        return evaluate_temporal(**(params | overrides))

    def test_later_reacquisition_keeps_proven_public_edition_eligible(self):
        decision = self.decision()
        self.assertEqual(decision.status, "ELIGIBLE")
        self.assertEqual(decision.earliest_proven_public_date, "2026-10-28")
        self.assertEqual(decision.reacquired_at, "2026-11-20T10:00:00Z")
        self.assertEqual(decision.matching_evidence_ids, (self.receipt.evidence_id,))

    def test_declared_first_publication_after_cutoff_is_ineligible(self):
        decision = self.decision(publication_date="2026-11-16",
                                 evidence=(replace(self.receipt, public_on="2026-11-16"),))
        self.assertEqual((decision.status, decision.reason), ("INELIGIBLE", "PUBLICATION_AFTER_CUTOFF"))

    def test_edition_mismatch_and_source_bytes_without_dated_public_proof(self):
        wrong = self.decision(source_sha256=self.other_sha)
        self.assertEqual((wrong.status, wrong.reason), ("CANDIDATE", "NO_SAME_EDITION_PUBLIC_EVIDENCE"))
        self.assertEqual(wrong.matching_evidence_ids, ())
        bare_sha = self.decision(evidence=())
        self.assertEqual((bare_sha.status, bare_sha.reason), ("CANDIDATE", "PUBLIC_EVIDENCE_MISSING"))

    def test_late_capture_is_not_a_first_publication_claim(self):
        result = self.decision(evidence=(replace(self.receipt, public_on="2026-11-20"),))
        self.assertEqual((result.status, result.reason), ("CANDIDATE", "PRE_CUTOFF_PUBLIC_EVIDENCE_MISSING"))

    def test_conflicting_time_fields_are_preserved(self):
        result = self.decision(available_date="2026-11-17")
        self.assertEqual(result.available_date, "2026-11-17")
        self.assertEqual(result.acquired_at, "2026-10-28T09:00:00+00:00")
        self.assertEqual((result.status, result.reason), ("CONFLICT", "UNCLASSIFIED_AVAILABILITY_AFTER_ACQUISITION"))
        reviewed = self.decision(available_date="2026-11-17", availability_role="REGISTRY_SELECTION")
        self.assertEqual(reviewed.status, "ELIGIBLE")
        self.assertNotEqual(result.decision_id, reviewed.decision_id)

    def test_conflicting_publication_witness_is_not_overwritten(self):
        result = self.decision(publication_date="2026-11-16")
        self.assertEqual((result.status, result.reason), ("CONFLICT", "EXACT_PUBLIC_EVIDENCE_PRECEDES_DECLARED_PUBLICATION"))

    def test_observation_date_policy_retains_forecast_and_plan(self):
        self.assertEqual(self.decision(effective_date="2028-12").status, "INELIGIBLE")
        for role in ("FORECAST", "ENGINEERING_PLAN"):
            decision = self.decision(observation_role=role, effective_date="2028-12", event_date="2028")
            self.assertEqual(decision.status, "ELIGIBLE")
            self.assertEqual(decision.observation_role, role)
            self.assertEqual(decision.effective_date, "2028-12")

    def test_uncertain_historical_date_stays_candidate(self):
        self.assertEqual(self.decision(effective_date="2026-11").reason, "HISTORICAL_DATE_PRECISION_SPANS_CUTOFF")
        self.assertEqual(self.decision(effective_date=None).reason, "HISTORICAL_OBSERVATION_DATE_MISSING")

    def test_reordering_evidence_and_cutoff_revision(self):
        second = replace(self.receipt, public_on="2026-11-01", reference="frozen receipt#second-record")
        first = self.decision(observation_role="DOCUMENT", evidence=(self.receipt, second))
        self.assertEqual(first, self.decision(observation_role="DOCUMENT", evidence=(second, self.receipt)))
        closer = self.decision(observation_role="DOCUMENT", cutoff="2026-10-27", evidence=(second, self.receipt))
        self.assertEqual(closer.status, "CANDIDATE")
        self.assertEqual(first.status, "ELIGIBLE")
        self.assertNotEqual(first.decision_id, closer.decision_id)
