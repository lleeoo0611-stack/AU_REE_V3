"""Small locked fixtures exercise the actual archive, cold rebuild and pointer paths."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from hashlib import sha256
from pathlib import Path

from au_ree_v3.publication import (
    archive_release, load_release, publish_release, rehearse_rollback, replay_release, verify_replicas,
)
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_environment import environment_lock
from au_ree_v3.sources import ContentStore, _projects


BUILDER = r'''
import json
from hashlib import sha256

def build_research(*, profile, bindings, sources, assets, out, code_commit):
    settings = json.loads(profile.read_bytes())
    source = json.loads(sources.read_bytes())["editions"][0]
    original = (assets / "objects" / source["sha256"]).read_bytes()
    proof = (assets / "objects" / source["receipt_sha256"]).read_bytes()
    selector = json.loads(bindings.read_bytes())["selection"]
    registry_reference = settings["inputs"]["project_registry"]
    registry = (profile.parent / registry_reference["path"]).read_bytes()
    output = {
        "census/census.csv": b"source,value\n" + original,
        "census/cell_status.csv": b"state,source\nPRESENT," + sha256(original).hexdigest().encode() + b"\n",
        "decision/mcda_inputs.json": json.dumps({"status": "INSUFFICIENT_COHORT"}).encode(),
    }
    result = {
        "code_commit": code_commit, "environment": ENVIRONMENT,
        "input_artifacts": {name: sha256(path.read_bytes()).hexdigest()
                            for name, path in {"profile": profile, "bindings": bindings, "sources": sources}.items()},
        "scientific_artifacts": {name: sha256(data).hexdigest() for name, data in output.items()},
        "scientific_output_count": 1, "project_registry_hash": sha256(registry).hexdigest(),
        "evidence_policy_version": sha256(proof).hexdigest(), "parser_version": "synthetic",
        "database_schema": 2, "selection_rules_hash": sha256(selector.encode()).hexdigest(),
        "mcda_inputs_hash": sha256(output["decision/mcda_inputs.json"]).hexdigest(),
        "gates": dict.fromkeys(("F0", "F1", "F2", "F3"), "PASS"),
    }
    out.mkdir(parents=True, exist_ok=True)
    for name, data in output.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (out / "build_receipt.json").write_text(json.dumps(result), encoding="utf8")
    return result
'''


def _git(repository, *args):
    return subprocess.run(["git", "-C", str(repository), *args], check=True, capture_output=True, text=True).stdout.strip()


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repository = self.root / "v3"
        package = self.repository / "src" / "au_ree_v3"
        package.mkdir(parents=True)
        self.builder = f"ENVIRONMENT = {environment_lock()!r}\n" + BUILDER
        (package / "__init__.py").touch()
        (package / "fresh_research.py").write_text(self.builder, encoding="utf8")
        _git(self.repository, "init", "--quiet")
        _git(self.repository, "add", ".")
        _git(self.repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "Fixture")
        self.code_commit = _git(self.repository, "rev-parse", "HEAD")
        self.release_root = self.root / "releases"
        self.primary = ContentStore(self.root / "primary")
        self.replica = ContentStore(self.root / "copy")
        self.original = self.primary.put(b"synthetic,1.250\n")
        self.proof = self.primary.put(b"synthetic proof receipt")

    def _package(self, revision="1", replica=None):
        inputs = self.root / f"inputs-{revision}"
        inputs.mkdir(exist_ok=True)
        registry = inputs / "registry.json"
        registry.write_bytes(b"{\"synthetic\": true}")
        profile = inputs / "profile.json"
        profile.write_text(json.dumps({
            "research_cutoff": "2026-11-28", "timezone": "Asia/Taipei",
            "inputs": {"project_registry": {"path": registry.name, "sha256": sha256(registry.read_bytes()).hexdigest()}},
        }), encoding="utf8")
        bindings = inputs / "bindings.json"
        bindings.write_text(json.dumps({"selection": revision}), encoding="utf8")
        sources = inputs / "sources.json"
        sources.write_text(json.dumps({
            "cutoff": "2026-11-28", "timezone": "Asia/Taipei",
            "editions": [{
                "sha256": self.original, "receipt_sha256": self.proof,
                "temporal_evidence": [{"reference_sha256": self.proof}],
            }],
        }), encoding="utf8")
        build = self.root / f"build-{revision}"
        namespace = {}
        exec(compile(self.builder, "synthetic-pinned-builder", "exec"), namespace)
        namespace["build_research"](
            profile=profile, bindings=bindings, sources=sources, assets=self.primary.root,
            out=build, code_commit=self.code_commit,
        )
        return archive_release(
            repository=self.repository, release_root=self.release_root, profile=profile, bindings=bindings,
            sources=sources, build=build, primary=self.primary, replica=replica,
        )

    def test_recover_from_replica_without_primary_or_index(self):
        archived = self._package(replica=self.replica)
        release_id = archived["release_id"]
        self.assertEqual(archived["F5"], "BLOCKED")
        self.assertEqual(archived["replica_inventory"]["reason"], "REPLICA_INDEPENDENCE_UNVERIFIED")
        self.assertEqual(archived["replica_inventory"]["primary_verified"], archived["replica_inventory"]["replica_verified"])
        expected = load_release(self.release_root, release_id)
        replay = replay_release(
            release_root=self.root / "absence-of-original-index", release_id=release_id,
            storage=self.replica, out=self.root / "restored",
        )
        self.assertEqual(replay["F4"], "PASS", replay)
        self.assertEqual(replay["actual"], expected["output_hashes"])
        self.assertEqual((self.root / "restored/rebuilt/census/census.csv").read_bytes(), b"source,value\nsynthetic,1.250\n")

    def test_corrupted_second_copy_blocks_release_and_cold_rebuild(self):
        release_id = self._package(replica=self.replica)["release_id"]
        self.replica.path(self.original).write_bytes(b"tampered fixture copy")
        manifest = load_release(self.release_root, release_id)
        inventory = verify_replicas(set(manifest["required_object_sha256"]), self.primary, self.replica)
        self.assertEqual(inventory["status"], "FAIL")
        self.assertEqual(inventory["reason"], "REPLICA_DIGEST_MISMATCH")
        replay = replay_release(
            release_root=self.release_root, release_id=release_id, storage=self.replica, out=self.root / "corrupt",
        )
        self.assertEqual(replay["F4"], "FAIL", replay)
        self.assertFalse((self.root / "corrupt/rebuilt").exists())

    def test_revision_and_pointer_only_rollback_are_immutable(self):
        first = self._package()["release_id"]
        self.assertEqual(first, self._package()["release_id"])
        second = self._package(revision="2")["release_id"]
        self.assertNotEqual(first, second)
        first_manifest = load_release(self.release_root, first)
        second_manifest = load_release(self.release_root, second)
        rehearsal = rehearse_rollback(
            release_root=self.release_root, previous_release_id=first, release_id=second,
            storage=self.primary, out=self.root / "rollback",
        )
        self.assertEqual(rehearsal["before"]["current"], second)
        self.assertEqual(rehearsal["after"]["current"], first)
        self.assertEqual(load_release(self.release_root, first), first_manifest)
        self.assertEqual(load_release(self.release_root, second), second_manifest)
        blocked = publish_release(
            release_root=self.release_root, release_id=second, primary=self.primary, replica=None,
            replica_failure_domain=None, previous_release_id=first, expected_current=None, out=self.root / "blocked",
        )
        self.assertEqual(blocked["F5"], "BLOCKED")
        self.assertFalse((self.release_root / "current.json").exists())


@unittest.skipUnless(
    os.environ.get("AU_REE_FRESH_ASSETS") and shutil.which("pdftotext"),
    "locked original issuer PDFs and Poppler required",
)
class NativeReleaseIntegrationTests(unittest.TestCase):
    def test_exact_issuer_pdfs_rebuild_from_archive_without_primary(self):
        from au_ree_v3.fresh_research import build_research

        repository = Path(__file__).resolve().parents[1]
        settings = repository / "config" / "research_profiles"
        profile = settings / "au_ree_2026-11-28.json"
        bindings = settings / "resource_bindings_v1.json"
        fixture = repository / "tests" / "fixtures" / "fresh_resource_v1"
        assets = Path(os.environ["AU_REE_FRESH_ASSETS"])
        commit = os.environ.get("AU_REE_RELEASE_CODE_COMMIT") or _git(repository, "rev-parse", "HEAD")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary, replica = (ContentStore(root / name) for name in ("primary", "local-replica"))
            source_manifest = json.loads((fixture / "acquisition_manifest.json").read_bytes())
            for proof in (fixture / "captures").glob("*.json"):
                self.assertEqual(primary.put(proof.read_bytes()), proof.stem)
            events = []
            for edition in source_manifest["editions"]:
                source_sha, receipt_sha = edition["sha256"], edition["receipt_sha256"]
                locations = [assets / folder / source_sha for folder in ("objects", "assets", "")]
                source = next((item for item in locations if item.is_file()), None)
                self.assertIsNotNone(source, f"missing locked source edition {source_sha}")
                self.assertEqual(primary.put(source.read_bytes()), source_sha)
                events.append({**json.loads(primary.read(receipt_sha)), "receipt_sha256": receipt_sha})
            projects = load_registry(load_profile(profile).registry_path).projects
            by_capture = {item["receipt_sha256"]: item for item in source_manifest["editions"]}
            source_manifest["events"] = events
            source_manifest["projects"] = _projects(tuple(item.project_id for item in projects), events, by_capture)
            manifest = root / "source_manifest.json"
            manifest.write_text(json.dumps(source_manifest), encoding="utf8")
            build = root / "initial-build"
            initial = build_research(
                profile=profile, bindings=bindings, sources=manifest, assets=primary.root, out=build, code_commit=commit,
            )
            self.assertEqual(initial["gates"], {"F0": "PASS", "F1": "BLOCKED", "F2": "PASS", "F3": "PASS"})
            self.assertEqual(initial["scientific_output_count"], 6)
            release = archive_release(
                repository=repository, release_root=root / "archive", profile=profile, bindings=bindings,
                sources=manifest, build=build, primary=primary, replica=replica,
            )
            self.assertEqual(release["F5"], "BLOCKED")
            replay = replay_release(
                release_root=root / "archive-index-unavailable", release_id=release["release_id"],
                storage=replica, out=root / "restored",
            )
            self.assertEqual(replay["F4"], "PASS", replay)
            self.assertEqual(replay["actual"], initial["scientific_artifacts"])
            self.assertEqual(replay["scientific_output_count"], 6)


if __name__ == "__main__":
    unittest.main()
