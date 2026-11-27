"""Check the independent resource annotation partition and replay receipt."""

import os
from pathlib import Path
import runpy
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "evaluate_semantic_resource_holdout.py"
API = runpy.run_path(str(TOOL))


class SemanticResourceHoldoutTests(unittest.TestCase):
    def test_zero_denominators_are_unmeasured(self):
        matrix = API["confusion"]([(False, False)])
        self.assertEqual(matrix["tn"], 1)
        self.assertEqual(matrix["precision"], "UNMEASURED")
        self.assertEqual(matrix["recall"], "UNMEASURED")

    def test_reject_design_project_alias_even_with_different_source(self):
        cases = (
            {"project_id": "AU-ad64122c217f0dbfabc8", "source_sha256": "0" * 64,
             "company": "An alias", "report_family": "Secondary issuer announcement"},
        )
        with self.assertRaisesRegex(ValueError, "project_id"):
            API["_check_partition"](cases)

    @unittest.skipUnless(
        os.environ.get("AU_REE_FULL_SOURCE_MANIFEST") and os.environ.get("AU_REE_FRESH_ASSETS"),
        "fixed real-source manifest and content store are required",
    )
    def test_replay_scoped_original_measurements_and_use_outcomes(self):
        assets = Path(os.environ["AU_REE_FRESH_ASSETS"])
        store = assets.parent if assets.name == "objects" else assets
        receipt = API["evaluate"](Path(os.environ["AU_REE_FULL_SOURCE_MANIFEST"]), store)
        self.assertEqual(receipt["status"], "PASS")
        cells = receipt["observation_scoped_normalization_eligibility"]
        self.assertEqual((cells["tp"], cells["tn"], cells["fp"], cells["fn"]), (3, 6, 0, 0))
        r1 = receipt["r1_shadow_use_compatibility"]
        self.assertEqual((r1["tp"], r1["tn"], r1["fp"], r1["fn"]), (1, 3, 0, 0))
        self.assertEqual(receipt["s1_formal_use_compatibility"]["precision"], "UNMEASURED")
