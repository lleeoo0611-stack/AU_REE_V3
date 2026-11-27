"""Replay semantic stages against the locked original evidence and production Store."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.semantic_research import run_resource_ablation


@unittest.skipUnless(all(os.environ.get(name) for name in (
    "AU_REE_BASELINE_BUILD", "AU_REE_FULL_SOURCE_MANIFEST", "AU_REE_RESOURCE_YIELD_ASSETS",
)), "full immutable V3 research fixtures required")
class SemanticResearchTest(unittest.TestCase):
    def test_separated_stages_preserve_formal_release_and_read_original_design_context(self):
        settings = Path("config/research_profiles")
        baseline = Path(os.environ["AU_REE_BASELINE_BUILD"])
        manifest = Path(os.environ["AU_REE_FULL_SOURCE_MANIFEST"])
        assets = Path(os.environ["AU_REE_RESOURCE_YIELD_ASSETS"])
        original = baseline / "observations/research.sqlite"
        original_sha = sha256(original.read_bytes()).hexdigest()
        with TemporaryDirectory() as work:
            output = Path(work)
            receipt = run_resource_ablation(
                settings / "au_ree_2026-11-28.json", baseline, manifest, assets, output,
                reread_bindings=settings / "semantic_reread_bindings_v1.json",
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(receipt["corpus_sha_count"], 144)
            self.assertEqual(receipt["variants"]["B"]["readback"], {"ORIGINAL_SPANS_LOCATED": 38})
            self.assertEqual({len(stage["formal_keys"]) for stage in receipt["variants"].values()}, {6})
            self.assertEqual(receipt["formal_gain"]["net"], 0)
            case, = json.loads((output / "resource_original_reread.json").read_bytes())
            self.assertEqual(case["B"]["source_context"]["effective_date"], "2025-11-17")
            self.assertEqual(case["C"]["resource_treo_grade"]["value"], "0.2100")
            self.assertEqual(case["C"]["contained_treo"]["value"], "82.000")
            self.assertEqual(case["D"]["formal_admission"], "CANDIDATE")
        self.assertEqual(sha256(original.read_bytes()).hexdigest(), original_sha)
