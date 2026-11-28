"""Candidate-only discovery across independently described native layouts."""

import unittest

from au_ree_v3.documents import PDFDocument
from au_ree_v3.native_table_discovery import discover_page_rows
from au_ree_v3.resource_table_binding import bind_page_row


HEADER = "Classification         Tonnage (Mt)        TREO (ppm)        Contained TREO (t)"
BODY = (
    "Measured               30                   1,200                36,000",
    "Global Total*          45                   1,100                49,500",
)


def document(*pages):
    return PDFDocument("a" * 64, tuple("\n".join(page) for page in pages), "synthetic")


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
