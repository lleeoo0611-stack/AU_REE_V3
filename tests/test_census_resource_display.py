"""Original issuer documents and counterexamples for Census-only resource display."""

from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from au_ree_v3.census_resource_display import (
    _Phrase, _cutoff, _effective_date, _linked_effective_date, reported_resource_facts,
)
from au_ree_v3.documents import DocumentCache, PDFDocument
from au_ree_v3.resource_extraction import ResourceStatement


ROOT = Path(__file__).resolve().parents[1]
BINDINGS = ROOT / "config/research_profiles/resource_bindings_v4.json"
DATE = "resource_estimate_effective_date"
CUTOFF = "resource_cutoff_as_reported"
CATEGORY = "resource_jorc_class_as_reported"
SCOPE = "resource_scope_or_deposit_name"
CHEMISTRY = "ree_grade_or_chemistry_basis"
WUDINNA = "AU-0098e17a63e7c05c213f"
NORTH = "AU-216d0983f6892f9b58f9"
GOSCHEN = "AU-6d807eb7caec9db0f35e"
WOLVERINE = "AU-9bd8e5ed8408cd84d876"
JUPITER = "AU-dac3c8e40326b8244cdb"
MT_WELD = "AU-efff91a06cf344122d88"


def _linked_date_fixture():
    link = json.loads((ROOT / "tests/fixtures/mt_weld_linked_estimate_date_v1.json").read_bytes())
    statement = ResourceStatement("Mt Weld Mineral Resource", "2.5", "2024-06-30", True, (),
                                  cutoff_unit="%", cutoff_analyte="TREO")
    measured = {
        name: SimpleNamespace(anchor=SimpleNamespace(page=2), raw=SimpleNamespace(raw_value=token, raw_unit=unit))
        for name, token, unit in (("ore_mt", "106.6", "million tonnes"), ("grade", "4.12", "%"))
    }
    return link, statement, measured


def _phrase(text):
    return _Phrase(1, "PDF page 1, line 1", text)


class ResourceDisplayContextTests(unittest.TestCase):
    def test_publication_date_is_not_an_estimate_date(self):
        self.assertIsNone(_effective_date((_phrase("ASX Announcement 19 June 2026"),), "2026-06-19"))
        result = _effective_date((_phrase("The June 2026 Mineral Resource Estimate is reported herein"),), "2026-06")
        self.assertEqual((result.value, result.token), ("2026-06", "June 2026"))

    def test_linked_estimate_day_requires_both_quantities_cutoff_and_edition(self):
        link, statement, measured = _linked_date_fixture()
        pages = (link["cover"]["quote"] + "\n" + link["cutoff_footnote"]["quote"], link["statement"]["quote"])
        pdf = PDFDocument(link["source_sha256"], pages, "native-test")
        fact, lineage = _linked_effective_date(pdf, statement, measured, link)
        self.assertEqual((fact.value, fact.token, fact.phrase.page), ("2024-06-30", "30 June 2024", 1))
        self.assertEqual(lineage["effective_date_role"], "SAME_EDITION_LINKED_ESTIMATE_DATE")
        for subject in (replace(pdf, source_sha256="b" * 64), replace(pdf, pages=(pages[1], pages[0])),
                        replace(pdf, pages=(pages[0].replace("106.6 Mt", "32.0 Mt"), pages[1])),
                        replace(pdf, pages=(pages[0].replace("2.5% TREO", "2.8% TREO"), pages[1])),
                        replace(pdf, pages=(pages[0] + " " + link["cover"]["quote"], pages[1]))):
            self.assertIsNone(_linked_effective_date(subject, statement, measured, link))
        self.assertIsNone(_linked_effective_date(pdf, replace(statement, effective_date="2018-06-30"), measured, link))

    def test_cutoff_analyte_must_not_be_truncated(self):
        estimate = ResourceStatement("Synthetic Project", "250", "2026-06", True, (), cutoff_unit="ppm")
        for token in ("250 ppm TREO+Sc2O3", "250 ppm TREO-CeO2", "250 ppm TREO excluding CeO2", "250 ppm P2O5"):
            self.assertIsNone(_cutoff((_phrase(f"Mineral Resource cutoff {token}."),), estimate, {}))
        adjusted = replace(estimate, cutoff_analyte="TREO-CeO2")
        matched = _cutoff((_phrase("Mineral Resource cutoff 250 ppm TREO-CeO2."),), adjusted, {})
        self.assertEqual((matched.value, matched.token, matched.unit), ("250 ppm TREO-CeO2", "250", "ppm"))


@unittest.skipUnless(all(os.environ.get(key) for key in (
    "AU_REE_RESOURCE_DISPLAY_BUILD", "AU_REE_CENSUS_SOURCE_MANIFEST", "AU_REE_CENSUS_CAS",
)), "original governed research artifacts and exact locked CAS are required")
class ExactResourceDisplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build = Path(os.environ["AU_REE_RESOURCE_DISPLAY_BUILD"])
        assets = Path(os.environ["AU_REE_CENSUS_CAS"])
        manifest = Path(os.environ["AU_REE_CENSUS_SOURCE_MANIFEST"]).read_bytes()
        cls.receipt = json.loads((build / "census/census_receipt.json").read_bytes())
        cls.observations = [json.loads(line) for line in (build / "observations/observations.jsonl").read_text().splitlines()]
        cls.manifest = json.loads(manifest)
        cls.kwargs = {"assets": assets, "bindings": json.loads(BINDINGS.read_bytes()), "manifest_sha": sha256(manifest).hexdigest()}
        cls.diagnostics = []
        cls.facts = reported_resource_facts(cls.receipt, cls.observations, cls.manifest, **cls.kwargs, diagnostics=cls.diagnostics)
        cls.cells = {(fact.project_id, fact.field_id): fact for fact in cls.facts}

    def test_independent_issuer_readbacks_preserve_scope_role_and_printed_precision(self):
        # Expected values were transcribed from issuer PDFs independently of the adapter.
        expected = {
            (WUDINNA, DATE): ("2026-10-22", "22 October, 2026", "", 3),
            (WUDINNA, CUTOFF): ("200 ppm TREO-Ce", "200", "ppm", 3),
            (WUDINNA, CATEGORY): ("Indicated and Inferred", "Indicated and Inferred", "", 3),
            (NORTH, DATE): ("2026-08", "August 2026", "", 97),
            (NORTH, CUTOFF): ("300 ppm TREO", "300", "ppm", 97),
            (GOSCHEN, CUTOFF): ("1% THM", "1", "%", 12),
            (GOSCHEN, CHEMISTRY): ("In-Situ TREO + Y2O3", "In-Situ TREO + Y2O3", "", 12),
            (WOLVERINE, CUTOFF): ("0.15 % TREO", "0.15", "%", 5),
            (JUPITER, DATE): ("2026-09", "September 2026", "", 19),
            (JUPITER, CUTOFF): ("1,000 ppm TREO", "1,000", "ppm", 4),
            (MT_WELD, CUTOFF): ("2.5% TREO", "2.5", "%", 2),
        }
        self.assertEqual(len(self.facts), 28)
        self.assertTrue(all(len(fact.context) <= 40 and fact.context["source_kind"] == "pdf" for fact in self.facts))
        for identity, values in expected.items():
            with self.subTest(identity=identity):
                fact = self.cells[identity]
                self.assertEqual((fact.value, fact.raw_token, fact.raw_unit, int(fact.context["physical_page"])), values)
                self.assertIn(fact.raw_token, fact.quote)
                edition, = [row for row in self.manifest["editions"] if row["receipt_sha256"] == fact.source_capture_sha256]
                self.assertEqual(edition["sha256"], fact.source_sha256)
                self.assertIn(fact.project_id, edition["project_ids"])
        jupiter = self.cells[JUPITER, SCOPE]
        self.assertIn("September 2026", jupiter.value)
        self.assertEqual(jupiter.context["native_parent_header"], "MRE - 2026")
        self.assertEqual((jupiter.context["reported_ore_mt_token"], jupiter.context["reported_ore_mt_unit"]), ("1.7", "Bt"))
        north = self.cells[NORTH, SCOPE]
        self.assertEqual((north.context["reported_ore_mt_token"], north.context["reported_grade_token"]), ("278.80", "482.64"))
        self.assertNotIn("reported_contained_treo_kt_token", north.context)
        weld = self.cells[MT_WELD, CHEMISTRY]
        self.assertEqual((weld.context["reported_grade_token"], weld.context["reported_contained_treo_kt_token"]), ("4.12", "4,389"))

    def test_scope_and_direct_category_negatives_remain_visible_as_holds(self):
        self.assertNotIn((GOSCHEN, CATEGORY), self.cells)
        self.assertNotIn((MT_WELD, DATE), self.cells)
        self.assertEqual(len({fact.subject_id for fact in self.facts if fact.project_id == WOLVERINE}), 1)
        conflict, = [row for row in self.diagnostics if row["status"] == "SCIENTIFIC_CONFLICT"]
        self.assertEqual(conflict["reason"], "SOURCE_ROW_AND_GOVERNED_CATEGORY_DIFFER")
        self.assertIn("M+Ind+Inf", conflict["review_evidence"]["quote"])
        self.assertEqual(self.cells[GOSCHEN, CUTOFF].context["jorc_category"], "UNKNOWN_CONTEXT")
        self.assertEqual(self.cells[MT_WELD, CUTOFF].context["effective_date"], "UNKNOWN_CONTEXT")

    def test_mt_weld_original_cover_day_joins_the_exact_table_and_cutoff(self):
        link, statement, measured = _linked_date_fixture()
        source = self.kwargs["assets"] / "objects" / link["source_sha256"]
        original = source.read_bytes()
        self.assertEqual(sha256(original).hexdigest(), link["source_sha256"])
        pdf = DocumentCache().pdf(original, link["source_sha256"], 2)
        fact, context = _linked_effective_date(pdf, statement, measured, link)
        self.assertEqual((fact.value, fact.token, fact.phrase.page), ("2024-06-30", "30 June 2024", 1))
        self.assertIn("PDF page 2", context["date_link_statement_locator"])
        self.assertIn("PDF page 1", context["date_link_cutoff_locator"])

    def test_altered_lineage_cannot_claim_display(self):
        changed = deepcopy(self.receipt)
        claim = changed["science_views"]["verified_resource_presence"][GOSCHEN]["inputs"][0]
        claim["selection_id"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "exact selected observations"):
            reported_resource_facts(changed, self.observations, self.manifest, **self.kwargs)

    def test_missing_exact_pdf_only_withdraws_one_statement(self):
        excluded = self.cells[WUDINNA, CUTOFF].source_sha256
        required = {item.source_sha256 for item in self.facts} | {item.source_capture_sha256 for item in self.facts}
        required.update(proof["reference_sha256"] for entry in self.manifest["editions"] if entry["sha256"] in required
                        for proof in entry["temporal_evidence"])
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            (store / "objects").mkdir()
            for digest in required - {excluded}:
                shutil.copyfile(self.kwargs["assets"] / "objects" / digest, store / "objects" / digest)
            reasons = []
            remaining = reported_resource_facts(self.receipt, self.observations, self.manifest,
                                                **{**self.kwargs, "assets": store}, diagnostics=reasons)
        self.assertEqual(remaining, tuple(fact for fact in self.facts if fact.project_id != WUDINNA))
        missing, = [row for row in reasons if row["project_id"] == WUDINNA]
        self.assertEqual((missing["status"], missing["reason"]), ("SOURCE_UNAVAILABLE", "SOURCE_OBJECT_MISSING"))
