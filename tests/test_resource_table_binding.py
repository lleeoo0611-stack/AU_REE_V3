"""Independent native layout, scope, and Decimal conversion checks."""

from copy import deepcopy
import unittest

from au_ree_v3.documents import DocumentError, PDFDocument
from au_ree_v3.resource_extraction import native_resource_table
from au_ree_v3.resource_table_binding import _baseline_overlap, bind_page_row, reviewed_table_rows

SHA = "a" * 64
CAPTION = "Table 1 Example Hill Mineral Resource Estimate as at 21 September 2026"
HEADER = "Classification        Contained TREO (t)        TREO (ppm)        Tonnage (kt)"
PAGE = "\n".join((
    "ASX Announcement 8 October 2026",
    CAPTION,
    HEADER,
    "Measured                       190                  1,000                 190",
    "Indicated                      465                  1,000                 465",
    "Inferred                       570                  1,000                 570",
    "Total                        1,225                  1,000               1,225",
    "The Mineral Resource is classified in accordance with the JORC Code 2012.",
    "Mineral Resource tonnages are estimated and reported on a dry basis.",
    "The table was reported at 500 ppm TREO cut-off grade.",
    "TREO = Total Rare Earth Oxides (La2O3, CeO2, Pr6O11, Nd2O3, Sm2O3, Eu2O3, Gd2O3, Tb4O7, "
    "Dy2O3, Ho2O3, Er2O3, Tm2O3, Yb2O3, Lu2O3, Y2O3).",
))
CONTEXT = [
    {"role": role, "page": 1, "quote": text}
    for role, text in (
        ("PUBLICATION_DATE", "ASX Announcement 8 October 2026"),
        ("EFFECTIVE_DATE", CAPTION),
        ("JORC", "The Mineral Resource is classified in accordance with the JORC Code 2012."),
        ("MATERIAL_BASIS", "Mineral Resource tonnages are estimated and reported on a dry basis."),
        ("CUTOFF", "The table was reported at 500 ppm TREO cut-off grade."),
        ("CHEMICAL_DEFINITION", PAGE.splitlines()[10]),
    )
]
BINDING = {
    "source_sha256": SHA, "publication_date": "2026-10-08", "effective_date": "2026-09-21",
    "scope_name": "Example Hill Mineral Resource", "page": 1,
    "cutoff": {"value": "500", "unit": "ppm", "analyte": "TREO", "reported": "500 ppm TREO"},
    "table": {
        "caption": CAPTION, "header_lines": [HEADER],
        "columns": [
            dict(variable="ore_mt", header="Tonnage", header_line=1, unit="kt", unit_label="kt", unit_line=1),
            dict(variable="grade", header="TREO (ppm)", header_line=1, unit="ppm", unit_label="ppm", unit_line=1),
            dict(variable="contained_treo_kt", header="Contained TREO", header_line=1, unit="t", unit_label="t", unit_line=1),
        ],
        "rows": [dict(label="Total", category="MEASURED_INDICATED_INFERRED_TOTAL", primary=True)],
    },
    "context": CONTEXT,
}


class NativeLayoutTests(unittest.TestCase):
    def test_reordered_native_fields_bind_exact_quantities_and_precision(self):
        statement, = native_resource_table((PAGE,), BINDING, BINDING["publication_date"])
        self.assertTrue(statement.primary)
        self.assertEqual((statement.category, statement.reporting_standard, statement.resource_class),
                         ("MEASURED_INDICATED_INFERRED_TOTAL", "JORC 2012", "MINERAL_RESOURCE"))
        self.assertEqual(len(statement.treo_members), 15)
        got = {cell.variable: (cell.token, cell.raw_unit, cell.value) for cell in statement.measurements}
        self.assertEqual(got, {
            "contained_treo_kt": ("1,225", "t", "1.225"),
            "ore_mt": ("1,225", "kt", "1.225"),
            "grade": ("1,000", "ppm", "1000"),
        })
        original = PDFDocument(SHA, (PAGE,), "test")
        for cell in statement.measurements:
            self.assertIsNone(cell.evidence_issue)
            proof = cell.evidence_context["native_table_binding"]
            for key in ("value_anchor", "unit_anchor", "header_anchor"):
                anchor = proof[key]
                actual = original.locate(1, line=anchor["line"], column=anchor["start_column"],
                                         end_column=anchor["end_column"]).text
                self.assertEqual(actual, anchor["text"])
            self.assertEqual(proof["row_anchor"], "PDF page 1, line 7")

    def test_geometry_checks_units_and_material_before_number_assignment(self):
        dangerous = PAGE.replace("Tonnage (kt)", "HM tonnes (kt)")
        result = bind_page_row(PDFDocument(SHA, (dangerous,), "test"), page=1, row_line=7, caption_line=2)
        self.assertNotIn("ore_mt", {cell.variable for cell in result})
        self.assertTrue(any(cell.variable is None for cell in result))
        concentrated = PAGE.replace("TREO (ppm)", "Concentrate TREO (ppm)")
        result = bind_page_row(PDFDocument(SHA, (concentrated,), "test"), page=1, row_line=7, caption_line=2)
        self.assertNotIn("grade", {cell.variable for cell in result})

    def test_two_year_groups_do_not_bind_prior_or_cutoff_columns(self):
        caption = "Table 2 Example Hill Mineral Resource 2025 and 2026"
        first = "                 MRE - 2025              MRE - 2026"
        second = "Class          Tonnes       TREO (ppm)     Tonnes       TREO (ppm)"
        row = "Total          1.8 Bt          1,900       1.7 Bt          1,700"
        pdf = PDFDocument(SHA, ("\n".join((caption, first, second, row)),), "test")
        spec = {"page": 1, "table": {"caption": caption, "header_lines": [first, second], "columns": [
            dict(variable="ore_mt", header="Tonnes", header_line=2, unit="Bt",
                 unit_inline=True, group_header="MRE - 2026", group_line=1),
            dict(variable="grade", header="TREO (ppm)", header_line=2, unit="ppm", unit_line=2,
                 unit_label="ppm", group_header="MRE - 2026", group_line=1),
        ], "rows": [dict(label="Total")]}}
        _, _, _, mapped, _, _, _ = next(reviewed_table_rows(pdf, spec))
        self.assertEqual([(cell.variable, cell.value.text, unit.text) for cell, _, unit in mapped], [
            ("ore_mt", "1.7", "Bt"), ("grade", "1,700", "ppm"),
        ])
        spec["table"]["columns"][0].pop("group_header")
        with self.assertRaisesRegex(DocumentError, "NOT_UNIQUE"):
            tuple(reviewed_table_rows(pdf, spec))

    def test_earlier_native_columns_hold_against_newer_effective_vintage(self):
        caption = "Table 2 Example Hill Mineral Resource 2025 and 2026"
        first = "                      MRE - 2025            MRE - 2026"
        second = "Class               Tonnes    TREO (ppm)     Tonnes    TREO (ppm)"
        total = "Total               1.8 Bt       1,900       1.7 Bt       1,700"
        publication, date = "ASX Announcement 8 October 2026", "The Mineral Resource is effective 21 September 2026."
        prior = deepcopy(BINDING)
        prior["table"] = {"caption": caption, "header_lines": [first, second], "columns": [
            dict(variable="ore_mt", header="Tonnes", header_line=2, unit="Bt",
                 unit_inline=True, group_header="MRE - 2025", group_line=1),
            dict(variable="grade", header="TREO (ppm)", header_line=2, unit="ppm", unit_line=2,
                 unit_label="ppm", group_header="MRE - 2025", group_line=1),
        ], "rows": [dict(label="Total", category="INDICATED_INFERRED_TOTAL", primary=True)]}
        earlier = "Indicated           0.3 Bt       1,800       0.2 Bt       1,600"
        inferred = "Inferred            1.5 Bt       1,900       1.5 Bt       1,700"
        prior["context"] = [
            {"role": "PUBLICATION_DATE", "page": 1, "quote": publication},
            {"role": "EFFECTIVE_DATE", "page": 1, "quote": date},
            *[item for item in CONTEXT if item["role"] not in {"PUBLICATION_DATE", "EFFECTIVE_DATE"}],
        ]
        body = "\n".join((publication, caption, first, second, earlier, inferred, total, date, *PAGE.splitlines()[7:]))
        actual, = native_resource_table((body,), prior, prior["publication_date"])
        self.assertEqual(actual.effective_date, "2026-09-21")
        self.assertEqual({cell.conflict for cell in actual.measurements}, {"NATIVE_PARENT_HEADER_ESTIMATE_VINTAGE_CONFLICT"})
        prior["table"]["columns"][0]["group_header"] = "MRE - 2026"
        actual, = native_resource_table((body,), prior, prior["publication_date"])
        self.assertEqual({cell.conflict for cell in actual.measurements}, {"INCOMPATIBLE_NATIVE_MEASUREMENT_PARENT_HEADERS"})

    def test_exact_caption_scopes_repeated_header_and_reviewed_end_bounds_rows(self):
        prior = "Table 0 Example Hill Ore Reserve"
        stop = "Rows before this statement use the headline cutoff."
        body = "\n".join((prior, HEADER, PAGE, stop, PAGE.splitlines()[6]))
        fixture = deepcopy(BINDING)
        fixture["table"]["end_quote"] = stop
        statement, = native_resource_table((body,), fixture, fixture["publication_date"])
        self.assertTrue(statement.primary)
        self.assertEqual(len(statement.measurements), 3)
        self.assertTrue(all(cell.evidence_context["native_table_binding"]["header_anchor"]["line"] == 5
                            for cell in statement.measurements))
        fixture["table"].pop("end_quote")
        with self.assertRaisesRegex(DocumentError, "ROW_NOT_UNIQUE"):
            native_resource_table((body,), fixture, fixture["publication_date"])

    def test_adjacent_page_requires_exact_same_table_continuation(self):
        spec = deepcopy(BINDING)
        spec["page"] = 2
        spec["table"].update(header_page=1, continuation_quote="Table 1 Continued")
        body = ("\n".join((CAPTION, HEADER)), "Table 1 Continued\n" + PAGE.splitlines()[6])
        pdf = PDFDocument(SHA, body, "test")
        _, _, _, got, _, _, _ = next(reviewed_table_rows(pdf, spec))
        self.assertEqual([cell.value.physical_page for cell, _, _ in got], [2, 2, 2])
        self.assertEqual([header.physical_page for _, header, _ in got], [1, 1, 1])
        spec["table"]["continuation_quote"] = "Table 7 Continued"
        bad = PDFDocument(SHA, (body[0], body[1].replace("Table 1", "Table 7")), "test")
        with self.assertRaisesRegex(DocumentError, "CONTINUATION_MISMATCH"):
            tuple(reviewed_table_rows(bad, spec))

    def test_unbound_context_and_disagreeing_mass_remain_evidence_holds(self):
        no_chem = deepcopy(BINDING)
        no_chem["context"] = [item for item in CONTEXT if item["role"] != "CHEMICAL_DEFINITION"]
        value, = native_resource_table((PAGE,), no_chem, no_chem["publication_date"])
        self.assertFalse(value.treo_members)
        self.assertTrue(all(cell.evidence_issue == "TREO_CHEMICAL_DEFINITION_UNBOUND" for cell in value.measurements))
        wrong = PAGE.replace("Total                        1,225", "Total                        4,225")
        value, = native_resource_table((wrong,), BINDING, BINDING["publication_date"])
        self.assertTrue(all(cell.conflict == "TONNAGE_GRADE_CONTAINED_MISMATCH" for cell in value.measurements))

    def test_exact_baseline_measurement_is_distinct_from_row_lexeme_hint(self):
        # A single occurrence per row distinguishes the raw source position.
        row = "Total                        1,225                  1,000               1,226"
        toy = "\n".join((*PAGE.splitlines()[:6], row, *PAGE.splitlines()[7:]))
        pdf = PDFDocument(SHA, (toy,), "test")
        bound = next(cell for cell in bind_page_row(pdf, page=1, row_line=7, caption_line=2)
                     if cell.variable == "contained_treo_kt")
        case = {"source_sha256": SHA, "project_id": "example", "physical_page": 1, "source_token": "1,225",
                "row": {"text": row}, "existing_reviewed_row_lexeme_overlap": ["old"]}
        old = dict(case, existing_observation_id="old", source_unit_as_reported="t",
                   role_guess="contained_treo_kt", existing_validation_id="validated")
        self.assertEqual(_baseline_overlap(case, bound, {"old": old})["status"], "EXISTING_REVIEWED_CELL_OVERLAP")
        self.assertEqual(_baseline_overlap(case, bound, {"old": {**old, "role_guess": "ore_mt"}})["status"],
                         "BASELINE_ROW_LEXEME_MATCH_UNRESOLVED")

    def test_printed_thousands_marker_is_not_an_unscaled_tonne(self):
        source = PAGE.replace("Contained TREO (t)", "TREO* (‘000 tonnes)")
        original = PDFDocument(SHA, (source,), "test")
        item = next(cell for cell in bind_page_row(original, page=1, row_line=7, caption_line=2)
                    if cell.variable == "contained_treo_kt")
        self.assertEqual(item.unit, "kt")
        self.assertIn("‘000 tonnes", item.unit_label.text)

    def test_original_non_resource_category_and_mixed_scope_do_not_authorize(self):
        spec = deepcopy(BINDING)
        next_caption = CAPTION.replace("Mineral Resource Estimate", "Ore Reserve Estimate")
        spec["table"]["caption"] = next_caption
        wrong = PAGE.replace(CAPTION, next_caption)
        value, = native_resource_table((wrong,), spec, spec["publication_date"])
        self.assertEqual(value.resource_class, "ORE_RESERVE")
        self.assertFalse(value.primary)


if __name__ == "__main__":
    unittest.main()
