"""Check independent Decimal and exact source witnesses for scoped R1 challenges."""

from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
import runpy
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
PROFILES = ROOT / "config/research_profiles"
SCRIPT = ROOT / "tools/audit_resource_statement_holds.py"
CHALLENGE = PROFILES / "resource_comparability_challenge_v1.json"


class ResourceComparisonMathTest(unittest.TestCase):
    def test_reports_can_disagree_when_both_masses_fit_the_same_rounded_ore_and_grade(self):
        arithmetic = runpy.run_path(str(SCRIPT))["_arithmetic"]
        yin = json.loads(CHALLENGE.read_bytes())["cases"][0]
        quotes = {row["id"]: row["quote"] for edition in yin["editions"] for row in edition["witnesses"]}
        verdicts = arithmetic(yin, quotes)
        self.assertEqual([item["intersects"] for item in verdicts], [False, True, True, True])
        self.assertEqual(verdicts[0]["left_bounds_kt"], ["311.750", "311.850"])
        self.assertEqual(verdicts[0]["right_bounds_kt"], ["312.250", "312.350"])
        self.assertEqual(verdicts[2]["left_bounds_kt"], ["310.241250", "313.343250"])
        bad_quote = dict(quotes, **{"yin-detail-units": "Contained TREO (%)"})
        with self.assertRaisesRegex(ValueError, "unit is absent"):
            arithmetic(yin, bad_quote)
        changed_scope = deepcopy(yin)
        changed_scope["decimal_checks"][0]["inputs"][0]["token"] = "311,799"
        with self.assertRaisesRegex(ValueError, "token is absent"):
            arithmetic(changed_scope, quotes)


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "AU_REE_R1_CHALLENGE_BUILD", "AU_REE_R1_CHALLENGE_FIXED", "AU_REE_R1_CHALLENGE_ASSETS", "AU_REE_R1_CHALLENGE_ANNEX",
)), "separately captured original resources and fixed exact release required")
class ExactResourceComparabilityChallengeTest(unittest.TestCase):
    def test_independent_source_readback_and_formal_lineage_refusal(self):
        audit = runpy.run_path(str(SCRIPT))["audit_comparability"]
        profile = PROFILES / "au_ree_2026-11-28.json"
        fixed, assets, build, root = (Path(os.environ[name]) for name in (
            "AU_REE_R1_CHALLENGE_FIXED", "AU_REE_R1_CHALLENGE_ASSETS", "AU_REE_R1_CHALLENGE_BUILD", "AU_REE_R1_CHALLENGE_ANNEX",
        ))
        annexes = [(key, root / f"{key}-expansion/source_manifest.json", root / f"{key}-cas")
                   for key in ("yin", "yangibana")]
        receipt = audit(profile, fixed, assets, build, CHALLENGE, annexes)
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["known_scope_negative_count"], 3)
        self.assertEqual((receipt["pdf_decodes"], receipt["pdf_pages_decoded"]), (6, 233))
        self.assertEqual(receipt["generalized_admission_precision_recall"], "UNMEASURED")
        north = receipt["cases"][-1]
        self.assertEqual(north["state"], "EVIDENCE_INSUFFICIENT")
        self.assertTrue(north["decimal_checks"][0]["intersects"])
        with tempfile.TemporaryDirectory() as folder:
            altered = Path(folder)
            (altered / "decision").mkdir()
            inputs = json.loads((build / "decision/mcda_inputs.json").read_bytes())
            record = inputs["criteria_by_project"][north["project_id"]]["R1"]
            record["lineage"] = [{"source_sha256": north["editions"][0]["source_sha256"]}]
            payload = json.dumps(inputs).encode()
            (altered / "decision/mcda_inputs.json").write_bytes(payload)
            report = json.loads((build / "build_receipt.json").read_bytes())
            report["mcda_inputs_hash"] = sha256(payload).hexdigest()
            (altered / "build_receipt.json").write_text(json.dumps(report))
            with self.assertRaisesRegex(ValueError, "held original edition has entered formal R1 lineage"):
                audit(profile, fixed, assets, altered, CHALLENGE, annexes)
