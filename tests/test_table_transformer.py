"""Numeric cells retain original native word identities in structure experiments."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.table_geometry import _Fragment
from au_ree_v3.table_transformer import _column_word_groups, _pinned_bytes, _rect, _unique_boxes


class TableTransformerTests(unittest.TestCase):
    def test_model_candidates_cannot_supply_or_duplicate_original_numeric_words(self):
        words = (
            _Fragment("Measured", (5, 10, 40, 14)),
            _Fragment("23.1", (44, 11, 50, 14)),
            _Fragment("99.9", (80, 11, 90, 14)),
            _Fragment("0.34", (121, 11, 127, 14)),
        )
        columns = ((41, 0, 90, 30), (78, 0, 130, 30), (132, 0, 150, 30))
        self.assertEqual(_column_word_groups(words, columns), (words[0], words[1], words[3]))

    def test_edge_clipping_and_duplicate_predictions_do_not_duplicate_regions(self):
        area = (10, 20, 110, 120)
        self.assertEqual(_rect((0.5, 0.5, 1.2, 1.2), area), area)
        self.assertEqual(_unique_boxes([(0, 0.7, area), (0, 0.9, area), (1, 0.9, area)], 0), [area])

    def test_model_requires_exact_pinned_bytes(self):
        with TemporaryDirectory() as folder:
            path = Path(folder, "mistaken_source.onnx")
            path.write_bytes(b"different model")
            with self.assertRaisesRegex(ValueError, "detection model SHA-256"):
                _pinned_bytes(path, "detection")
