from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import json
import os
import unittest

from au_ree_v3.census_metadata import prepare_census_metadata
from au_ree_v3.contracts import ContractViolation
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.project_attributes import (
    AttributeAssertion, AttributeSelection, POLICY_SHA, ValidatedAttribute,
    _development_role, _select_attributes, _verdict,
)
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.sources import ContentStore

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

    def test_expanded_opt_in_keeps_original_bindings(self):
        previous = load_profile(PROFILE_DIR / "au_ree_census_attributes_v1.json")
        expanded = load_profile(PROFILE_DIR / "au_ree_census_attributes_v2.json")
        self.assertEqual((expanded.registry_path, expanded.census_columns_path, expanded.source_catalog_path),
                         (previous.registry_path, previous.census_columns_path, previous.source_catalog_path))
        self.assertNotEqual(previous.sha256, expanded.sha256)
        original_attrs, new_attrs = (json.loads(settings.attribute_bindings_path.read_bytes())
                                     for settings in (previous, expanded))
        self.assertEqual(original_attrs["deposit_types"], new_attrs["deposit_types"])
        self.assertEqual(original_attrs["development_milestones"],
                         new_attrs["development_milestones"][:len(original_attrs["development_milestones"])])
        original_cases, new_cases = (json.loads(settings.attribute_milestones_path.read_bytes())["bindings"]
                                       for settings in (previous, expanded))
        self.assertEqual(original_cases, new_cases[:len(original_cases)])

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
    def setUp(self):
        if not (os.environ.get("AU_REE_MILESTONE_MANIFEST") and os.environ.get("AU_REE_MILESTONE_ASSETS")):
            self.skipTest("fixed 144-SHA manifest and source CAS are required")
        self.assets = Path(os.environ["AU_REE_MILESTONE_ASSETS"])
        self.manifest_bytes = Path(os.environ["AU_REE_MILESTONE_MANIFEST"]).read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        self.pinned = {"source_manifest_sha256": sha256(self.manifest_bytes).hexdigest(), "release_id": "b" * 64}

    def _real_profile(self, version):
        profile = PROFILE_DIR / f"au_ree_census_attributes_v{version}.json"
        settings = load_profile(profile)
        registry = load_registry(settings.registry_path)
        receipt = verify_source_manifest(settings, self.manifest, registry, ContentStore(self.assets))
        return profile, receipt

    def test_real_pdf_positive_and_missing_source_isolation(self):
        from shutil import copyfile
        from tempfile import TemporaryDirectory

        profile, receipt = self._real_profile(1)
        metadata = prepare_census_metadata(profile, self.manifest, receipt, attribute_assets=self.assets, **self.pinned)
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
                digest for edition in self.manifest["editions"] if edition["receipt_sha256"] in captures
                for digest in (edition["sha256"], *(ref["reference_sha256"] for ref in edition["temporal_evidence"]))
            }
            for digest in needed - {missing}:
                copyfile(self.assets / "objects" / digest, objects / digest)
            isolated = prepare_census_metadata(profile, self.manifest, receipt, attribute_assets=limited, **self.pinned)
        self.assertNotIn(("AU-efff91a06cf344122d88", "礦床型態代碼"), isolated.cells)
        self.assertIn(("AU-524c37fc04cf47bf8dca", "開發階段代碼"), isolated.cells)
        self.assertEqual(isolated.attribute_receipt["verdicts"]["SOURCE_UNAVAILABLE"], 2)

    def test_expanded_profile_exact_completed_studies_and_refusals(self):
        profile, receipt = self._real_profile(2)
        metadata = prepare_census_metadata(profile, self.manifest, receipt, attribute_assets=self.assets, **self.pinned)
        self.assertEqual(metadata.attribute_receipt["selected_attributes"], 4)
        self.assertEqual(metadata.attribute_receipt["display_cells"], 11)
        self.assertEqual(metadata.attribute_receipt["mcda_inputs_added"], 0)
        expected = {"avonbank-dfs-completed": ("AU-329908ab8d4c1d1d3e03", "2021", 46),
                    "copi-dfs-completed": ("AU-fe65cb1bd28eacb7a67f", "2024-01", 47)}
        cases = {row["case_id"]: row for row in metadata.attribute_receipt["cases"]}
        for case, (project, event_date, page) in expected.items():
            with self.subTest(case=case):
                entry = cases[case]
                self.assertEqual((entry["project_id"], entry["assertion"]["event_date"], entry["page"]),
                                 (project, event_date, page))
                self.assertEqual(entry["assertion"]["publication_date"], "2026-03")
                self.assertEqual(entry["selection"]["purpose"], "CENSUS_DISPLAY_ONLY")
                self.assertEqual(metadata.cells[project, "開發階段代碼"]["value"], "DFS")
        for case, reason in (
            ("goschen-dfs-explicit-completion-date-unreported", "HISTORICAL_OBSERVATION_DATE_MISSING"),
            ("dubbo-dfs-project-scope-not-local", "PROJECT_SCOPE_NOT_LOCAL_TO_MILESTONE"),
        ):
            self.assertEqual(cases[case]["reason"], reason)
            self.assertIsNone(cases[case]["selection"])
            self.assertNotIn((cases[case]["project_id"], "開發階段代碼"), metadata.cells)


class DatedStudyEventsTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv("AU_REE_FILL_T7_CAS"), "Stage 6 exact archive CAS is required")
    def test_fixed_issuer_events_and_old_attributes(self):
        assets = Path(os.environ["AU_REE_FILL_T7_CAS"])
        store = ContentStore(assets)
        manifest_sha = "4ed939586a90708da79e95072eef363905e568e1463cb79a1c546ac379feff6a"
        baseline = store.read("dc50a0fe07adc957b05c8990a77b3b4c77d1a479bb6319d6a74296f5d5482dbd")
        manifest = json.loads(store.read(manifest_sha))
        names = {"attribute_bindings": "census_attribute_bindings_v3.json",
                 "attribute_milestones": "development_milestone_bindings_v3.json"}
        with TemporaryDirectory() as directory:
            workspace = Path(directory)
            baseline_path = workspace / "baseline.json"
            baseline_path.write_bytes(baseline)
            profile = json.loads(baseline)
            for pin in profile["inputs"].values():
                (workspace / pin["path"]).write_bytes(store.read(pin["sha256"]))
            for kind, name in names.items():
                data = (PROFILE_DIR / name).read_bytes()
                (workspace / name).write_bytes(data)
                profile["inputs"][kind] = {"path": name, "sha256": sha256(data).hexdigest()}
            current_path = workspace / "candidate.json"
            current_path.write_text(json.dumps(profile))
            settings = load_profile(current_path)
            registry = load_registry(settings.registry_path)
            source_receipt = verify_source_manifest(settings, manifest, registry, store)
            previous, current = (
                prepare_census_metadata(path, manifest, source_receipt, attribute_assets=assets,
                                        source_manifest_sha256=manifest_sha, release_id="b" * 64)
                for path in (baseline_path, current_path)
            )
        expected = {
            "north-stanmore-pfs-results-released": ("VALIDATED", "STUDY_RESULTS_RELEASED", "2026-08", 8, True, None),
            "nolans-dfs-results-released": ("VALIDATED", "STUDY_RESULTS_RELEASED", "2019-02-07", 1, True, None),
            "cummins-range-scoping-completed-august-2023": ("VALIDATED", "COMPLETED", "2023-08", 12, True, None),
            "cummins-range-scoping-completed-september-2022": (
                "VALIDATED", "COMPLETED", "2022-09", 13, False, "HISTORICAL_MILESTONE_RETAINED",
            ),
            "north-stanmore-prior-pfs-planned": ("CANDIDATE", "PLANNED", None, 1, False, None),
            "nolans-early-construction-works": ("CANDIDATE", "EARLY_WORKS", None, 1, False, None),
        }
        actual = {
            item["case_id"]: (
                item["verdict"], item["assertion"]["role"], item["assertion"]["event_date"], item["page"],
                item["selection"] is not None, item.get("selection_blocker"),
            )
            for item in current.attribute_receipt["cases"] if item["case_id"] in expected
        }
        self.assertEqual(actual, expected)
        self.assertEqual((previous.attribute_receipt["display_cells"], current.attribute_receipt["display_cells"]),
                         (27, 36))
        self.assertEqual(current.attribute_receipt["mcda_inputs_added"], 0)
        for key, row in previous.cells.items():
            after = current.cells[key]
            self.assertEqual({field: after.get(field) for field in ("value", "source_sha256", "anchor_id")},
                             {field: row.get(field) for field in ("value", "source_sha256", "anchor_id")})
