"""Verify fresh extraction from independently read issuer PDF cells and real capture receipts."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest

from au_ree_v3.contracts import ResearchRelease
from au_ree_v3.fresh_observations import _select, extract_sources
from au_ree_v3.research_inputs import checked_research_inputs
from au_ree_v3.sources import ContentStore
from au_ree_v3.store import Store


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/fresh_resource_v1"
BINDINGS = ROOT / "config/research_profiles/resource_bindings_v1.json"
CODE_COMMIT = "a" * 40


class ResourceScopeSelectionTests(unittest.TestCase):
    @staticmethod
    def statement(scope, effective_date, value, *, primary=True):
        measurement = SimpleNamespace(
            raw=SimpleNamespace(variable="ore_mt"), candidate=SimpleNamespace(value=value),
            validation=SimpleNamespace(outcome="VALIDATED"),
        )
        return SimpleNamespace(
            statement=SimpleNamespace(primary=primary, effective_date=effective_date),
            decision=SimpleNamespace(status="ELIGIBLE"), project=SimpleNamespace(entity_id="project"),
            scope=SimpleNamespace(scope_id=scope), source=SimpleNamespace(sha256="a", source_key="source"),
            measurements=(measurement,),
        )

    def test_select_latest_compatible_effective_date_and_keep_subscope_out(self):
        first = self.statement("approved", "2025-01-01", "1")
        latest = self.statement("approved", "2026-01-01", "2")
        alternate = self.statement("subscope", "2026-02-01", "3", primary=False)
        selection, reasons = _select([latest, first, alternate])
        self.assertEqual(selection["project"], latest.measurements)
        self.assertEqual(reasons["project"], "APPROVED_REPORTING_SCOPE")

    def test_competing_scope_and_same_date_values_do_not_select(self):
        primary = self.statement("whole", "2025-01-01", "1")
        for second, reason in (
            (self.statement("different-cutoff", "2026-01-01", "2"), "CONFLICTING_RESEARCH_SCOPES"),
            (self.statement("whole", "2025-01-01", "2"), "CONFLICTING_SAME_DATE_ESTIMATES"),
        ):
            with self.subTest(reason=reason):
                selection, reasons = _select([primary, second])
                self.assertFalse(selection)
                self.assertEqual(reasons["project"], reason)


@unittest.skipUnless(os.environ.get("AU_REE_FRESH_ASSETS") and shutil.which("pdftotext"),
                     "locked original issuer PDFs and Poppler required")
class FreshObservationIntegrationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.work = Path(directory.name)
        self.assets = Path(os.environ["AU_REE_FRESH_ASSETS"])
        self.manifest = json.loads((FIXTURE / "acquisition_manifest.json").read_bytes())
        self.store = ContentStore(self.work / "source-store")
        for path in (FIXTURE / "captures").glob("*.json"):
            self.assertEqual(self.store.put(path.read_bytes()), path.stem)
        for source in self.manifest["editions"]:
            expected = source["sha256"]
            paths = [self.assets / folder / expected for folder in ("objects", "assets", "")]
            source_path = next((path for path in paths if path.is_file()), None)
            self.assertIsNotNone(source_path, f"Missing locked original {expected}")
            self.assertEqual(self.store.put(source_path.read_bytes()), expected)

    def _run(self, name, manifest=None, *, database=None):
        root = self.work / name
        root.mkdir()
        path = root / "acquisition.json"
        path.write_text(json.dumps(manifest if manifest is not None else self.manifest), encoding="utf-8")
        db = database or root / "observations.sqlite"
        result = extract_sources(BINDINGS, path, self.store.root, db, root, code_commit=CODE_COMMIT)
        rows = [json.loads(line) for line in (root / "observations.jsonl").read_bytes().splitlines()]
        return result, rows, db

    def test_three_real_modes_and_independent_measurement_and_rejection_checks(self):
        result, rows, database = self._run("first")
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["release_mode"], "FORMAL")
        self.assertEqual(result["verified_source_mode_count"], 3)
        self.assertEqual(result["selected_mode_count"], 2)
        self.assertEqual(result["candidate_count"], 24)
        self.assertEqual(result["validated_count"], 12)
        self.assertEqual(result["selected_count"], 6)
        self.assertEqual(result["sqlite_integrity"], {"integrity_check": ["ok"], "foreign_key_check": []})
        cases = {case["case_id"]: case for case in result["cases"]}
        keys = ("variable", "raw_value", "raw_unit", "value", "unit", "page", "cutoff_grade", "effective_date")
        expected = json.loads((FIXTURE / "expected.json").read_bytes())
        for reference in expected["evidence"]:
            with self.subTest(case=reference["case_id"]):
                case = cases[reference["case_id"]]
                self.assertEqual(case["source_integrity"], "EXACT_SHA")
                self.assertEqual(case["proof_status"], "EXACT_CAPTURE_PROOF")
                self.assertEqual(case["temporal_decision"]["earliest_proven_public_date"], "2026-11-28")
                source_rows = [row for row in rows if row["source_sha256"] == reference["source_sha256"]]
                self.assertTrue(source_rows)
                self.assertEqual({(row["material_basis"], row["chemical_basis"]) for row in source_rows},
                                 {("WHOLE_ORE", "TREO")})
                selected = [[row[key] for key in keys] for row in source_rows if row["selected_for_census"]]
                self.assertCountEqual(selected, reference["selected"])
                for scope, tokens in reference.get("scoped_subtotals", {}).items():
                    scoped = [row for row in source_rows if row["row"] == scope]
                    self.assertCountEqual([row["raw_value"] for row in scoped], tokens)
                    self.assertTrue(all(not row["selected_for_census"] for row in scoped))
                    self.assertEqual({row["selection_status"] for row in scoped}, {"SHADOW_OUTSIDE_REPORTING_SCOPE"})
                for threshold, expected_values in reference.get("cutoffs", {}).items():
                    scoped = [row for row in source_rows if row["cutoff_grade"] == threshold]
                    self.assertEqual(len({row["scope_id"] for row in scoped}), 1)
                    self.assertEqual({row["effective_date"] for row in scoped}, {None})
                    self.assertEqual({row["publication_date"] for row in scoped}, {"2023-11-30"})
                    for variable in ("ore_mt", "grade"):
                        row, = (row for row in scoped if row["variable"] == variable)
                        self.assertEqual(row["raw_value"], expected_values[variable])
                        self.assertEqual(row["reason"], "HISTORICAL_OBSERVATION_DATE_MISSING")
                    contained = [row for row in scoped if row["variable"] == "contained_treo_kt"]
                    self.assertCountEqual([row["raw_value"] for row in contained], expected_values["contained_treo_kt"])
                    self.assertEqual({row["page"] for row in contained}, {1, 11})
                    self.assertEqual({row["reason"] for row in contained},
                                     {"CONFLICTING_CONTAINED_TREO_IN_SAME_STATEMENT"})
        with Store(database, trusted_temporal_evidence_ids=result["trusted_temporal_evidence_ids"]) as store:
            release = store.get(ResearchRelease, result["release_id"])
            self.assertEqual(len(checked_research_inputs(store, release, purpose="MCDA")), 6)
        replay, replay_rows, _ = self._run("clean")
        self.assertEqual(result, replay)
        self.assertEqual(rows, replay_rows)
        rerun, rerun_rows, _ = self._run("same-db", database=database)
        self.assertEqual(result, rerun)
        self.assertEqual(rows, rerun_rows)

    def test_tampered_proof_and_wrong_original_do_not_escape_quarantine(self):
        manifest = deepcopy(self.manifest)
        by_source = {row["source_key"]: row for row in manifest["editions"]}
        by_source["mt_weld_2024"]["temporal_evidence"][0]["public_on"] = "2020-01-01"
        wol = by_source["asx_document_02904920"]
        self.store.path(wol["sha256"]).write_bytes(b"%PDF-1.4 altered")
        result, rows, _ = self._run("faults", manifest)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["selected_count"], 0)
        self.assertEqual(result["release_mode"], "EXPERIMENTAL")
        cases = {case["case_id"]: case for case in result["cases"]}
        self.assertEqual(cases["wolverine-deposit-total"]["source_integrity"], "WRONG_SHA")
        self.assertEqual(cases["mt-weld-resource"]["source_integrity"], "EXACT_SHA")
        self.assertEqual(cases["mt-weld-resource"]["proof_status"], "UNVERIFIED_CAPTURE_PROOF")
        self.assertEqual(cases["mt-weld-resource"]["temporal_decision"]["status"], "CANDIDATE")
        self.assertEqual(cases["yin-resource-cutoff-series"]["outcome"], "EXTRACTED")
        self.assertEqual(len(rows), 15)
        self.assertFalse(any(row["selected_for_census"] for row in rows))
