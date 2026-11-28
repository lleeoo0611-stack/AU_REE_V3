"""Test the independent S1 evidence audit with and without exact source assets."""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json
import os
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, "path", [str(ROOT / "tools"), *sys.path]):
    from audit_resource_composition import audit, nominal_interval, _pinned_json


class ResourceCompositionAuditTests(unittest.TestCase):
    def test_printed_precision_is_unit_specific_and_preserves_trailing_digits(self):
        self.assertEqual(nominal_interval("5,700", "ppm"), (Decimal("5699.5"), Decimal("5700.5")))
        self.assertEqual(nominal_interval("5.70", "kg/t"), (Decimal("5695"), Decimal("5705")))
        self.assertEqual(nominal_interval("5.7", "kg/t"), (Decimal("5650"), Decimal("5750")))

    def test_changed_scientific_input_is_refused_before_its_contents_are_used(self):
        manifest_sha = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"
        with TemporaryDirectory() as directory:
            document = Path(directory) / "manifest.json"
            document.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Exact review input changed"):
                _pinned_json(document, manifest_sha)

    @unittest.skipUnless(os.environ.get("AU_REE_COMPOSITION_AUDIT_INPUTS"), "fixed and expanded exact review corpora required")
    def test_all_named_originals_bind_and_frozen_panel_has_no_new_key(self):
        inputs = json.loads(Path(os.environ["AU_REE_COMPOSITION_AUDIT_INPUTS"]).read_bytes())
        result = audit(**{key: Path(value) for key, value in inputs.items()})
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["hypothesis_status"], "HYPOTHESIS_NOT_SUPPORTED")
        self.assertEqual((result["new_comparable_s1_keys"], result["new_complete_panel_projects"]), (0, 0))
        self.assertEqual(result["reviewed_pdf_count"], 9)
        self.assertEqual({case["s1_state"] for case in result["cases"]}, {"UNKNOWN", "COMPARABLE"})
        y = result["wolverine_precision"]["Y2O3"]
        self.assertEqual(y[-2]["nominal_ppm"], y[-1]["nominal_ppm"])
        self.assertEqual(y[-2]["nominal_ppm"], ["5655.000", "5665.000"])
