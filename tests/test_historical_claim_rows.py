import unittest

from au_ree_v3.documents import PDFDocument
from au_ree_v3.native_table_discovery import NativeResourceRow
from au_ree_v3.resource_table_binding import CellBinding, Span
from tools.evaluate_oxide_table_retrieval import _native_cell_scores
from tools.historical_claim_rows import _negative_page_rows, locate_claim_row


class HistoricalClaimRowsTest(unittest.TestCase):
    def test_markers_must_identify_unique_original_row_and_exact_token(self):
        caption = "Table 3: Rare Earth Mineral Resource Estimate"
        valid = "Total      7.3      0.96      70,500"
        different = "Total     107.3      0.96      70,500"
        markers = ["Mineral Resource Estimate", "7.3", "0.96", "70,500"]
        self.assertEqual(locate_claim_row("\n".join([caption, different, valid]), markers, "7.3"), (3, None))
        self.assertEqual(locate_claim_row("\n".join([caption, valid, valid]), markers, "7.3"),
                         (None, "AMBIGUOUS_MARKED_TABLE_ROW"))
        self.assertEqual(locate_claim_row("\n".join([caption, different]), markers, "7.3"),
                         (None, "PAGE_MARKERS_NOT_FOUND"))

    def test_same_document_controls_require_separate_excluded_table(self):
        source = "d" * 64
        page = "\n".join([
            "Table 1: Ore Reserves", "Class     Ore Mt     TREO ppm", "Proven     10    1300",
            "Total      20    1600", "", "Table 2: Mineral Resource Estimate", "Class     Ore Mt     TREO ppm",
            "Total      40    1000", "", "This unlabelled row     10     1200",
        ])
        rows = list(_negative_page_rows(PDFDocument(source, (page,), "test"), source, 1, "demo"))
        self.assertEqual([row["line"] for row in rows], [3, 4])
        self.assertTrue(all(row["label"] is False and row["source"] == source for row in rows))

    def test_value_metrics_require_the_exact_column_even_when_literal_repeats(self):
        held = [{"source": "fixture", "page": 1, "line": 3, "label": True}]
        expected = {"variable": "ore_mt", "unit": "Mt", "literal": "125", "start_column": 11, "end_column": 13}
        probes = {("fixture", 1, 3): {"expected_fields": [expected], "forbidden_bindings": []}}
        correct = CellBinding("ore_mt", "Mt", None, Span(1, 3, 11, 13, "125"), (), None, None, "test")
        wrong = CellBinding("ore_mt", "Mt", None, Span(1, 3, 18, 20, "125"), (), None, None, "test")
        parsed = {("fixture", 1): (NativeResourceRow(1, 1, 3, "test", (correct, wrong)),)}
        scores = _native_cell_scores(held, [True], parsed, probes)
        self.assertEqual((scores["TP"], scores["FP"], scores["FN"]), (1, 1, 0))


if __name__ == "__main__":
    unittest.main()
