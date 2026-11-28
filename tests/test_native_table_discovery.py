"""Candidate-only discovery across independently described native layouts."""

import os
from pathlib import Path
import unittest

from au_ree_v3.documents import DocumentError, PDFDocument
from au_ree_v3.native_table_discovery import discover_page_rows
from au_ree_v3.native_table_work import (
    _cohort_formula_checks, _cohort_quote_anchors, _legacy_cell, _legacy_page_cells, _recovered_cell,
    replay_legacy_source_lanes,
)
from au_ree_v3.resource_table_binding import bind_page_row


HEADER = "Classification         Tonnage (Mt)        TREO (ppm)        Contained TREO (t)"
BODY = (
    "Measured               30                   1,200                36,000",
    "Global Total*          45                   1,100                49,500",
)


def document(*pages):
    return PDFDocument("a" * 64, tuple("\n".join(page) for page in pages), "synthetic")


class LegacySourceLaneTests(unittest.TestCase):
    def test_repeated_marker_requires_a_unique_original_block(self):
        text = ("Example issue heading", "", "Example issue published its Scoping Study", "as approved.")
        pdf = document(text)
        anchor, = _cohort_quote_anchors(pdf, "source", 1, ("Example issue", "Scoping Study"))
        self.assertEqual(anchor.quote, "Example issue published its Scoping Study as approved.")
        self.assertEqual(anchor.role, "ARCHIVED_TEXT_COLOCATED_QUOTE")
        with self.assertRaises(DocumentError):
            _cohort_quote_anchors(document((*text, "", *text)), "source", 1, ("Example issue", "Scoping Study"))

    def test_formula_check_retains_its_same_native_row_and_shadow_role(self):
        pdf = document(("Table 1 Synthetic Mineral Resource Estimate", HEADER, *BODY))
        edition = {"url": "https://publisher.invalid/resource.pdf", "edition_id": "historical",
                   "receipt_sha256": None, "temporal_decision": {"decision_id": "pending"}}
        cells = {}
        for located in _legacy_page_cells(pdf, [1]).values():
            for native_row, cell in located.values():
                result = _recovered_cell("AU-A", pdf, native_row, cell, edition)
                cells[result["candidate_id"]] = result
        formula, = _cohort_formula_checks(cells)
        self.assertEqual((formula["value"], formula["unit"], formula["layer"]), ("49.500", "kt", "SHADOW"))
        self.assertEqual(formula["formula_id"], "contained-treo-from-mt-ppm-v1")
        self.assertEqual(len(formula["input_observation_ids"]), 2)
        for raw in cells.values():
            if raw["variable"] == "ore_mt":
                raw["row_anchor"]["locator"] = "PDF page 1, line 99"
        self.assertEqual(_cohort_formula_checks(cells), [])

    def test_nominated_token_is_bound_to_a_native_lane_without_legacy_value(self):
        pdf = document(("Table 1 Synthetic Mineral Resource Estimate", HEADER, *BODY))
        index = _legacy_page_cells(pdf, [1])
        hint = {"observation_id": "legacy-hint", "source_sha256": pdf.source_sha256, "project_id": "AU-A",
                "physical_page": "1", "variable": "grade", "basis": "TREO", "original_token": "1,100",
                "original_unit": "ppm", "source_transform": "", "value": "irrelevant legacy value"}
        edition = {"url": "https://publisher.invalid/resource.pdf", "edition_id": "original", "acquired_at": "day",
                   "receipt_sha256": "receipt", "temporal_decision": {"decision_id": "time"}}
        row, hold = _legacy_cell(hint, pdf, index, [edition])
        self.assertIsNone(hold)
        self.assertEqual((row["variable"], row["native_unit"], row["value_anchor"]["text"]), ("grade", "ppm", "1,100"))
        self.assertIn("Total", row["row_anchor"]["text"])
        self.assertEqual(row["scope_status"], "UNBOUND")
        self.assertFalse(row["formal_admission"])
        self.assertNotIn(hint["value"], str(row))
        for changes, expected in (({"original_token": "1,200.0"}, "TYPED_LANE_NOT_LOCATED"),
                                  ({"original_unit": "%"}, "TYPED_LANE_NOT_LOCATED"),
                                  ({"source_transform": "{}"}, "HISTORICAL_TRANSFORM_REQUIRES_SEPARATE_REVIEW")):
            with self.subTest(changes):
                row, hold = _legacy_cell({**hint, **changes}, pdf, index, [edition])
                self.assertIsNone(row)
                self.assertEqual(hold["reason"], expected)


_LANE_VARS = ("AU_REE_LEGACY_V2_ROOT", "AU_REE_LEGACY_LANE_MANIFEST", "AU_REE_LEGACY_LANE_CAS")


@unittest.skipUnless(all(os.environ.get(key) for key in _LANE_VARS), "Pinned legacy statement corpus unavailable")
class ExactLegacySourceLaneTests(unittest.TestCase):
    def test_exact_source_batch_exposes_candidate_evidence_and_hold_denominator(self):
        inputs = dict(zip(("v2_root", "sources", "assets"), (Path(os.environ[key]) for key in _LANE_VARS), strict=True))
        receipt = replay_legacy_source_lanes(**inputs)
        self.assertEqual(receipt["status"], "PASS")
        counts = receipt["counts"]
        self.assertEqual((counts["legacy_hints"], counts["exact_manifest_project_pdf_join"], counts["pdfs_read"]),
                         (519, 448, 44))
        self.assertEqual((counts["matching_legacy_hints"], counts["candidate_positions"], counts["candidate_projects"]),
                         (55, 55, 11))
        self.assertEqual(counts["matching_legacy_hints"] + len(receipt["holds"]), counts["legacy_hints"])
        observations = receipt["observations"]
        self.assertEqual(len({r["candidate_id"] for r in observations}), 55)
        self.assertTrue(all(r["header_anchor"] and r["unit_anchor"] and r["scope_status"] == "UNBOUND"
                            and r["temporal_decision_id"] and not r["formal_admission"] for r in observations))
        match, = (r for r in observations if r["source_sha256"].startswith("5f8f642b") and r["variable"] == "ore_mt")
        self.assertEqual(match["value_anchor"]["text"], "94,200,000")
        self.assertIn("M+Ind+Inf", match["row_anchor"]["text"])
        self.assertEqual(match["physical_page"], 12)


class NativeTableDiscoveryTests(unittest.TestCase):
    def test_scale_tier_is_read_with_its_literal_mass_header(self):
        label = f"{'Resource Classification':26}{'Tonnes':23}{'TREO':23}Contained TREO"
        units = f"{'':26}{'(Millions)':23}{'(ppm)':23}('000 tonnes)"
        body = f"{'Total':26}{'2.3':23}{'905':23}2.08"
        pdf = document(("Table 8: Synthetic Mineral Resource Estimate", label, units, body))
        row, = discover_page_rows(pdf, page=1)
        self.assertEqual([(cell.variable, cell.value.text, cell.unit) for cell in row.cells], [
            ("ore_mt", "2.3", "Mt"), ("grade", "905", "ppm"), ("contained_treo_kt", "2.08", "kt"),
        ])
        self.assertEqual(row.cells[0].unit_label.text, "(Millions)")

    def test_shortened_caption_requires_resource_classification_and_rejects_reserves(self):
        title = "Table 1: Summary of Synthetic Resources at 400 ppm TREO cut-off."
        head = f"{'Resource Classification':26}{'Tonnes (Mt)':23}{'TREO (ppm)':23}Contained TREO (t)"
        body = f"{'Total':26}{'12':23}{'450':23}5,400"
        pdf = document((title, head, body))
        row, = discover_page_rows(pdf, page=1)
        self.assertEqual(row.strategy, "PRINTED_CLASSIFIED_RESOURCE_CUTOFF")
        self.assertIn("400 ppm", row.context_anchors[0].text)
        negative = document((title.replace("Resources", "Ore Reserves"), head, body),
                            (title, HEADER, *BODY))
        self.assertEqual(discover_page_rows(negative, page=1), ())
        self.assertEqual(discover_page_rows(negative, page=2), ())

    def test_explicit_single_class_below_a_printed_section_heading(self):
        single_class = BODY[0].replace("Measured", "Inferred")
        pdf = document(("3.2 Synthetic Mineral Resource Estimate (JORC 2012)", HEADER, single_class))
        row, = discover_page_rows(pdf, page=1)
        self.assertEqual(row.row_line, 3)
        self.assertEqual({cell.variable for cell in row.cells}, {"ore_mt", "grade", "contained_treo_kt"})
        reserve = document(("3.2 Synthetic Ore Reserve Estimate (JORC 2012)", HEADER, single_class))
        self.assertEqual(discover_page_rows(reserve, page=1), ())

    def test_earlier_grade_witness_requires_same_named_scope_and_analyte(self):
        title = "Table 1 Example REE Project Mineral Resource Estimate"
        header = HEADER.replace("TREO (ppm)", "TREO      ")
        witness = 'Example Rare Earth Project’s maiden Mineral Resource Estimate ("MRE") of 45 Mt grading 1,100 ppm TREO.'
        for printed, found in ((witness, True), (witness.replace("Example", "Other"), False),
                               (witness.replace("TREO.", "TREO-Ce."), False)):
            pdf = document((printed,), (), (title, header, *BODY))
            cells = bind_page_row(pdf, page=3, row_line=4, caption_line=1)
            grade = next((row for row in cells if row.variable == "grade"), None)
            self.assertEqual(grade is not None, found)
            if grade:
                self.assertEqual((grade.value.text, grade.unit, grade.unit_label.physical_page), ("1,100", "ppm", 1))

    def test_unnumbered_resource_heading_anchors_exact_total_positions(self):
        pdf = document(("Mineral Resource Estimate", HEADER, *BODY))
        row, = discover_page_rows(pdf, page=1)
        self.assertEqual((row.physical_page, row.caption_line, row.row_line), (1, 1, 4))
        self.assertEqual(row.strategy, "UNNUMBERED_RESOURCE_HEADING")
        self.assertEqual([(entry.variable, entry.unit, entry.value.text) for entry in row.cells], [
            ("ore_mt", "Mt", "45"), ("grade", "ppm", "1,100"), ("contained_treo_kt", "t", "49,500"),
        ])

    def test_post_grid_caption_retains_order_and_original_line(self):
        pdf = document((HEADER, *BODY, "Table 2: Synthetic Mineral Resource Estimate"))
        row, = discover_page_rows(pdf, page=1)
        self.assertEqual((row.caption_line, row.row_line, row.strategy), (4, 3, "RESOURCE_CAPTION_BELOW_GRID"))
        self.assertTrue(all(entry.caption[0].line == 4 and entry.value.line == 3 for entry in row.cells))

    def test_conflicting_table_claims_and_reserve_are_not_guessed(self):
        pdf = document(("Table 1: Synthetic Mineral Resource Estimate", HEADER, *BODY,
                        "Table 2: Synthetic Mineral Resource Estimate"),
                       ("Ore Reserve Estimate", HEADER, *BODY),
                       ("Mineral Resource Estimate", "Ore Reserve Estimate", HEADER, *BODY))
        self.assertEqual(discover_page_rows(pdf, page=1), ())
        self.assertEqual(discover_page_rows(pdf, page=2), ())
        self.assertEqual(discover_page_rows(pdf, page=3), ())

    def test_exact_adjacent_continuation_recovers_original_header(self):
        pdf = document(("Table 1: Synthetic Mineral Resource Estimate", HEADER, BODY[0]),
                       ("Table 1 Continued", BODY[1]))
        row, = discover_page_rows(pdf, page=2)
        self.assertEqual(row.strategy, "NUMBERED_TABLE_CONTINUED")
        self.assertTrue(all(entry.header.physical_page == 1 and entry.value.physical_page == 2 for entry in row.cells))

    def test_anchored_adjacent_unit_footnote_does_not_mix_tables(self):
        title = "Table 3: Synthetic Mineral Resource Estimate"
        header = HEADER.replace("TREO (ppm)", "TREO*     ")
        reference = (title, header, *BODY)
        note = "* TREO grade is reported in ppm."
        pdf = document(reference, ("Table 3 — Notes", note))
        row, = discover_page_rows(pdf, page=1)
        field = next(entry for entry in row.cells if entry.variable == "grade")
        self.assertEqual((field.unit, field.unit_label.physical_page, field.unit_label.text), ("ppm", 2, note))
        for second_page in (("Table 4 — Notes", note), ("Table 3 — Notes", note, "* TREO grade is reported in %.")):
            pdf = document(reference, second_page)
            self.assertNotIn("grade", {entry.variable for entry in bind_page_row(pdf, page=1, row_line=4, caption_line=1)})

    def test_classification_with_single_printed_material_mass_lane(self):
        header = HEADER.replace("Tonnage (Mt)", "Material (Mt)")
        pdf = document(("Table 3: Synthetic Mineral Resource Estimate", header,
                        BODY[1].rsplit("  ", maxsplit=1)[0]))
        # The explicit containment header above is a second mass lane, so a
        # generic Material label cannot establish which mass the source means.
        result = bind_page_row(pdf, page=1, row_line=3, caption_line=1)
        self.assertNotIn("ore_mt", {entry.variable for entry in result})
        shallower = header.replace("Contained TREO (t)", "Density")
        pdf = document(("Table 3: Synthetic Mineral Resource Estimate", shallower, BODY[1]))
        result = bind_page_row(pdf, page=1, row_line=3, caption_line=1)
        self.assertIn(("ore_mt", "45", "Mt"), [(entry.variable, entry.value.text, entry.unit) for entry in result])

    def test_located_cutoff_and_modified_analyte_keep_explicit_evidence_holds(self):
        title = "Table 5: Synthetic Mineral Resource Estimate"
        header = f"{'Category':20}{'Tonnes (Mt)':20}{'TREO-CeO2 (ppm)':20}Cut-off (ppm)"
        total = f"{'Total':20}{'12':20}{'450':20}150"
        result = bind_page_row(document((title, header, total)), page=1, row_line=3, caption_line=1)
        self.assertEqual([(cell.value.text, cell.variable, cell.reason) for cell in result], [
            ("12", "ore_mt", "ORIGINAL_HEADER_UNIT_AND_ROW_BOUND"),
            ("450", None, "CHEMICALLY_MODIFIED_TREO_IS_NOT_STANDARD_TREO"),
            ("150", None, "CUTOFF_THRESHOLD_IS_NOT_A_RESOURCE_GRADE"),
        ])
        self.assertEqual([entry.header_tiers[0].text for entry in result[1:]], ["TREO-CeO2 (ppm)", "Cut-off (ppm)"])
