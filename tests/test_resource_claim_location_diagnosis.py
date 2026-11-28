from unittest import TestCase

from tools.diagnose_resource_claim_locations import _compare_printed, _marker_lines, diagnose_page, recheck_native_cell


class ClaimLocationDiagnosisTest(TestCase):
    def test_only_printed_value_equality_ignores_grouping(self):
        self.assertEqual(_compare_printed("4,389", "4389"), "EQUAL_VALUE_DIFFERENT_FORMAT")
        self.assertEqual(_compare_printed("4,389", "4389.1"), "DIFFERENT_VALUES_OVERLAPPING_PRINTED_PRECISION")
        self.assertEqual(_compare_printed("4,390", "4389"), "DIFFERENT_VALUES_OVERLAPPING_PRINTED_PRECISION")
        self.assertEqual(_compare_printed("4,391", "4389"), "DIFFERENT_PRINTED_VALUE")
        self.assertEqual(_compare_printed("1,2", "12"), "TARGET_TOKEN_NOT_COMPARED")

    def test_exact_marker_preserves_multiline_native_location(self):
        result, = _marker_lines(7, ["Estimated mineral", "", "resource increased"], ["Estimated mineral resource"])
        self.assertEqual(result["lines"], [[1, 3]])
        self.assertEqual(result["locators"], ["PDF page 7, lines 1-3"])

    def test_caption_and_row_diagnosis_keeps_literal_and_all_anchors(self):
        binding = {
            "mode": "native_cell", "page": 3,
            "table_bbox": [0, 0, 20, 20], "row_bbox": [0, 10, 20, 14], "column_bbox": [6, 5, 10, 20],
            "table_label": "Table 1", "table_label_bbox": [1, 1, 4, 4],
            "row_label": "Total", "row_label_bbox": [1, 10, 4, 14],
            "column_label": "Volume", "column_label_bbox": [6, 5, 10, 8],
            "unit_label": "Mt", "unit_bbox": [6, 8, 10, 10],
        }
        source_readback = {
            (0, 0, 20, 20): "Table 1\nVolume Mt\nTotal 11 1130",
            (0, 10, 20, 14): "Total 11 1130", (6, 10, 10, 14): "11",
            (1, 1, 4, 4): "Table 1", (1, 10, 4, 14): "Total", (6, 5, 10, 8): "Volume", (6, 8, 10, 10): "Mt",
        }
        native = "Table 1: Source\nVolume   Mt\nTotal   11    1130"
        claim = {"original_token": "11", "page_markers": ["Table 1", "Volume", "Mt"], "cell_binding": binding}
        actual = diagnose_page(3, native, native, False, claim, lambda rect: source_readback[tuple(rect)])
        self.assertEqual(actual["status"], "BOXED_CELL_FOUND_MARKERS_SPAN_TABLE_ZONES")
        self.assertEqual(actual["marker_regions"], {"printed_header": 3})
        self.assertEqual(actual["native_box_readback"]["cell"]["printed_numeric_token"], "11")
        self.assertTrue(actual["native_box_readback"]["anchors"]["unit_label"]["matches_hint"])
        self.assertEqual(recheck_native_cell(4, claim, source_readback.get)["status"], "HISTORICAL_GEOMETRY_UNUSABLE")

    def test_cross_table_requires_two_literal_printed_tables(self):
        native = (
            "Table 1: Market share\nAlpha   11   31\nBeta   17   41\n\n"
            "Table 2: Annual quota\nGama   22   52\nDelta   28   62"
        )
        claim = {"page_markers": ["11", "22"]}
        result = diagnose_page(5, native, native, False, claim, lambda rect: "")
        self.assertEqual(result["status"], "MARKERS_SPAN_SEPARATELY_LABELLED_TABLES")
        self.assertEqual(result["printed_caption_locators"], ["PDF page 5, line 1", "PDF page 5, line 5"])
        arc = "These press releases discuss Table 1 and Table 2.\nThe plants have announced 11 and 22 variants."
        result = diagnose_page(5, arc, arc, False, claim, lambda rect: "")
        self.assertEqual(result["status"], "MARKERS_IN_NARRATIVE_TEXT")

    def test_image_without_native_text_differs_from_unknown_layout(self):
        claim = {"page_markers": ["11"]}
        actual = diagnose_page(4, "", "", True, claim, lambda rect: "")
        self.assertEqual(actual["status"], "NO_NATIVE_TEXT_RASTER_PRESENT")
        actual = diagnose_page(4, "", "", False, claim, lambda rect: "")
        self.assertEqual(actual["status"], "NO_EXTRACTABLE_PAGE_TEXT")
