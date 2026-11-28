"""Verify dated annex evidence and non-destructive source expansion."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import unittest

from au_ree_v3.documents import DocumentCache
from au_ree_v3.resource_extraction import native_resource_table
from au_ree_v3.sources import SCHEMA, require_source_expansion


class ExpandedSourceScopeTests(unittest.TestCase):
    def test_only_a_superset_of_identical_captures_can_repin_bindings(self):
        fixed = {"schema": SCHEMA, "profile_id": "fixed", "cutoff": "2026-11-28", "timezone": "Asia/Taipei",
                 "projects": [{"project_id": "P"}], "editions": [{"receipt_sha256": "one", "sha256": "PDF-1"}]}
        expanded = deepcopy(fixed)
        expanded["input_manifests"] = [{"sha256": "verified-fixed-manifest"}]
        expanded["editions"].append({"receipt_sha256": "two", "sha256": "PDF-2"})
        self.assertEqual(require_source_expansion(fixed, expanded, "verified-fixed-manifest"), (1, 2))
        changed = deepcopy(expanded)
        changed["editions"][0]["sha256"] = "SUBSTITUTED"
        with self.assertRaisesRegex(ValueError, "changes a fixed source edition"):
            require_source_expansion(fixed, changed, "verified-fixed-manifest")
        changed = deepcopy(expanded)
        changed["projects"].append({"project_id": "Q"})
        with self.assertRaisesRegex(ValueError, "changes the project cohort"):
            require_source_expansion(fixed, changed, "verified-fixed-manifest")
        with self.assertRaisesRegex(ValueError, "does not identify the fixed input"):
            require_source_expansion(fixed, expanded, "another-manifest")


@unittest.skipUnless(os.environ.get("AU_REE_SCOPED_RESOURCE_ASSETS") and shutil.which("pdftotext"),
                     "exact public original and Poppler are required")
class DatedResourceAnnexTests(unittest.TestCase):
    def test_exact_figure_admits_census_fields_without_borrowing_reported_inventory(self):
        root = Path(__file__).resolve().parents[1]
        profile = json.loads((root / "config/research_profiles/resource_bindings_v6.json").read_bytes())
        binding = next(row for row in profile["bindings"] if row["case_id"] == "nolans-bore-2017-reported-resource")
        expected_sha = "5f72a08840640987e8d619c6d55ca047a2fe26c0194ffd1a46521f76eda7b06f"
        asset = Path(os.environ["AU_REE_SCOPED_RESOURCE_ASSETS"]) / "objects" / expected_sha
        content = asset.read_bytes()
        self.assertEqual(sha256(content).hexdigest(), expected_sha)
        pages = DocumentCache().pdf(content, expected_sha, 31).pages
        statement, = native_resource_table(pages, binding, "2017-06-07")
        self.assertEqual((statement.category, statement.reporting_standard, statement.cutoff, statement.effective_date),
                         ("MEASURED_INDICATED_INFERRED_TOTAL", "JORC 2012", "1", "2017-06-07"))
        self.assertEqual(set(statement.treo_members), {"La2O3", "CeO2", "Pr6O11", "Nd2O3", "Sm2O3", "Eu2O3",
                                                        "Gd2O3", "Tb4O7", "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3",
                                                        "Yb2O3", "Lu2O3", "Y2O3"})
        self.assertEqual([(m.variable, m.token, m.raw_unit, m.page, m.material_basis, m.evidence_issue)
                          for m in statement.measurements],
                         [("ore_mt", "56", "Mt", 2, "WHOLE_ORE", None), ("grade", "2.6", "%", 2, "WHOLE_ORE", None)])
        for page_number, before, after in (
            (2, "and attached JORC Table 1", "and attached JORC Table 2"),
            (4, "AS AT 7 JUNE 2017", "AS AT 6 JUNE 2017"),
            (7, "JORC TABLE 1 ASSESSMENT CRITERIA", "RESERVE REVIEW"),
        ):
            with self.subTest(page_number=page_number):
                inexact = list(pages)
                self.assertIn(before, inexact[page_number - 1])
                inexact[page_number - 1] = inexact[page_number - 1].replace(before, after)
                blocked, = native_resource_table(tuple(inexact), binding, "2017-06-07")
                self.assertTrue(all(cell.evidence_issue == "WHOLE_ORE_MATERIAL_BASIS_UNBOUND"
                                    for cell in blocked.measurements))
