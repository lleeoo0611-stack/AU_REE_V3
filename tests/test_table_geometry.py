"""Evidence-preserving coordinate geometry for native PDF tables."""

from importlib.util import find_spec
import os
from pathlib import Path
from unittest import TestCase, skipUnless

from au_ree_v3.documents import DocumentError
from au_ree_v3.table_geometry import METHODS, open_table_geometry


_MUPDF = find_spec("pymupdf") is not None
_CIRCLE_SHA = "259ebe012c6b6e2048ca2f50f5b47e4c91d9a56e520c97f6deee554d069a54ad"


@skipUnless(_MUPDF, "optional table geometry dependencies required")
class TableGeometryTest(TestCase):
    @staticmethod
    def _example():
        import pymupdf

        with pymupdf.open() as document:
            page = document.new_page()
            widths = (38, 145, 235, 325, 430, 530)
            grids = (
                (60, "Table 1 - Wattle Ridge Mineral Resources", (("Measured", "50", "800", "750", "14"),
                                                               ("Total", "76", "850", "787", "25"))),
                (236, "Table 2 - Wattle Ridge Exploration Target", (("Total", "55", "1,210", "920", "50"),)),
            )
            for start, title, values in grids:
                page.insert_text((38, start), title, fontsize=11)
                rows = (("JORC Category", "Tonnes", "TREO", "TREO-CeO 2", "Nd 2 O 3"),
                        ("", "(Mt)", "(ppm)", "(ppm)", "(ppm)"), *values)
                top = start + 15
                bottom = top + len(rows) * 18
                for x in widths:
                    page.draw_line((x, top), (x, bottom))
                for index in range(len(rows) + 1):
                    y = top + index * 18
                    page.draw_line((widths[0], y), (widths[-1], y))
                for index, row in enumerate(rows):
                    for x, token in zip(widths, row):
                        if token:
                            page.insert_text((x + 5, top + index * 18 + 13), token, fontsize=9)
            page.insert_text((20, 300), "for personal use only", fontsize=17, rotate=90)
            return document.tobytes()

    def test_both_adapters_preserve_quantity_class_and_original_witness(self):
        body = self._example()
        for method in METHODS:
            if find_spec(method) is None:
                continue
            with self.subTest(method=method), open_table_geometry(body, method) as geometry:
                self.assertEqual(geometry.page_count, 1)
                rows = geometry.page(1)
                self.assertEqual(geometry.page(1), rows)
                with self.assertRaisesRegex(DocumentError, "PHYSICAL_PAGE_MISSING"):
                    geometry.page(2)
                measured, total = (row for row in rows if row.table_kind == "MINERAL_RESOURCE")
                self.assertEqual((measured.category, total.category), ("MEASURED_ONLY", "TOTAL_UNSPECIFIED"))
                self.assertEqual((measured.row_label, total.row_label), ("Measured", "Total"))
                self.assertEqual(total.caption, "Table 1 - Wattle Ridge Mineral Resources")
                self.assertNotIn("personal", total.text)
                cells = {cell.text: cell for cell in total.cells}
                self.assertEqual((cells["76"].role, cells["76"].unit), ("ore_mt", "Mt"))
                self.assertEqual((cells["850"].role, cells["850"].unit), ("grade", "ppm"))
                self.assertIsNone(cells["787"].role)
                self.assertEqual(cells["787"].header, "TREO-CeO 2")
                self.assertEqual(cells["25"].role, "nd2o3_ppm")
                self.assertLess(cells["76"].header_bbox[1], cells["76"].unit_bbox[1])
                self.assertLess(cells["76"].unit_bbox[1], cells["76"].bbox[1])
                self.assertTrue(cells["76"].locator.startswith("PDF page 1, top-left points ["))
                target, = (row for row in rows if row.table_kind == "NON_RESOURCE")
                self.assertTrue(all(cell.role is None for cell in target.cells))

    def test_original_circle_valley_reads_plain_and_modified_treo_separately(self):
        if not (path := os.environ.get("AU_REE_COHORT_CIRCLE_VALLEY_PDF")):
            self.skipTest("original Circle Valley PDF required")
        from hashlib import sha256

        body = Path(path).read_bytes()
        self.assertEqual(sha256(body).hexdigest(), _CIRCLE_SHA)
        for method in METHODS:
            if find_spec(method) is None:
                continue
            with self.subTest(method=method), open_table_geometry(body, method) as geometry:
                rows = geometry.page(2)
                total, = (row for row in rows if row.row_label == "Total")
                self.assertEqual(total.table_kind, "MINERAL_RESOURCE")
                self.assertEqual(total.category, "TOTAL_UNSPECIFIED")
                self.assertIn("Circle Valley Rare Earth Mineral Resource", total.caption)
                self.assertEqual({(cell.role, cell.text, cell.unit) for cell in total.cells if cell.role},
                                 {("ore_mt", "98", "Mt"), ("grade", "890", "ppm")})
                modified, = (cell for cell in total.cells if cell.text == "660")
                self.assertIsNone(modified.role)
                self.assertEqual(modified.unit, "ppm")
                self.assertEqual(modified.header.replace(" ", ""), "TREO-CeO2")
                scandium, = (cell for cell in total.cells if cell.text == "25")
                self.assertEqual(scandium.unit, "g/t")
                self.assertIsNone(scandium.role)
