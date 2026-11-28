"""Check versioned display-only joins without a research write path."""

import csv
import io
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
import unittest

from au_ree_v3.census_display import DisplayFact, display_csv, display_document, load_display_columns, verify_display_document
from au_ree_v3.registry import load_census_columns


PROFILES = Path(__file__).resolve().parents[1] / "config/research_profiles"
PROJECTS = ("AU-" + "1" * 20, "AU-" + "2" * 20)
RELEASE = "d" * 64
FORECAST = "forecast_annual_rate_as_reported"
COMPLETION = "study_effective_or_completion_date"


class CensusDisplayContractTest(unittest.TestCase):
    def setUp(self):
        self.core = load_census_columns(PROFILES / "census_columns_v1.json")
        self.columns = load_display_columns(PROFILES / "census_display_columns_v2.json", core_columns=self.core)
        self.forecast = DisplayFact(
            PROJECTS[0], FORECAST, "8,300 tpa (life-of-mine)", "8,300", "tpa", "a" * 64, "b" * 64,
            "https://announcements.asx.com.au/technical-test.pdf", "2026-10-20", "PDF page 8",
            "Averages 8,300 tpa of contained TREO over the life of mine.", "Project base case", "study-base",
            "FORECAST", {"source_kind": "pdf", "as_reported_at": "2026-10-20", "temporal_status": "ELIGIBLE"},
        )

    def build(self, *facts, **kwargs):
        return display_document(release_id=RELEASE, columns=self.columns, project_ids=PROJECTS, facts=facts, **kwargs)

    def test_keeps_original_project_order_and_fills_only_display_cells(self):
        design = replace(self.forecast, value="9,300 tpa (design)", raw_token="9,300", subject_id="design",
                         quote="9,300 tpa design.", data_role="ENGINEERING_PLAN")
        doc = self.build(self.forecast, design)
        contents = display_csv(doc)
        verify_display_document(doc, contents, release_id=RELEASE, project_ids=PROJECTS, core_columns=self.core)
        self.assertEqual(len(self.core), 51)
        self.assertEqual(doc["coverage"][FORECAST], {"present": 1, "unknown": 1, "not_applicable": 0})
        rows = list(csv.reader(io.StringIO(contents.decode())))
        self.assertEqual([row[0] for row in rows[1:]], list(PROJECTS))
        self.assertEqual(set(doc["facts"]), {self.forecast.fact_id, design.fact_id})
        self.assertIn("8,300 tpa (life-of-mine)", rows[1][-1])

    def test_rejects_bad_role_wrong_scope_or_calendar_and_duplicate_statement(self):
        for fact in (
            replace(self.forecast, data_role="OPERATING_ACTUAL"),
            replace(self.forecast, publication_date="2026-02-30"),
            replace(self.forecast, scope=""),
            replace(self.forecast, raw_unit=""),
            replace(self.forecast, value="=HYPERLINK(...)"),
            replace(self.forecast, context={**self.forecast.context, "temporal_status": "AFTER_CUTOFF"}),
            replace(self.forecast, field_id=COMPLETION, value="2026-11", data_role="FORECAST"),
        ):
            with self.assertRaises(ValueError):
                self.build(fact)
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            self.build(self.forecast, replace(self.forecast, raw_token="9,300"))

    def test_rejects_rehashed_orphaned_swapped_and_forged_cells(self):
        doc = self.build(self.forecast)
        payload = display_csv(doc)
        for key, update in (
            ("release_id", "2" * 64), ("facts", {}), ("projects", doc["projects"][::-1]),
            ("contract_sha256", "1" * 64),
        ):
            with self.assertRaisesRegex(ValueError, "differs"):
                verify_display_document({**doc, key: update}, payload, release_id=RELEASE, project_ids=PROJECTS)
        altered = deepcopy(doc)
        altered["columns"][0]["purpose"] = "MCDA"
        with self.assertRaisesRegex(ValueError, "field definition"):
            verify_display_document(altered, payload, release_id=RELEASE, project_ids=PROJECTS)
        with self.assertRaisesRegex(ValueError, "CSV"):
            verify_display_document(doc, payload + b"extra", release_id=RELEASE, project_ids=PROJECTS)

    def test_review_only_does_not_change_any_grid_cell(self):
        row = {
            "project_id": PROJECTS[0], "data_role": "OFFICIAL_DEPOSIT_POINT", "state": "EVIDENCE_INSUFFICIENT",
            "label": "Official deposit feature", "reason": "Survey date unavailable",
            "source_sha256": "a" * 64, "source_capture_sha256": "b" * 64,
            "source_url": "https://services.ga.gov.au/example", "publication_date": "2026-11",
            "locator": "Feature 7", "quote": "Deposit Feature 7", "scope": "Project",
            "context": {"source_kind": "json"},
        }
        self.assertEqual(self.build(review_only=(row,))["projects"], self.build()["projects"])
        with self.assertRaisesRegex(ValueError, "review-only"):
            self.build(review_only=({**row, "state": "PRESENT"},))
