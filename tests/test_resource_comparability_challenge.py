"""Check independent Decimal and exact source witnesses for scoped R1 challenges."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest

from au_ree_v3.comparability import TOTAL_REO_15
from au_ree_v3.documents import DocumentCache, DocumentError, PDFDocument
from au_ree_v3.resource_extraction import _chemical_species, native_resource_table
from au_ree_v3.resource_selection import _statement_issue
from au_ree_v3.resource_table_binding import reviewed_table_rows
from au_ree_v3.sources import ContentStore


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config/research_profiles"
SCRIPT = ROOT / "tools/audit_resource_statement_holds.py"
CHALLENGE = PROFILES / "resource_comparability_challenge_v1.json"
COHORT = ROOT / "tests/fixtures/historical_resource_statement_review_v1.json"


def _reviewed_grade_column():
    return {"variable": "grade", "header": "TREO", "header_line": 1,
            "unit": "ppm", "unit_label": "ppm", "unit_line": 2}


class ResourceComparisonMathTest(unittest.TestCase):
    def test_treo_definition_requires_exact_species_in_original_print_formats(self):
        terms = " + ".join(TOTAL_REO_15)
        parenthetical = f"TREO (Total Rare Earth Oxide) = {terms}"
        dash_label = f"TREO = Total Rare Earth Oxides – {terms}"
        self.assertEqual(set(_chemical_species((parenthetical, dash_label))), set(TOTAL_REO_15))
        wrong = parenthetical.replace("CeO2", "Ce2O3")
        self.assertNotEqual(set(_chemical_species((wrong,))), set(TOTAL_REO_15))
        self.assertEqual(_chemical_species((parenthetical, wrong)), ())
        self.assertEqual(_chemical_species((parenthetical.replace("CeO2", "CeO2 excluded"),)), ())

    def test_frozen_statement_joins_require_original_row_and_table(self):
        join = runpy.run_path(str(SCRIPT))["_same_printed_row"]
        original = {"row_anchor": {"text": " Total   98    890"}, "caption_anchors": [{"text": "Table 1 Resource"}]}
        self.assertTrue(join({"row": "total", "table": "Table 1"}, original))
        self.assertFalse(join({"row": "Inferred", "table": "Table 1"}, original))
        self.assertFalse(join({"row": "total", "table": "Table 2"}, original))
        original["caption_anchors"][0]["text"] = "Table 10 Resource"
        self.assertFalse(join({"row": "total", "table": "Table 1"}, original))

    def test_reviewed_treo_does_not_treat_modified_analyte_as_duplicate_lane(self):
        caption = "Table 1 Hill Mineral Resource"
        heading = "Classification    Tonnes    TREO    TREO-CeO2"
        unit_line = "                    (Mt)   (ppm)     (ppm)"
        text = "\n".join((caption, heading, unit_line,
                          "Inferred              98     890       660",
                          "Total                 98     890       660"))
        spec = {"page": 1, "table": {
            "caption": caption, "header_lines": [heading, unit_line],
            "columns": [_reviewed_grade_column()],
            "rows": [{"label": "Total"}],
        }}
        recovered, = reviewed_table_rows(PDFDocument("a" * 64, (text,), "test"), spec)
        grade, _, _ = recovered[3][0]
        self.assertEqual((grade.value.text, grade.unit), ("890", "ppm"))
        ambiguous = text.replace("TREO-CeO2", "TREO")
        spec["table"]["header_lines"][0] = heading.replace("TREO-CeO2", "TREO")
        with self.assertRaisesRegex(DocumentError, "REVIEWED_COLUMN_OR_UNIT_NOT_UNIQUE"):
            tuple(reviewed_table_rows(PDFDocument("a" * 64, (ambiguous,), "test"), spec))

    def test_reports_can_disagree_when_both_masses_fit_the_same_rounded_ore_and_grade(self):
        arithmetic = runpy.run_path(str(SCRIPT))["_arithmetic"]
        yin = json.loads(CHALLENGE.read_bytes())["cases"][0]
        quotes = {row["id"]: row["quote"] for edition in yin["editions"] for row in edition["witnesses"]}
        verdicts = arithmetic(yin, quotes)
        self.assertEqual([item["intersects"] for item in verdicts], [False, True, True, True])
        self.assertEqual(verdicts[0]["left_bounds_kt"], ["311.750", "311.850"])
        self.assertEqual(verdicts[0]["right_bounds_kt"], ["312.250", "312.350"])
        self.assertEqual(verdicts[2]["left_bounds_kt"], ["310.241250", "313.343250"])
        bad_quote = dict(quotes, **{"yin-detail-units": "Contained TREO (%)"})
        with self.assertRaisesRegex(ValueError, "unit is absent"):
            arithmetic(yin, bad_quote)
        changed_scope = deepcopy(yin)
        changed_scope["decimal_checks"][0]["inputs"][0]["token"] = "311,799"
        with self.assertRaisesRegex(ValueError, "token is absent"):
            arithmetic(changed_scope, quotes)


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "AU_REE_R1_CHALLENGE_BUILD", "AU_REE_R1_CHALLENGE_FIXED", "AU_REE_R1_CHALLENGE_ASSETS", "AU_REE_R1_CHALLENGE_ANNEX",
)), "separately captured original resources and fixed exact release required")
class ExactResourceComparabilityChallengeTest(unittest.TestCase):
    def test_independent_source_readback_and_formal_lineage_refusal(self):
        audit = runpy.run_path(str(SCRIPT))["audit_comparability"]
        profile = PROFILES / "au_ree_2026-11-28.json"
        fixed, assets, build, root = (Path(os.environ[name]) for name in (
            "AU_REE_R1_CHALLENGE_FIXED", "AU_REE_R1_CHALLENGE_ASSETS", "AU_REE_R1_CHALLENGE_BUILD", "AU_REE_R1_CHALLENGE_ANNEX",
        ))
        annexes = [(key, root / f"{key}-expansion/source_manifest.json", root / f"{key}-cas")
                   for key in ("yin", "yangibana")]
        receipt = audit(profile, fixed, assets, build, CHALLENGE, annexes)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["known_scope_negative_count"], 3)
        self.assertEqual((receipt["pdf_decodes"], receipt["pdf_pages_decoded"]), (6, 233))
        self.assertEqual(receipt["generalized_admission_precision_recall"], "UNMEASURED")
        north = receipt["cases"][-1]
        self.assertEqual(north["state"], "EVIDENCE_INSUFFICIENT")
        self.assertTrue(north["decimal_checks"][0]["intersects"])
        with tempfile.TemporaryDirectory() as folder:
            altered = Path(folder)
            (altered / "decision").mkdir()
            inputs = json.loads((build / "decision/mcda_inputs.json").read_bytes())
            record = inputs["criteria_by_project"][north["project_id"]]["R1"]
            record["lineage"] = [{"source_sha256": north["editions"][0]["source_sha256"]}]
            payload = json.dumps(inputs).encode()
            (altered / "decision/mcda_inputs.json").write_bytes(payload)
            report = json.loads((build / "build_receipt.json").read_bytes())
            report["mcda_inputs_hash"] = sha256(payload).hexdigest()
            (altered / "build_receipt.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "held original edition has entered formal R1 lineage"):
                audit(profile, fixed, assets, altered, CHALLENGE, annexes)


_COHORT_INPUTS = (
    "AU_REE_COHORT_REVIEW_MANIFEST", "AU_REE_COHORT_REVIEW_ASSETS", "AU_REE_COHORT_REVIEW_BUILD",
    "AU_REE_COHORT_REVIEW_RECOVERY", "AU_REE_COHORT_REVIEW_SQLITE",
)


@unittest.skipUnless(all(os.environ.get(name) for name in _COHORT_INPUTS), "original fixed cohort bytes required")
class ExactRecoveredResourceStatementTest(unittest.TestCase):
    def test_eleven_sources_original_statement_roles_and_conflicts(self):
        audit = runpy.run_path(str(SCRIPT))["audit_recovered_statements"]
        profiles = PROFILES / "au_ree_census_evidence_fill_v1.json"
        inputs = tuple(Path(os.environ[name]) for name in _COHORT_INPUTS)
        receipt = audit(profiles, *inputs, COHORT)
        self.assertEqual((receipt["reviewed_statement_rows"], receipt["observation_count"]), (12, 70))
        by_id = {row["case_id"]: row for row in receipt["records"]}
        splinter = by_id["splinter-rock-2024-table-one-total"]["checks"]
        self.assertEqual(splinter["CONFLICT"]["reason"], "SAME_DOCUMENT_PROSPECT_CLASSIFICATION_CONFLICT")
        self.assertEqual(splinter["ESTIMATE_DATE"]["status"], "BLOCKED")
        self.assertEqual(set(splinter["CHEMICAL_BASIS"]["members"]), set(TOTAL_REO_15))
        circle = by_id["circle-valley-primary-inferred-resource"]["checks"]
        self.assertEqual(circle["CUTOFF"]["reason"], "CERIUM_EXCLUDED_TREO_CUTOFF_NOT_TREO")
        wolverine = by_id["wolverine-table-five-total-oxide-grades"]
        yttrium, = [item for item in wolverine["observations"] if item["variable"] == "y2o3_ppm"]
        self.assertEqual(yttrium["governed_matches"][0]["outcome"], "SCIENTIFIC_CONFLICT")
        self.assertIs(yttrium["governed_matches"][0]["selected_for_census"], False)

    def test_circle_valley_exact_rebind_keeps_its_inferred_only_category(self):
        case = next(row for row in json.loads(COHORT.read_bytes())["records"]
                    if row["case_id"] == "circle-valley-primary-inferred-resource")
        digest = case["source_sha256"]
        content = ContentStore(Path(os.environ["AU_REE_COHORT_REVIEW_ASSETS"]))
        pdf = DocumentCache().pdf(content.read(digest), digest, 2)
        heading = "Classification Tonnes TREO TREO-CeO2 NdPr Sc2O3"
        witnesses = case["witnesses"]
        binding = {
            "source_sha256": digest, "scope_name": "Circle Valley Rare Earth Mineral Resource",
            "page": 2, "publication_date": "2023-06-14",
            "cutoff": {"value": "430", "unit": "ppm", "analyte": "TREO-CeO2", "reported": "430ppm TREO-CeO2"},
            "table": {
                "caption": witnesses["scope"][1], "header_lines": [heading, "(Mt) (ppm) (ppm) (ppm) (g/t)"],
                "columns": [_reviewed_grade_column(), {
                    "variable": "ore_mt", "header": "Tonnes", "header_line": 1,
                    "unit": "Mt", "unit_label": "Mt", "unit_line": 2,
                }],
                "rows": [{"label": "Total", "category": "INFERRED_ONLY", "primary": True}],
            },
            "context": [
                {"role": "PUBLICATION_DATE", "page": 1, "quote": "14 June 2023"},
                {"role": "JORC", "page": 2, "quote": "Mineral Resources are classified in accordance with JORC code (2012)."},
                {"role": "CHEMICAL_DEFINITION", "page": 2, "quote": witnesses["definition"][1]},
                {"role": "CUTOFF", "page": 2, "quote": witnesses["cutoff"][1]},
            ],
        }
        statement, = native_resource_table(pdf.pages, binding, "2023-06-14")
        # The existing strict geometry still holds the offset 98 Mt lane. The
        # source-only cohort receipt independently records its candidate position.
        self.assertEqual({item.variable: item.token for item in statement.measurements}, {"grade": "890"})
        self.assertEqual(set(statement.treo_members), set(TOTAL_REO_15))
        self.assertEqual(_statement_issue(statement), ("REJECTED", "APPROVED_PROJECT_RESOURCE_CATEGORY_REQUIRED"))
