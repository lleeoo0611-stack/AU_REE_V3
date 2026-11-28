from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import unittest

from au_ree_v3.contracts import ContractViolation
from au_ree_v3.project_attributes import (
    AttributeAssertion, AttributeSelection, POLICY_SHA, ValidatedAttribute,
    _development_role, _select_attributes, _verdict,
)
from au_ree_v3.registry import load_profile

ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = ROOT / "config/research_profiles"


def assertion(**changes):
    return AttributeAssertion(**{
        "entity_id": "AU-one", "entity_kind": "PROJECT", "project_id": "AU-one",
        "attribute_kind": "DEVELOPMENT_STAGE", "source_sha256": "a" * 64,
        "source_edition_id": "b" * 64, "source_term": "The DFS was completed in June 2025.",
        "controlled_term": "DFS", "role": "COMPLETED", "scope": "Whole Project", "scope_kind": "PROJECT",
        "publication_date": "2026-01-01", "event_date": "2025-06", "event_date_token": "June 2025",
        "evidence_anchor_ids": ("c" * 64,), **changes,
    })


class ProjectAttributeTests(unittest.TestCase):
    def test_profile_is_opt_in(self):
        disabled = load_profile(PROFILE_DIR / "au_ree_2026-11-28.json")
        self.assertIsNone(disabled.attribute_bindings_path)
        enabled = load_profile(PROFILE_DIR / "au_ree_census_attributes_v1.json")
        self.assertTrue(enabled.attribute_bindings_path.is_file())
        self.assertTrue(enabled.attribute_milestones_path.is_file())
        self.assertEqual((enabled.profile_id, enabled.research_cutoff), (disabled.profile_id, disabled.research_cutoff))

    def test_identity_prevents_rewriting_reported_terms(self):
        row = assertion()
        self.assertEqual(AttributeAssertion.from_dict(row.to_dict()), row)
        with self.assertRaisesRegex(ContractViolation, "assertion_id differs"):
            AttributeAssertion.from_dict({**row.to_dict(), "source_term": "PFS planned"})
        with self.assertRaisesRegex(ContractViolation, "identities differ"):
            assertion(project_id="AU-two")

    def test_typed_milestone_states_do_not_become_current_stage(self):
        for native_role in ("PLANNED", "NOT_COMPLETED"):
            self.assertEqual(_development_role(native_role, "reason", "Study ongoing", None, "2026-11-28")[0],
                             native_role)
        self.assertEqual(_development_role("MENTIONED", "", "PFS is in progress.", None, "2026-11-28")[0],
                         "ONGOING")
        self.assertEqual(_development_role("MENTIONED", "", "PFS current as of 28 November 2026.",
                                           "28 November 2026", "2026-11-28")[0], "CURRENT_AS_OF_CUTOFF")
        eligible = SimpleNamespace(status="ELIGIBLE")
        for role in ("MENTIONED", "PLANNED", "ONGOING", "CURRENT_AS_OF_CUTOFF", "NOT_COMPLETED"):
            self.assertEqual(_verdict(assertion(role=role), eligible, "candidate")[0], "CANDIDATE")
        self.assertEqual(_verdict(assertion(), eligible, "explicit")[0], "VALIDATED")
        self.assertEqual(_verdict(assertion(), SimpleNamespace(status="CANDIDATE", reason="no date"),
                                  "explicit"), ("EVIDENCE_INSUFFICIENT", "no date"))

    def test_narrower_phase_is_rejected_even_with_edited_scope_kind(self):
        quote = "The DFS was completed in June 2025 for the Phase 1 operation."
        outcome = _verdict(assertion(source_term=quote), SimpleNamespace(status="ELIGIBLE"), "explicit")
        self.assertEqual(outcome, ("EVIDENCE_INSUFFICIENT", "NARROWER_REPORT_SCOPE_THAN_CENSUS_PROJECT"))

    def test_conflicting_assertions_never_choose_a_stage_by_order(self):
        def evidence(row):
            return SimpleNamespace(assertion=row, assessment=ValidatedAttribute(
                assertion_id=row.assertion_id, policy_sha256=POLICY_SHA, temporal_decision_id="e" * 64,
                temporal_status="ELIGIBLE", verdict="VALIDATED", reason="test",
            ))

        initial = assertion()
        earlier = assertion(controlled_term="PFS", event_date="2024", event_date_token="2024")
        selected, conflicts = _select_attributes({"first": evidence(initial), "next": evidence(earlier)},
                                                 "f" * 64, "1" * 64, "2" * 64)
        self.assertEqual(selected, [])
        self.assertEqual(conflicts, {("AU-one", "DEVELOPMENT_STAGE"): "CONFLICTING_ELIGIBLE_ATTRIBUTE_ASSERTIONS"})
        unambiguous, _ = _select_attributes({"first": evidence(initial)}, "f" * 64, "1" * 64, "2" * 64)
        chosen = unambiguous[0][2]
        self.assertEqual(AttributeSelection.from_dict(json.loads(json.dumps(chosen.to_dict()))), chosen)
        with self.assertRaisesRegex(ContractViolation, "restricted to approved Census display"):
            replace(chosen, purpose="D1", selection_id=None)


class ExactAttributeEvidenceTests(unittest.TestCase):
    def test_real_pdf_positive_and_missing_source_isolation(self):
        import os
        from hashlib import sha256
        from shutil import copyfile
        from tempfile import TemporaryDirectory
        from au_ree_v3.census_metadata import prepare_census_metadata
        from au_ree_v3.fresh_research import verify_source_manifest
        from au_ree_v3.registry import load_registry
        from au_ree_v3.sources import ContentStore

        if not (os.environ.get("AU_REE_MILESTONE_MANIFEST") and os.environ.get("AU_REE_MILESTONE_ASSETS")):
            self.skipTest("fixed 144-SHA manifest and source CAS are required")
        assets = Path(os.environ["AU_REE_MILESTONE_ASSETS"])
        profile = PROFILE_DIR / "au_ree_census_attributes_v1.json"
        settings = load_profile(profile)
        registry = load_registry(settings.registry_path)
        manifest_bytes = Path(os.environ["AU_REE_MILESTONE_MANIFEST"]).read_bytes()
        manifest = json.loads(manifest_bytes)
        source_receipt = verify_source_manifest(settings, manifest, registry, ContentStore(assets))
        pinned = {"source_manifest_sha256": sha256(manifest_bytes).hexdigest(), "release_id": "b" * 64}
        metadata = prepare_census_metadata(profile, manifest, source_receipt, attribute_assets=assets, **pinned)
        self.assertEqual(metadata.attribute_receipt["selected_attributes"], 2)
        self.assertEqual(metadata.attribute_receipt["display_cells"], 5)
        self.assertEqual(metadata.cells["AU-efff91a06cf344122d88", "礦床型態代碼"]["value"],
                         "CARBONATITE_SUPERGENE_REGOLITH")
        self.assertEqual(metadata.cells["AU-524c37fc04cf47bf8dca", "開發階段代碼"]["value"], "DFS")
        self.assertNotIn(("AU-49b538453133bd968e8d", "開發階段代碼"), metadata.cells)
        missing = next(row["source_sha256"] for row in metadata.attribute_receipt["cases"]
                       if row["case_id"] == "mt-weld-issuer-deposit-type")
        with TemporaryDirectory() as directory:
            limited = Path(directory)
            objects = limited / "objects"
            objects.mkdir()
            captures = {row["capture_sha256"] for row in metadata.attribute_receipt["cases"]}
            needed = {
                digest for edition in manifest["editions"] if edition["receipt_sha256"] in captures
                for digest in (edition["sha256"], *(ref["reference_sha256"] for ref in edition["temporal_evidence"]))
            }
            for digest in needed - {missing}:
                copyfile(assets / "objects" / digest, objects / digest)
            isolated = prepare_census_metadata(profile, manifest, source_receipt, attribute_assets=limited, **pinned)
        self.assertNotIn(("AU-efff91a06cf344122d88", "礦床型態代碼"), isolated.cells)
        self.assertIn(("AU-524c37fc04cf47bf8dca", "開發階段代碼"), isolated.cells)
        self.assertEqual(isolated.attribute_receipt["verdicts"]["SOURCE_UNAVAILABLE"], 2)
