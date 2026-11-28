"""Check the Stage 2 audit boundary and replay the fixed corpus when available."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "census_stage2_baseline.py"
SPEC = importlib.util.spec_from_file_location("census_stage2_baseline", TOOL)
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class CensusStage2BaselineTests(unittest.TestCase):
    def test_shadow_keeps_missing_grid_value_and_formal_value_takes_precedence(self):
        cell = {"value_state": "UNKNOWN", "evidence_state": "EVIDENCE_INSUFFICIENT", "reason": "NO_SELECTED_MRE"}
        shadow = ("SHADOW", "formula", "NO_APPROVED_CENSUS_DERIVATION_SELECTION")
        self.assertEqual(audit.funnel_stage(cell, shadow), shadow)
        self.assertEqual(audit.funnel_stage(cell)[0], "EVIDENCE_INSUFFICIENT")
        cell.update(value_state="PRESENT", evidence_state="FORMAL_DIRECT", reason="DIRECT")
        self.assertEqual(audit.funnel_stage(cell, shadow)[0], "REPORTED_DIRECT")

    def test_missing_resource_is_never_true_not_applicable(self):
        with self.assertRaisesRegex(ValueError, "Missing selection"):
            audit.funnel_stage({"value_state": "NOT_APPLICABLE", "evidence_state": "DESCRIPTIVE_ONLY",
                                "reason": "NO_SELECTED_MRE"})

    def test_pins_and_existing_output_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "fixture"
            evidence.write_bytes(b"original")
            fingerprint = audit.digest(evidence.read_bytes())
            self.assertEqual(audit.checked_bytes(evidence, fingerprint), b"original")
            evidence.write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "Pinned input differs"):
                audit.checked_bytes(evidence, fingerprint)
            with self.assertRaises(FileExistsError):
                audit.build_baseline(root, root, root, root)
            self.assertEqual(evidence.read_bytes(), b"changed")

    @unittest.skipUnless(os.environ.get("AU_REE_STAGE2_WORKSPACE"), "fixed archive and acquired CAS required")
    def test_exact_current_and_archive_replay_reproduces_output(self):
        root = Path(os.environ["AU_REE_STAGE2_WORKSPACE"])
        stage = root / "work" / "census-stage2"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "verification"
            actual = audit.build_baseline(root, stage / "baseline-main-074e00d", stage / "archive-cold-a", output)
            self.assertEqual(actual["counts"]["cells"], 4743)
            self.assertEqual(actual["counts"]["formal_derived_cells"], 0)
            self.assertEqual(actual["counts"]["reported_science_cells"], 16)
            self.assertEqual(actual["funnel"]["stages"]["SHADOW"], 7)
            for artifact in ("stage2_baseline.json", "census_gain_funnel.csv"):
                self.assertEqual((output / artifact).read_bytes(), (stage / "stage2-final-a" / artifact).read_bytes())


if __name__ == "__main__":
    unittest.main()
