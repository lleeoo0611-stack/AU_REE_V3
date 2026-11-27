import csv
from hashlib import sha256
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.migration.baseline import ARCHIVED_CENSUS, PinnedRepository, _verify_configuration_assets, asset_inventory, census_counts, verify_baseline
from au_ree_v3.migration.golden import build_golden


def csv_bytes(header, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue().encode()


def sample_census(grade="", *, duplicate=False):
    census = csv_bytes(("專案ID", "grade", "deposit"), [("P", grade, "")])
    status = [
        ("P", "專案ID", "PRESENT", "VALUE_PRESENT"),
        ("P", "grade", "MISSING", "NO_SOURCE_VALUE"),
        ("P", "deposit", "MISSING", "NOT_APPLICABLE_NO_MRE"),
    ]
    prior = [
        ("P", "專案ID", "PRESENT", "VALUE_PRESENT"),
        ("P", "grade", "MISSING", "NO_SOURCE_VALUE"),
        ("P", "deposit", "MISSING", "NO_SOURCE_VALUE"),
    ]
    provenance = [("P", "專案ID", "P", ""), ("P", "grade", grade, ""), ("P", "deposit", "", "")]
    gaps = [("P", "grade", grade, "NO_SOURCE_VALUE"), ("P", "deposit", "", "NOT_APPLICABLE_NO_MRE")]
    header = ("project_id", "column", "value_state", "status")
    return (
        census,
        csv_bytes(header, status + status[:1] if duplicate else status),
        csv_bytes(("project_id", "column", "value", "source_sha256"), provenance),
        csv_bytes(header, prior),
        csv_bytes(("project_id", "column", "current_value", "current_status"), gaps),
        ["P"],
    )


class CensusAccountingTests(unittest.TestCase):
    def test_frozen_and_current_denominators_are_independent(self):
        counts = census_counts(*sample_census())
        self.assertEqual(counts["historical_frozen_denominator"], 3)
        self.assertEqual(counts["applicable"], 2)
        self.assertEqual(counts["historical_applicable_now_excluded"], 1)
        self.assertEqual(counts["historical_fixed_coverage_pct"], "33.33%")
        self.assertEqual(counts["current_coverage_pct"], "50.00%")

    def test_zero_cannot_replace_a_missing_value(self):
        with self.assertRaisesRegex(ValueError, "Inconsistent Census value"):
            census_counts(*sample_census("0"))

    def test_duplicate_cell_fails(self):
        with self.assertRaisesRegex(ValueError, "Duplicate Census coordinates"):
            census_counts(*sample_census(duplicate=True))


class AvailabilityTests(unittest.TestCase):
    def test_current_asset_bytes_determine_status(self):
        contents = [b"matching", b"wrong", b"missing", b"out of scope"]
        shas = [sha256(value).hexdigest() for value in contents]
        records = [
            {"expected_sha256": sha, "status": "AVAILABLE_EXACT_ORIGINAL",
             "locked_sources": [], "project_ids_in_historical_lineage": []}
            for sha in shas
        ]
        with TemporaryDirectory() as dir_name:
            root = Path(dir_name)
            library = root / "library" / "assets"
            library.mkdir(parents=True)
            (library / shas[0]).write_bytes(contents[0])
            (library / shas[1]).write_bytes(b"wrong version")
            (library / shas[3]).write_bytes(contents[3])
            inventory = asset_inventory(
                PinnedRepository(root / "v2"), set(shas[:3]), {"assets": records}, library.parent,
            )
        states = {row["expected_sha256"]: row["status"] for row in inventory}
        self.assertEqual([states[sha] for sha in shas], ["EXACT_SHA", "WRONG_SHA", "MISSING", "NOT_REQUIRED_FOR_SCOPE"])

    def test_missing_commit_blocks_references_without_creating_expected_data(self):
        with TemporaryDirectory() as dir_name:
            root = Path(dir_name)
            ref = root / "not-a-repo"
            receipt = verify_baseline(ref, ARCHIVED_CENSUS, root / "baseline")
            self.assertEqual(receipt["status"], "BLOCKED")
            self.assertEqual(receipt["asset_inventory_counts"], {})
            out = root / "golden"
            manifest = build_golden(ref, out)
            self.assertEqual(manifest["status"], "BLOCKED")
            self.assertFalse((out / "golden_expected.jsonl").exists())

    def test_outputs_inside_v2_reference_are_refused(self):
        with TemporaryDirectory() as dir_name:
            root = Path(dir_name)
            out = root / "work"
            with self.assertRaisesRegex(ValueError, "outside the V2"):
                verify_baseline(root, ARCHIVED_CENSUS, out)
            self.assertFalse(out.exists())


class GoldenArtifactsTests(unittest.TestCase):
    def test_frozen_fixture_manifest_matches_artifact_bytes_and_case_ids(self):
        golden = Path(__file__).parent / "golden"
        manifest = json.loads((golden / "oracle_manifest.json").read_bytes())
        ids = []
        for key in ("expected_file", "cases_file"):
            pin = manifest[key]
            data = (golden / pin["path"]).read_bytes()
            self.assertEqual(sha256(data).hexdigest(), pin["sha256"])
            ids.append([json.loads(line)["case_id"] for line in data.splitlines()])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(len(set(ids[0])), manifest["case_count"])


class RecoveredConfigurationTests(unittest.TestCase):
    def test_only_exact_recovered_bytes_can_clear_a_mismatch(self):
        with TemporaryDirectory() as folder:
            assets = Path(folder)
            (assets / "configuration").mkdir()
            exact = sha256(b"sealed").hexdigest()
            mismatch = sha256(b"historic").hexdigest()
            configurations = {
                "summary_rules.json": {"published_sha256": exact, "status": "BLOCKED", "reason": "REPLAY_ENVIRONMENT_MISMATCH"},
                "research.json": {"published_sha256": mismatch, "status": "BLOCKED", "reason": "REPLAY_ENVIRONMENT_MISMATCH"},
            }
            (assets / "configuration" / exact).write_bytes(b"sealed")
            (assets / "configuration" / mismatch).write_bytes(b"other")
            _verify_configuration_assets(configurations, assets)
            self.assertEqual(configurations["summary_rules.json"]["status"], "PASS")
            self.assertEqual(configurations["research.json"]["status"], "BLOCKED")
            self.assertEqual(configurations["research.json"]["recovered_asset_status"], "WRONG_SHA")
