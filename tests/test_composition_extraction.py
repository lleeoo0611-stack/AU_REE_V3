"""Check the recovered oxide table against independently transcribed source cells."""

from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import unittest

from au_ree_v3.composition_extraction import classified_total_oxides
from au_ree_v3.documents import DocumentError, pdf_pages
from au_ree_v3.resource_extraction import classified_sections, narrative_table


BINDINGS = Path(__file__).resolve().parents[1] / "config/research_profiles/resource_bindings_v1.json"
WOLVERINE = "e29ab1305c8edb5bd1074806d5cf31c323c82cfd68c5c5b2e9b545137f09ccb0"
MT_WELD = "fe26ac29c4773a5af95e909a1cd95ca1e9ad37c34560dfa72c1e9674e4c8a503"


@unittest.skipUnless(os.environ.get("AU_REE_FRESH_ASSETS") and shutil.which("pdftotext"),
                     "exact issuer PDF bytes and pdftotext required")
class CompositionEvidenceTests(unittest.TestCase):
    def pages(self, expected_hash, last_page):
        assets = Path(os.environ["AU_REE_FRESH_ASSETS"])
        candidates = (assets / expected_hash, assets / f"{expected_hash}.pdf")
        confirmed = next((path for path in candidates if path.exists()), None)
        self.assertIsNotNone(confirmed, f"missing exact evidence {expected_hash}")
        content = confirmed.read_bytes()
        self.assertEqual(sha256(content).hexdigest(), expected_hash)
        return pdf_pages(content, last_page)

    def test_wolverine_constituents_anchor_to_matching_total_table(self):
        bindings = json.loads(BINDINGS.read_bytes())["bindings"]
        binding = next(item for item in bindings if item["case_id"] == "wolverine-deposit-total")
        pages = self.pages(WOLVERINE, 6)
        primary, = (item for item in classified_sections(pages, binding, binding["publication_date"])
                    if item.primary)
        binding = {**binding, "composition_table": {"page": 6, "number": 5}}
        cells = classified_total_oxides(pages, binding, primary)
        actual = [(item.chemical_basis, item.token, item.raw_unit, item.page, item.table, item.row)
                  for item in cells]
        self.assertEqual(actual, [
            ("Pr6O11", "60", "ppm", 6, "Table 5", "Total"),
            ("Nd2O3", "280", "ppm", 6, "Table 5", "Total"),
            ("Dy2O3", "840", "ppm", 6, "Table 5", "Total"),
            ("Tb4O7", "120", "ppm", 6, "Table 5", "Total"),
        ])
        self.assertEqual(primary.measurements[1].token, "0.96")
        self.assertEqual(primary.cutoff, "0.15")
        self.assertEqual(primary.effective_date, "2025-01-15")

        binding["composition_table"] = {"page": 6, "number": 4}
        with self.assertRaisesRegex(DocumentError, "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH"):
            classified_total_oxides(pages, binding, primary)

    def test_mt_weld_reserve_table_does_not_supply_resource_constituents(self):
        bindings = json.loads(BINDINGS.read_bytes())["bindings"]
        binding = next(item for item in bindings if item["case_id"] == "mt-weld-resource")
        pages = self.pages(MT_WELD, 23)
        resource, = narrative_table(pages, binding, binding["publication_date"])
        binding = {**binding, "section": "Total", "composition_table": {"page": 23, "number": 7}}
        with self.assertRaisesRegex(DocumentError, "OXIDE_SCOPE_DATE_OR_CUTOFF_MISMATCH"):
            classified_total_oxides(pages, binding, resource)
