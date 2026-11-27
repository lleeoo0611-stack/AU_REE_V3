"""Verify the O4 offline process boundary and deterministic output contract."""

import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import unittest

TOOL = Path(__file__).resolve().parents[1] / "tools" / "verify_semantic_release.py"
SPEC = importlib.util.spec_from_file_location("verify_semantic_release", TOOL)
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def _tar_resource_module(*, prohibited=None):
    examples = {
        None: "assert profile.read_text() == 'pinned input'",
        "socket": "import socket; socket.socket()",
        "process": "import subprocess; subprocess.run(['true'], check=True)",
    }
    source_code = rf'''
import sqlite3

def run_resource_ablation(profile, baseline, sources, assets, out, *, reread_bindings=None):
    {examples[prohibited]}
    out.mkdir()
    for name in {release.ARTIFACTS["resource"]!r}:
        (out / name).write_text('{{"status": "PASS"}}\n')
    with sqlite3.connect(out / "semantic_research.sqlite") as database:
        database.execute("CREATE TABLE observations (id TEXT)")
        database.execute("INSERT INTO observations VALUES ('shadow')")
    return {{"status": "PASS"}}
'''
    technical_code = rf'''
import sqlite3

def run_technical_pilot(profile, sources, assets, bindings, out, *, oracle=None):
    out.mkdir()
    for name in {release.ARTIFACTS["technical"]!r}:
        (out / name).write_text('{{"status": "PASS", "elapsed_seconds": 123}}\n')
    with sqlite3.connect(out / "technical_shadow.sqlite") as database:
        database.execute("CREATE TABLE observations (id TEXT)")
    return {{"status": "PASS"}}
'''
    members = {
        "src/au_ree_v3/__init__.py": "",
        "src/au_ree_v3/contracts.py": "import json\ndef canonical_json(value): return json.dumps(value, sort_keys=True)",
        "src/au_ree_v3/semantic_research.py": source_code,
        "src/au_ree_v3/technical_processing.py": technical_code,
    }
    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w") as archive:
        for path, text in members.items():
            raw = text.encode("utf8")
            info = tarfile.TarInfo(path)
            info.size = len(raw)
            archive.addfile(info, io.BytesIO(raw))
    return content.getvalue()


class SemanticReplayToolTests(unittest.TestCase):
    def test_sealed_replay_needs_no_live_input_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            store = release.ContentStore(root / "shadow")
            profile = root / "discarded-profile.json"
            profile.write_text("pinned input")
            code = _tar_resource_module()
            reference = root / "build"
            reference.mkdir()
            artifacts, _ = release._run_detached(
                code_archive=code, temporary=reference,
                inputs={key: profile for key in ("profile", "sources", "assets", "baseline", "technical-bindings")},
                out=root / "prior-run",
            )
            profile_sha = store.put(profile.read_bytes())
            profile.unlink()
            metadata = store.put(b"test-only prior baseline metadata")
            formal = {
                "output_hashes": {}, "build_receipt_sha256": metadata,
                "cutoff": "2026-11-28", "timezone": "Asia/Taipei",
            }
            formal_id = store.put(release._encode(formal))
            manifest = {
                "schema": release.SCHEMA, "mode": "SHADOW",
                "code_archive_sha256": store.put(code), "verification_program_sha256": store.put(TOOL.read_bytes()),
                "environment_sha256": store.put(release._environment_bytes()), "formal_release_id": formal_id,
                "formal_build_receipt_sha256": metadata, "formal_output_hashes": formal["output_hashes"],
                "cutoff": formal["cutoff"], "timezone": formal["timezone"],
                "source_object_sha256": [], "profile_artifacts": {}, "policy_artifacts": {},
                "input_artifacts": {
                    "profile": profile_sha, "sources": profile_sha, "technical-bindings": profile_sha,
                    "baseline-sqlite": metadata,
                },
                "output_hashes": {name: store.put(data) for name, data in artifacts.items()},
            }
            identity = store.put(release._encode(manifest))
            receipt = release.replay_shadow(store=store.root, identity=identity, out=root / "cold-replay")
            self.assertEqual(receipt["O4"], "PASS", receipt)
            self.assertEqual(receipt["artifact_count"], len(artifacts))
            self.assertFalse(receipt["differences"])

    def test_sealed_shadow_readback_rejects_corrupt_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            store = release.ContentStore(Path(temporary) / "shadow")
            metadata = store.put(b"checked bytes")
            output = store.put(b"observed value")
            formal = {
                "output_hashes": {"census/census.csv": metadata}, "build_receipt_sha256": metadata,
                "cutoff": "2026-11-28", "timezone": "Asia/Taipei",
            }
            formal_id = store.put(release._encode(formal))
            shadow = {
                "schema": release.SCHEMA, "mode": "SHADOW",
                "code_archive_sha256": metadata, "verification_program_sha256": metadata,
                "environment_sha256": metadata, "formal_release_id": formal_id,
                "formal_build_receipt_sha256": metadata, "formal_output_hashes": formal["output_hashes"],
                "cutoff": formal["cutoff"], "timezone": formal["timezone"],
                "source_object_sha256": [metadata], "profile_artifacts": {}, "policy_artifacts": {},
                "input_artifacts": {}, "output_hashes": {"measurement.json": output},
            }
            identity = store.put(release._encode(shadow))
            self.assertEqual(release.verify_shadow_archive(store, identity)["exact_objects"], 4)
            store.path(output).write_bytes(b"corrupted run fixture")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                release.verify_shadow_archive(store, identity)

    def test_technical_timing_does_not_change_scientific_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "technical_pilot_receipt.json"
            receipt.write_text(json.dumps({"status": "PASS", "elapsed_seconds": 123, "observed": 5}))
            first = release._science_file(receipt)
            receipt.write_text(json.dumps({"status": "PASS", "elapsed_seconds": 456, "observed": 5}))
            self.assertEqual(first, release._science_file(receipt))
            receipt.write_text(json.dumps({"status": "PASS", "elapsed_seconds": 456, "observed": 6}))
            self.assertNotEqual(first, release._science_file(receipt))

    def test_sqlite_fingerprint_uses_logical_sets_and_detects_changed_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            first, second = (Path(temporary) / name for name in ("a.sqlite", "b.sqlite"))
            for path, data in ((first, ["0", "1"]), (second, ["1", "0"])):
                with sqlite3.connect(path) as database:
                    database.execute("CREATE TABLE observations (value TEXT)")
                    database.executemany("INSERT INTO observations VALUES (?)", [(value,) for value in data])
            self.assertEqual(release._sqlite_digest(first), release._sqlite_digest(second))
            with sqlite3.connect(second) as database:
                database.execute("UPDATE observations SET value = NULL WHERE value = '0'")
            self.assertNotEqual(release._sqlite_digest(first), release._sqlite_digest(second))

    def test_pinned_code_runs_in_clean_subprocess_with_socket_rejection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profile = root / "profile.json"
            profile.write_text("pinned input")
            inputs = {key: profile for key in ("profile", "sources", "assets", "baseline", "technical-bindings")}
            first, second, network = (root / name for name in ("first", "second", "network"))
            for path in (first, second, network):
                path.mkdir()
            code = _tar_resource_module()
            before, receipt = release._run_detached(
                code_archive=code, temporary=first, inputs=inputs, out=root / "run-a",
            )
            after, _ = release._run_detached(
                code_archive=code, temporary=second, inputs=inputs, out=root / "run-b",
            )
            self.assertEqual(receipt["statuses"], {"resource": "PASS", "technical": "PASS"})
            self.assertEqual(before, after)
            self.assertEqual(len(before), 14)
            with self.assertRaisesRegex(release.ArchiveError, "network access is disabled"):
                release._run_detached(
                    code_archive=_tar_resource_module(prohibited="socket"), temporary=network,
                    inputs=inputs, out=root / "run-net",
                )
            process = root / "process"
            process.mkdir()
            with self.assertRaisesRegex(release.ArchiveError, "non-document command is disabled"):
                release._run_detached(
                    code_archive=_tar_resource_module(prohibited="process"), temporary=process,
                    inputs=inputs, out=root / "run-process",
                )


if __name__ == "__main__":
    unittest.main()
