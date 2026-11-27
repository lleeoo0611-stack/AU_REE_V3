"""Exercise the fixed research cohort and validate identity-only input boundaries."""

from collections import Counter
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from au_ree_v3.migration.project_registry import verify_reference
from au_ree_v3.registry import PROJECT_ID_COLUMN, load_census_columns, load_profile, load_registry

PROFILE_PATH = Path(__file__).resolve().parents[1] / "config/research_profiles/au_ree_2026-11-28.json"


class ProjectRegistryTests(unittest.TestCase):
    def test_pinned_profile_uses_fixed_identity_and_ordered_columns(self):
        profile = load_profile(PROFILE_PATH)
        registry = load_registry(profile.registry_path)
        columns = load_census_columns(profile.census_columns_path)
        ids = {project.project_id for project in registry.projects}
        self.assertEqual((len(registry.projects), len(ids), len(columns)), (93, 93, 51))
        self.assertEqual(columns[0], PROJECT_ID_COLUMN)
        self.assertEqual(columns[-1], "ASX代碼")
        self.assertEqual(profile.research_cutoff, date(2026, 11, 28))
        self.assertEqual(profile.timezone.key, "Asia/Taipei")
        self.assertEqual({field for project in registry.projects for field in project.__slots__}, {
            "project_id", "name", "state", "developer", "listing_code", "identity_links",
        })
        self.assertTrue({link.related_id for p in registry.projects for link in p.identity_links}.isdisjoint(ids))

    def test_locked_seed_catalog_covers_fixed_projects_and_per_project_priority(self):
        profile = load_profile(PROFILE_PATH)
        projects = load_registry(profile.registry_path).projects
        payload = json.loads(profile.source_catalog_path.read_bytes())
        seeds = payload["seed_urls"]
        ids = {project.project_id for project in projects}
        priorities = Counter()
        self.assertEqual(payload["seed_role"], "HISTORICAL_SEARCH_POINTER_ONLY")
        self.assertEqual(len({seed["url"] for seed in seeds}), len(seeds))
        for seed in seeds:
            self.assertTrue(seed["url"].startswith("https://"))
            self.assertNotIn("temporal_evidence", seed)
            self.assertEqual(set(seed["priority_by_project"]), set(seed["project_ids"]))
            self.assertEqual(set(seed["association_references"]), set(seed["project_ids"]))
            priorities.update(seed["project_ids"])
        self.assertEqual(set(priorities), ids)
        for project_id, size in priorities.items():
            ranks = [seed["priority_by_project"][project_id] for seed in seeds
                     if project_id in seed["priority_by_project"]]
            self.assertEqual(sorted(ranks), list(range(1, size + 1)))

    def test_duplicate_project_and_in_cohort_alias_are_rejected(self):
        profile = load_profile(PROFILE_PATH)
        original = json.loads(profile.registry_path.read_bytes())
        with TemporaryDirectory() as workspace:
            registry_path = Path(workspace) / "registry.json"
            duplicate = {**original, "projects": [*original["projects"], original["projects"][0]]}
            registry_path.write_text(json.dumps(duplicate), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                load_registry(registry_path)
            parent = next(project for project in original["projects"] if project["identity_links"])
            parent["identity_links"][0]["related_id"] = original["projects"][0]["project_id"]
            registry_path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Invalid external alias"):
                load_registry(registry_path)

    def test_profile_rejects_component_hash_change_and_path_escape(self):
        with TemporaryDirectory() as workspace:
            directory = Path(workspace)
            profile = json.loads(PROFILE_PATH.read_bytes())
            path = directory / PROFILE_PATH.name
            for component in profile["inputs"].values():
                src = PROFILE_PATH.parent / component["path"]
                (directory / component["path"]).write_bytes(src.read_bytes())
            path.write_text(json.dumps(profile), encoding="utf-8")
            load_profile(path)
            registry = directory / profile["inputs"]["project_registry"]["path"]
            registry.write_bytes(registry.read_bytes() + b"\n")
            with self.assertRaisesRegex(ValueError, "fingerprint"):
                load_profile(path)
            profile["inputs"]["project_registry"] = {
                "path": "../external.json", "sha256": sha256(b"{}").hexdigest(),
            }
            path.write_text(json.dumps(profile), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "relative"):
                load_profile(path)

    def test_absent_reference_writes_blocked_receipt(self):
        with TemporaryDirectory() as workspace:
            directory = Path(workspace)
            result = verify_reference(directory / "missing", PROFILE_PATH, directory / "out")
            self.assertEqual(result["status"], "BLOCKED")
            expected = json.loads((directory / "out/registry_verification.json").read_bytes())
            self.assertEqual(expected, result)


if __name__ == "__main__":
    unittest.main()
