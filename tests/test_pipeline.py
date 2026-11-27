"""Synthetic PDF replay and exact edition checks for the resource slice."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from au_ree_v3.application import replay
from au_ree_v3.contracts import (
    AcceptedResearchValue, ContractViolation, RawObservation, ResearchRelease, Source, ValidatedObservation,
)
from au_ree_v3.contracts.temporal import TEMPORAL_POLICY_VERSION
from au_ree_v3.migration.reference_oracle import compare_files
from au_ree_v3.observations import digest
from au_ree_v3.store import Store

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config/migration/mt_weld_resource_binding_v1.json"
HEADER = ("5 August 2024", "Release updated from 30 June 2018 to 30 June 2024.")
NARRATIVE = (
    "The Fixture Rare Earth Mineral Resources estimate is now 14.2 million tonnes",
    "at an average grade of 5.02% Total Rare Earth Oxide (TREO)",
    "for a total of 0.71 million tonnes of contained TREO.",
    "A Mineral Resource cut-off grade of 2.5% TREO was applied.",
    "The resource has been estimated in accordance with JORC 2012 guidelines",
    "and is summarised in Table 1.",
)
TABLE = (
    "Table 1: Fixture Rare Earth Deposit Mineral Resource summary June 2024 at 2.5% TREO cut-off grade",
    "JORC Classification    Million Tonnes    TREO* (%)    TREO* ('000 tonnes)",
    "Measured       4.0      7.2       288",
    "Indicated      3.1      5.4       167",
    "Inferred       7.1      3.6       257",
    "Total         14.2      5.0       712",
    "*TREO = total Rare Earth Oxides (La2O3, CeO2) + Yttrium (Y2O3).",
)
CODE = "a" * 40


def pdf_fixture(pages: list[tuple[str, ...]]) -> bytes:
    """Write a minimal standards-compliant PDF with synthetic native text."""
    page_ids = [4 + 2 * i for i in range(len(pages))]
    children = " ".join(f"{page} 0 R" for page in page_ids)
    objects = ["<< /Type /Catalog /Pages 2 0 R >>",
               f"<< /Type /Pages /Kids [{children}] /Count {len(pages)} >>",
               "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    for page, lines in zip(page_ids, pages):
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 660 792] "
                       f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page + 1} 0 R >>")
        escaped = (line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines)
        commands = ["BT /F1 9 Tf 40 730 Td 14 TL", *(f"({line}) Tj T*" for line in escaped), "ET"]
        stream = "\n".join(commands) + "\n"
        objects.append(f"<< /Length {len(stream.encode('ascii'))} >>\nstream\n{stream}endstream")
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, item in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{number} 0 obj\n{item}\nendobj\n".encode("ascii"))
    xref = len(data)
    data.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    data.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(data)


@unittest.skipUnless(shutil.which("pdftotext"), "Poppler pdftotext required for PDF integration")
class ResourcePipelineTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.assets = self.folder / "assets"
        self.assets.mkdir()
        self.spec = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.spec["bindings"] = []

    def binding(self, label: str, *, pages=None, wrong_bytes=False, write=True, deposit="Fixture"):
        original = json.loads(MANIFEST.read_text(encoding="utf-8"))["bindings"][0]
        evidence = tuple(line.replace("Fixture", deposit) for line in NARRATIVE + TABLE)
        content = pdf_fixture(pages or [(HEADER[0], HEADER[1], label), evidence])
        sha = hashlib.sha256(content).hexdigest()
        original["case_id"] = label
        original["deposit_name"] = deposit
        original["statement_id"] = f"{label}-2024-resource"
        original["scope_name"] = f"{deposit} Mineral Resource"
        original["source"].update(
            source_key=label, sha256=sha, source_version=sha, url=f"https://example.invalid/{label}.pdf",
        )
        original["legacy_claim_reference"] = {"synthetic": True, "path": "tests/test_pipeline.py"}
        original["temporal_evidence"] = [{
            "source_sha256": sha,
            "kind": "ARCHIVED_PUBLIC_CAPTURE",
            "public_on": "2024-08-05",
            "reference": f"synthetic:{label}:reviewed-public-capture",
            "reference_sha256": hashlib.sha256(f"synthetic-public-capture:{label}".encode()).hexdigest(),
        }]
        original["project"].update(
            name="Fixture Rare Earth Project", native_id=label,
            entity_id="AU-" + digest(["project_registry_v1", label])[:20],
        )
        if write:
            (self.assets / sha).write_bytes(b"%PDF-1.4 wrong edition" if wrong_bytes else content)
        self.spec["bindings"].append(original)
        return original

    def run_replay(self, name="first", database="scratch.sqlite"):
        spec_path = self.folder / "spec.json"
        spec_path.write_text(json.dumps(self.spec), encoding="utf-8")
        output = self.folder / name
        return replay(spec_path, self.assets, self.folder / database, output, code_commit=CODE)

    def test_clean_and_repeated_replay_are_equivalent(self):
        self.binding("exact")
        initial = self.run_replay()
        self.assertEqual(initial["status"], "PASS", initial)
        self.assertEqual(initial["release_mode"], "EXPERIMENTAL")
        self.assertEqual(initial["sqlite_integrity"], {"integrity_check": ["ok"], "foreign_key_check": []})
        self.assertEqual(initial["observations"], 3)
        repeated = self.run_replay("same")
        clean = self.run_replay("clean", database="clean.sqlite")
        self.assertEqual(initial, repeated)
        self.assertEqual(initial, clean)
        for name in ("v3-observations.jsonl", "v3-evidence.jsonl", "census.csv"):
            self.assertEqual((self.folder / "first" / name).read_bytes(),
                             (self.folder / "clean" / name).read_bytes())
        with Store(self.folder / "scratch.sqlite") as store:
            actual = {row.variable: row.raw_value for row in store.records(RawObservation)}
        self.assertEqual(actual, {"ore_mt": "14.2", "grade": "5.02", "contained_treo_kt": "712"})

    def test_missing_and_wrong_sha_are_isolated(self):
        self.binding("primary")
        self.binding("missing", write=False)
        self.binding("mismatch", wrong_bytes=True)
        result = self.run_replay()
        self.assertEqual(result["status"], "FAIL", result)
        self.assertEqual(result["observations"], 3)
        self.assertEqual(
            [(x["case_id"], x["outcome"], x["reason"]) for x in result["cases"]],
            [("mismatch", "SCIENTIFIC_CONFLICT", "WRONG_SHA"),
             ("missing", "SOURCE_UNAVAILABLE", "MISSING"),
             ("primary", "VALIDATED", "BOUND_SAME_PAGE_V2_DECLARATION")],
        )
        census = (self.folder / "first/census.csv").read_text()
        self.assertEqual(census.count("PRESENT"), 3)
        self.assertEqual(census.count("UNKNOWN"), 6)

    def test_cross_page_text_cannot_support_a_statement(self):
        self.binding("across-pages", pages=[HEADER, NARRATIVE, TABLE])
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual(result["cases"][0]["outcome"], "EVIDENCE_INSUFFICIENT")
        self.assertEqual(result["cases"][0]["reason"], "STATEMENT_EVIDENCE_MISSING")

    def test_source_after_cutoff_cannot_be_selected(self):
        binding = self.binding("late", pages=[("14 November 2026", HEADER[1]), NARRATIVE + TABLE])
        binding["source"].update(
            publication_date="2026-11-14", available_date="2026-11-14", acquired_at="2026-11-14T11:56:00Z",
        )
        binding["temporal_evidence"][0]["public_on"] = "2026-11-14"
        with patch("au_ree_v3.application.pdf_pages") as parse:
            result = self.run_replay()
        parse.assert_not_called()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual(result["source_pdf_count"], 1)
        self.assertEqual(result["parsed_pdf_count"], 0)
        decision = result["cases"][0]
        self.assertEqual(decision["outcome"], "AFTER_CUTOFF")
        self.assertEqual(decision["reason"], "PUBLICATION_AFTER_CUTOFF")
        self.assertEqual(decision["source_integrity"], "EXACT_SHA")
        self.assertEqual(decision["observed_sha256"], decision["source_sha256"])
        self.assertEqual(decision["scientific_comparability"], "NOT_EVALUATED")

    def test_postcutoff_reacquisition_of_public_edition_retains_selection(self):
        binding = self.binding("exact")
        first = self.run_replay()
        binding["source"]["reacquired_at"] = "2026-11-25T16:00:00Z"
        result = self.run_replay("reacquired")
        self.assertEqual(first["status"], "PASS", first)
        self.assertEqual(result["status"], "PASS", result)
        self.assertNotEqual(first["release_id"], result["release_id"])
        self.assertEqual(first["scientific_fingerprint"], result["scientific_fingerprint"])
        self.assertEqual(result["temporal_policy_version"], TEMPORAL_POLICY_VERSION)
        self.assertEqual(result["observations"], 3)
        temporal = result["cases"][0]["observation_temporal"]
        self.assertEqual(temporal["reacquired_at"], "2026-11-25T16:00:00Z")
        self.assertEqual(temporal["reason"], "EXACT_EDITION_PUBLIC_BY_CUTOFF")
        evidence = [json.loads(row) for row in (self.folder / "reacquired/v3-evidence.jsonl").read_text().splitlines()]
        self.assertEqual(len(evidence), 3)
        for row in evidence:
            self.assertEqual(row["temporal_lineage"]["temporal_decision"]["decision_id"], temporal["decision_id"])
        with Store(self.folder / "scratch.sqlite") as store:
            sources = store.records(Source)
            self.assertEqual(len(sources), 1)
            self.assertIsNone(sources[0].reacquired_at)
            for receipt, timestamp in ((first, None), (result, binding["source"]["reacquired_at"])):
                release = store.get(ResearchRelease, receipt["release_id"])
                accepted = store.accepted_for_release(release.release_id)
                self.assertEqual(len(accepted), 3)
                for value in accepted:
                    trace = store.selection_temporal_trace(value, release)
                    self.assertEqual(trace["reacquired_at"], timestamp)

    def test_registry_selection_date_does_not_replace_exact_public_proof(self):
        binding = self.binding("exact")
        binding["source"].update(available_date="2026-11-14", availability_role="REGISTRY_SELECTION")
        result = self.run_replay()
        self.assertEqual(result["status"], "PASS", result)
        decision = result["cases"][0]["observation_temporal"]
        self.assertEqual(decision["available_date"], "2026-11-14")
        self.assertEqual(decision["availability_role"], "REGISTRY_SELECTION")
        self.assertEqual(decision["earliest_proven_public_date"], "2024-08-05")

    def test_old_date_and_pre_cutoff_acquisition_do_not_establish_exact_public_proof(self):
        missing = self.binding("unproven")
        del missing["temporal_evidence"]
        foreign = self.binding("wrong-edition-proof")
        foreign["temporal_evidence"][0]["source_sha256"] = "f" * 64
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(
            {row["observation_temporal"]["reason"] for row in result["cases"]},
            {"PUBLIC_EVIDENCE_MISSING", "NO_SAME_EDITION_PUBLIC_EVIDENCE"},
        )
        self.assertEqual({row["observation_temporal"]["status"] for row in result["cases"]}, {"CANDIDATE"})
        with Store(self.folder / "scratch.sqlite") as store:
            self.assertEqual(len(store.records(RawObservation)), 6)
            self.assertEqual(len(store.records(ValidatedObservation)), 6)
            self.assertEqual(store.records(AcceptedResearchValue), [])

    def test_partial_effective_date_candidate_isolates_other_valid_sources(self):
        self.binding("uncertain")["effective_date"] = "2026-11"
        self.binding("exact")
        result = self.run_replay()
        self.assertEqual(result["status"], "BLOCKED", result)
        self.assertEqual(result["observations"], 3)
        uncertain = next(case for case in result["cases"] if case["case_id"] == "uncertain")
        self.assertEqual(uncertain["observed_sha256"], uncertain["source_sha256"])
        self.assertEqual(uncertain["observation_temporal"]["effective_date"], "2026-11")
        self.assertEqual(uncertain["reason"], "HISTORICAL_DATE_PRECISION_SPANS_CUTOFF")

    def test_unclassified_availability_conflict_is_not_treated_as_public_access(self):
        for label, wrong in (("exact-conflict", False), ("wrong-conflict", True)):
            self.binding(label, wrong_bytes=wrong)["source"]["available_date"] = "2026-11-14"
        with patch("au_ree_v3.application.pdf_pages") as parse:
            result = self.run_replay()
        parse.assert_not_called()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual(
            [case["source_integrity"] for case in result["cases"]], ["EXACT_SHA", "WRONG_SHA"],
        )
        for case in result["cases"]:
            self.assertEqual(case["scientific_comparability"], "NOT_EVALUATED")
            decision = case["observation_temporal"]
            self.assertEqual(decision["status"], "CONFLICT")
            self.assertEqual(decision["reason"], "UNCLASSIFIED_AVAILABILITY_AFTER_ACQUISITION")

    def test_wrong_material_or_basis_is_rejected(self):
        self.binding("material")["material_basis"] = "HEAVY_MINERAL"
        self.binding("chemistry")["chemical_basis"] = "TREO+Sc2O3"
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual({(row["outcome"], row["reason"]) for row in result["cases"]},
                         {("SCIENTIFIC_CONFLICT", "BASIS_MISMATCH")})

    def test_competing_scopes_remain_unselected(self):
        left = self.binding("left")
        right = self.binding("right", deposit="Other")
        right["project"] = left["project"]
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual({(row["outcome"], row["reason"]) for row in result["cases"]},
                         {("SCIENTIFIC_CONFLICT", "COMPETING_STATEMENTS")})
        with Store(self.folder / "scratch.sqlite") as store:
            self.assertEqual(len(store.records(RawObservation)), 6)

    def test_evidence_does_not_admit_a_different_scope_or_exploration_target(self):
        self.binding("mismatched_scope")["scope_name"] = "Other Mineral Resource"
        self.binding("target")["category"] = "EXPLORATION_TARGET"
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual({row["reason"] for row in result["cases"]}, {"SCOPE_BINDING_MISMATCH", "BASIS_MISMATCH"})

    def test_oracle_matches_synthetic_rejections_and_empty_census(self):
        self.spec["profile"] = "synthetic-resource-v1"
        self.binding("across-pages", pages=[HEADER, NARRATIVE, TABLE])
        self.binding("chemistry")["chemical_basis"] = "TREO+Sc2O3"
        conflict_table = (*TABLE[:-2], TABLE[-2].replace("712", "912"), TABLE[-1])
        self.binding("conflict", pages=[HEADER, NARRATIVE + conflict_table])
        binding = self.binding("late", pages=[("14 November 2026", HEADER[1]), NARRATIVE + TABLE])
        binding["source"].update(
            publication_date="2026-11-14", available_date="2026-11-14", acquired_at="2026-11-14T11:56:00Z",
        )
        binding["temporal_evidence"][0]["public_on"] = "2026-11-14"
        left = self.binding("left")
        self.binding("right", deposit="Other")["project"] = left["project"]
        self.binding("material")["material_basis"] = "HEAVY_MINERAL"
        self.binding("mismatch", wrong_bytes=True)
        self.binding("mismatched_scope")["scope_name"] = "Other Mineral Resource"
        self.binding("missing", write=False)
        self.binding("target")["category"] = "EXPLORATION_TARGET"
        self.binding("independent-positive")

        result = self.run_replay()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["observations"], 3)
        result = compare_files(
            ROOT / "tests/golden/synthetic_pipeline_expected_temporal_v1.jsonl",
            self.folder / "first/v3-oracle-rejections.jsonl", self.folder / "rejection-oracle",
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["verified_synthetic_count"], 11)

    def test_pipeline_transaction_rolls_back_prior_source(self):
        self.binding("left")
        self.binding("right")
        add = Store.add

        def fail_second_source(store, record):
            if isinstance(record, RawObservation) and record.source_key == "right":
                raise ContractViolation("injected interrupted write")
            return add(store, record)

        with patch.object(Store, "add", fail_second_source):
            result = self.run_replay()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["outcome"], "CODE_ERROR")
        with Store(self.folder / "scratch.sqlite") as store:
            self.assertEqual(store.records(RawObservation), [])
        self.assertEqual(self.run_replay("retry")["observations"], 6)

    def test_inconsistent_table_total_does_not_fall_back_to_narrative(self):
        table = list(TABLE)
        table[-2] = table[-2].replace("712", "912")
        self.binding("conflict", pages=[HEADER, NARRATIVE + tuple(table)])
        result = self.run_replay()
        self.assertEqual(result["observations"], 0, result)
        self.assertEqual(result["cases"][0]["reason"], "TABLE_NARRATIVE_CONFLICT")


@unittest.skipUnless(os.environ.get("AU_REE_REPLAY_ASSETS"), "locked real PDF asset library required")
class ExactResourceReplayTests(unittest.TestCase):
    def test_mt_weld_replay_matches_fixed_values_and_table_nonselection(self):
        project = "AU-efff91a06cf344122d88"
        variables = {"ore_mt", "grade", "contained_treo_kt"}
        reference = Path(os.environ.get("AU_REE_GOLDEN_EXPECTED", ROOT / "tests/golden/golden_expected.jsonl"))
        rows = [json.loads(line) for line in reference.read_text(encoding="utf-8").splitlines()]
        cases = []
        table_reference = []
        for row in rows:
            values = row.get("values", {})
            if values.get("project_id") != project:
                continue
            if (":census:" in row["case_id"] or
                    values.get("variable") in variables and values.get("selected_for_census") is True):
                cases.append(row["case_id"])
            if values.get("selected_for_census") is False and values.get("selection_role") == "NOT_SELECTED":
                table_reference.append({
                    "evidence_tier": row["evidence_tier"],
                    "reference_sha256": row["reference_sha256"],
                    "expected_source": row["expected_source"],
                    "case_id": f"archived-census-2026-11-13:table-alternative:{project}:{values['variable']}",
                    "values": {
                        **{key: values[key] for key in (
                            "project_id", "research_cutoff", "source_sha256", "physical_page", "variable",
                            "scope", "value", "unit", "selected_for_census",
                        )},
                        "raw_tokens": json.loads(values["raw_value"]),
                    },
                })
        self.assertEqual(len(cases), 6)
        self.assertEqual(len(table_reference), 3)
        assets = Path(os.environ["AU_REE_REPLAY_ASSETS"])
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            first = work / "first"
            second = work / "second"
            table_expected = work / "table-reference.jsonl"
            table_expected.write_text("".join(json.dumps(row) + "\n" for row in table_reference), encoding="utf-8")
            for out in (first, second):
                result = replay(MANIFEST, assets, out / "observations.sqlite", out, code_commit=CODE)
                self.assertEqual(result["status"], "PASS", result)
                self.assertEqual(result["source_pdf_count"], 1)
                temporal = result["cases"][0]["observation_temporal"]
                self.assertEqual(temporal["earliest_proven_public_date"], "2026-11-02")
                self.assertEqual(temporal["publication_date"], "2024-08-05")
                self.assertEqual(temporal["reason"], "EXACT_EDITION_PUBLIC_BY_CUTOFF")
                result = compare_files(reference, out / "v3-oracle-actual.jsonl", out / "oracle", case_ids=cases)
                self.assertEqual(result["status"], "PASS", result)
                self.assertEqual(result["verified_reference_count"], 6)
                result = compare_files(
                    table_expected, out / "v3-table-alternatives.jsonl", out / "table-oracle",
                )
                self.assertEqual(result["status"], "PASS", result)
                self.assertEqual(result["verified_reference_count"], 3)
            for artifact in (
                "v3-oracle-actual.jsonl", "v3-evidence.jsonl", "v3-table-alternatives.jsonl", "census.csv", "replay.json",
            ):
                self.assertEqual((first / artifact).read_bytes(), (second / artifact).read_bytes())


if __name__ == "__main__":
    unittest.main()
