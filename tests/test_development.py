"""Verify milestone language and fixed issuer statements without formal D1 writes."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.contracts.temporal import TemporalEvidence
from au_ree_v3.development import DATED_STUDY_POLICY_VERSION, _classification, _review, review_milestones
from au_ree_v3.documents import DocumentError, PDFDocument

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "config/research_profiles/au_ree_2026-11-28.json"
BINDINGS = ROOT / "config/research_profiles/development_milestone_bindings_v1.json"


class DevelopmentMilestoneTests(unittest.TestCase):
    def test_completion_planning_mention_and_trial_are_separate(self):
        pairs = (
            ("DFS", "The Definitive Feasibility Study was completed in April 2023.", "COMPLETED"),
            ("DFS", "The Definitive Feasibility Study is expected to be completed in April 2027.", "PLANNED"),
            ("DFS", "The company will have completed the DFS by November 2027.", "PLANNED"),
            ("DFS", "The DFS was not yet completed.", "NOT_COMPLETED"),
            ("DFS", "The development update includes an estimate for the DFS.", "MENTIONED"),
            ("DFS", "The issuer completed the PFS and discussed a DFS.", "INSUFFICIENT_CONTEXT"),
            ("Construction", "The construction plans were completed in May 2023.", "MENTIONED"),
            ("Construction", "Construction commenced with early site access works.", "EARLY_WORKS"),
            ("PFS", "The issuer commenced a Pre-Feasibility Study in September 2025.", "ONGOING"),
            ("Production", "The demonstration plant commenced production in May 2023.", "INSUFFICIENT_CONTEXT"),
            ("Production", "The mine commenced commercial production in May 2023.", "ATTAINED"),
        )
        for stage, quote, expected in pairs:
            with self.subTest(quote=quote):
                self.assertEqual(_classification(stage, quote)[0], expected)

    def test_scoped_review_rejects_date_and_source_span_without_evidence(self):
        digest = "a" * 64
        proof = TemporalEvidence(digest, "ARCHIVED_PUBLIC_CAPTURE", "2026-11-28",
                                 "synthetic-fixture", "b" * 64)
        edition = {"sha256": digest, "source_key": "synthetic", "acquired_at": "2026-11-28T01:01:00+00:00",
                   "reacquired_at": None}
        binding = {
            "page": 1, "quote": "The DFS was completed in April 2023.", "event_date_token": "April 2023",
            "scope_quote": "Synthetic Project", "scope": "Synthetic Project", "stage": "DFS", "project_id": "synthetic",
        }
        pdf = PDFDocument(digest, ("Synthetic Project\nThe DFS was completed in April 2023.",), "synthetic")
        observed = _review(binding, edition, pdf, (proof,), "2026-11-28", 4)
        self.assertEqual(observed["shadow_ordinal"], 4)
        self.assertEqual(observed["event_date"], "2023-04")
        self.assertEqual(observed["raw_observation"]["observation_role"], "HISTORICAL_EVENT")
        with self.assertRaisesRegex(DocumentError, "EVENT_DATE_NOT_BOUND"):
            _review({**binding, "event_date_token": "April 2024"}, edition, pdf, (proof,), "2026-11-28", 4)
        with self.assertRaisesRegex(DocumentError, "REVIEW_QUOTE_NOT_UNIQUE"):
            _review({**binding, "scope_quote": "Different Project"}, edition, pdf, (proof,), "2026-11-28", 4)
        distant = PDFDocument(
            digest, ("Synthetic Project\n" + "Unrelated content. " * 20 + "\nThe DFS was completed in April 2023.",),
            "synthetic",
        )
        with self.assertRaisesRegex(DocumentError, "PROJECT_SCOPE_NOT_LOCAL"):
            _review(binding, edition, distant, (proof,), "2026-11-28", 4)

    def test_dated_study_result_event_requires_original_event_or_same_notice(self):
        digest = "a" * 64
        proof = TemporalEvidence(digest, "ARCHIVED_PUBLIC_CAPTURE", "2026-11-28", "fixture", "b" * 64)
        edition = {"sha256": digest, "source_key": "test", "acquired_at": "2026-11-28T01:01:00+00:00",
                   "reacquired_at": None}
        publication = {"page": 1, "quote": "ASX Announcement 30 October 2026", "date_token": "30 October 2026"}
        stated = {"page": 1, "stage": "PFS", "scope": "Example Project", "scope_quote": "Example Project",
                  "project_id": "fixture", "event_policy": DATED_STUDY_POLICY_VERSION, "publication": publication}

        def read(quote, **binding):
            pdf = PDFDocument(digest, (f"ASX Announcement 30 October 2026\nExample Project\n{quote}",), "fixture")
            return _review({**stated, "quote": quote, **binding}, edition, pdf, (proof,), "2026-11-28", 3)

        native_date = {"event_date_role": "STUDY_RESULTS_RELEASED", "event_date_token": "August 2026"}
        item = read("In August 2026, The Company released a Pre-Feasibility Study", **native_date)
        self.assertEqual((item["claim_state"], item["event_date"], item["shadow_ordinal"]),
                         ("STUDY_RESULTS_RELEASED", "2026-08", None))
        self.assertEqual(item["temporal_decision"]["observation_role"], "HISTORICAL_EVENT")
        own_notice = {"event_date_role": "STUDY_RESULTS_RELEASED", "event_date_source": "SAME_ISSUER_NOTICE"}
        item = read("The Company is pleased to announce the results of the Pre-Feasibility Study", **own_notice)
        self.assertEqual(item["event_date"], "2026-10-30")
        negative = (
            ("The company was founded in August 2026 and released a Pre-Feasibility Study", native_date),
            ("The company mentioned the Pre-Feasibility Study in August 2026", native_date),
            ("In August 2026, the company will release a Pre-Feasibility Study", native_date),
            ("The Company plans to announce the results of the Pre-Feasibility Study", own_notice),
            ("In August 2026, the company possibly released a Pre-Feasibility Study", native_date),
            ("The Pre-Feasibility Study found testing was completed in August 2026", {
                "event_date_token": "August 2026",
            }),
        )
        for quote, binding in negative:
            with self.subTest(quote=quote), self.assertRaises(DocumentError):
                read(quote, **binding)
        with self.assertRaisesRegex(DocumentError, "HISTORICAL_STUDY_EVENT_AFTER_ISSUER_PUBLICATION"):
            read("The Pre-Feasibility Study was completed in November 2026", event_date_token="November 2026")

    def test_a_scope_section_cannot_bind_a_claim_outside_its_boundaries(self):
        digest = "a" * 64
        record = {"sha256": digest, "source_key": "test", "acquired_at": None, "reacquired_at": None}
        scope = "Named Project Evidence"
        section = f"{scope}\nSection body."
        quote = "The Scoping Study was completed in September, 2022"
        pdf = PDFDocument(digest, (f"{section}\nDifferent Project Evidence\n{quote}",), "fixture")
        with self.assertRaisesRegex(DocumentError, "MILESTONE_OUTSIDE_PROJECT_SCOPE_SECTION"):
            _review({"page": 1, "stage": "Scoping", "scope_quote": scope, "scope_section_quote": section,
                     "quote": quote, "event_date_token": "September, 2022"}, record, pdf, (), "2026-11-28", 2)

    @unittest.skipUnless(
        os.getenv("AU_REE_MILESTONE_ASSETS") and os.getenv("AU_REE_MILESTONE_MANIFEST"),
        "fixed 144-SHA manifest and source CAS are required",
    )
    def test_frozen_issuer_evidence_cross_checks_completed_and_future_claims(self):
        # Source document statements checked independently at the bound physical pages.
        expected = {
            "koppamurra-pfs-asserted-date-unreported": ("COMPLETED", "CANDIDATE", None, None),
            "koppamurra-dfs-engineering-plan": ("PLANNED", "ELIGIBLE", None, None),
            "donald-phase-one-dfs-completed": ("COMPLETED", "ELIGIBLE", "2023-04", 4),
            "donald-phase-two-pfs-completed": ("COMPLETED", "ELIGIBLE", "2023-06", 3),
            "balranald-dfs-completed": ("COMPLETED", "ELIGIBLE", "2022", 4),
        }
        with TemporaryDirectory() as directory:
            receipt = review_milestones(
                PROFILE, Path(os.environ["AU_REE_MILESTONE_MANIFEST"]), Path(os.environ["AU_REE_MILESTONE_ASSETS"]),
                BINDINGS, Path(directory),
            )
        self.assertEqual(receipt["status"], "PASS")
        actual = {
            row["case_id"]: (row["claim_state"], row["temporal_decision"]["status"], row["event_date"],
                             row["shadow_ordinal"])
            for row in receipt["cases"]
        }
        self.assertEqual(actual, expected)
        self.assertEqual(receipt["source_manifest_unique_sha256"], 144)
        self.assertEqual(receipt["formal_project_criterion_delta"], 0)
        phases = [row["raw_observation"]["scope_id"] for row in receipt["cases"] if row["case_id"].startswith("donald")]
        self.assertEqual(len(set(phases)), 2)
