"""Web linkage transactions, using isolated synthetic publication-boundary fixtures."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from au_ree_v3 import publication, web_release, web_publication
from au_ree_v3.sources import ContentStore


def _encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _digest(payload):
    return sha256(payload).hexdigest()


class WebReleaseTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frontend = self.root / "frontend"
        for name in web_release.STATIC_FILES:
            path = self.frontend / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"test {name}".encode())
        self.release_root = self.root / "research"
        self.store = ContentStore(self.root / "primary")
        self.preview_data = self.root / "candidate-data"
        self.preview = self.root / "candidate-site"
        self.formal = self.root / "formal-site"
        self.profile, self.builds = self.root / "profile.json", {}
        self.profile.write_bytes(b"synthetic fixed profile")
        self.existing, self.first, self.second = [self._archive(revision) for revision in ("0", "1", "2")]
        self.verify_patch = patch.object(web_publication, "verify_web_release", side_effect=self._verify_exporter_fixture)
        self.export_patch = patch.object(web_publication, "export_web", side_effect=self._exporter_fixture)
        self.verify_patch.start()
        self.exporter = self.export_patch.start()
        self.addCleanup(self.verify_patch.stop)
        self.addCleanup(self.export_patch.stop)

    def _archive(self, revision, build=None):
        profile = self.store.put(self.profile.read_bytes())
        if build is None:
            build = self.root / f"build-{revision}"
            build.mkdir()
            (build / "build_receipt.json").write_bytes(f"synthetic receipt {revision}".encode())
            result = self.store.put(f"allowed data {revision}".encode())
            outputs = dict.fromkeys(("census/census.csv", "decision/mcda_inputs.json", "decision/mcda_results.json"), result)
        else:
            receipt = json.loads((build / "build_receipt.json").read_bytes())
            outputs = {name: self.store.put((build / name).read_bytes()) for name in receipt["scientific_artifacts"]}
            self.assertEqual(outputs, receipt["scientific_artifacts"])
        build_receipt = self.store.put((build / "build_receipt.json").read_bytes())
        manifest = {
            "schema": publication.RELEASE_SCHEMA, "cutoff": "2026-11-28",
            "build_receipt_sha256": build_receipt,
            "input_artifacts": {"profile": profile}, "output_hashes": outputs,
            "required_object_sha256": sorted({build_receipt, profile, *outputs.values()}),
            "build_gates": dict.fromkeys(("F0", "F1", "F2", "F3"), "PASS"),
        }
        payload = _encode(manifest)
        release_id = self.store.put(payload)
        path = self.release_root / "releases" / f"{release_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self.builds[release_id] = build
        return release_id

    def _published_fixture(self, release_id, previous, expected):
        """Use the real pointer/seal code with isolated simulated successful F5 prerequisites."""
        with patch.object(publication, "verify_replicas", return_value={"status": "PASS"}), patch.object(
            publication, "replay_release", return_value={"F4": "PASS"}
        ):
            result = publication.publish_release(
                release_root=self.release_root, release_id=release_id, primary=self.store, replica=self.store,
                replica_failure_domain=None, out=self.root / "research-receipts",
                previous_release_id=previous, expected_current=expected,
            )
        self.assertEqual(result["reason"], "FORMAL_POINTER_ADVANCED")

    def _exporter_fixture(self, *, profile, research_build, release_id, out, authorization):
        self.assertEqual(authorization.release_id, release_id)
        if (_digest(profile.read_bytes()) != authorization.profile_sha256
                or _digest((research_build / "build_receipt.json").read_bytes()) != authorization.build_receipt_sha256):
            raise ValueError("unbound original receipt")
        payload = self.store.read(authorization.output_hashes["census/census.csv"])
        self._fixture_release(
            out, release_id, "FORMAL_CURRENT", payload, authorization_sha256=authorization.authorization_sha256,
            research_build_receipt_sha256=authorization.build_receipt_sha256, profile_sha256=authorization.profile_sha256,
            census_sha256=authorization.output_hashes["census/census.csv"],
            research_mcda_inputs_sha256=authorization.output_hashes["decision/mcda_inputs.json"],
            research_mcda_results_sha256=authorization.output_hashes["decision/mcda_results.json"],
        )

    @staticmethod
    def _fixture_release(data, release_id, status, payload, **identity):
        directory = data / "releases" / release_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "census.csv").write_bytes(payload)
        manifest = {"release_id": release_id, "release_status": status, "cutoff": "2026-11-28", **identity,
                    "artifacts": {"census.csv": {"path": "census.csv", "sha256": _digest(payload)}}}
        (directory / "manifest.json").write_bytes(_encode(manifest))

    @staticmethod
    def _verify_exporter_fixture(directory, *, expected_release_id, expected_manifest_sha256):
        payload = (directory / "manifest.json").read_bytes()
        manifest = json.loads(payload)
        if _digest(payload) != expected_manifest_sha256 or manifest["release_id"] != expected_release_id:
            raise ValueError("fixed manifest fingerprint differs")
        if _digest((directory / "census.csv").read_bytes()) != manifest["artifacts"]["census.csv"]["sha256"]:
            raise ValueError("source CSV fingerprint differs")
        return manifest

    def _sync(self, release_id, expected=None, build=None):
        return web_release.sync_formal_site(
            frontend=self.frontend, site=self.formal, profile=self.profile,
            research_build=build or self.builds[release_id], release_root=self.release_root,
            primary=self.store, release_id=release_id, expected_current=expected,
        )

    def _preview(self, release_id):
        return web_release.build_preview_site(
            frontend=self.frontend, site=self.preview, data=self.preview_data, release_id=release_id,
        )

    def _snapshot(self, release_id, name):
        return web_release.snapshot_formal_site(
            site=self.formal, snapshot=self.root / name, frontend=self.frontend, release_root=self.release_root,
            primary=self.store, release_id=release_id,
        )

    def test_empty_formal_receives_no_candidate_pointer(self):
        result = web_release.build_empty_formal_site(frontend=self.frontend, site=self.formal)
        self.assertEqual(result["status"], "NO_FORMAL_RELEASE")
        self.assertFalse((self.formal / "data/latest.json").exists())
        self.assertIsNone(web_release.verify_site(site=self.formal, mode="formal")["current"])
        self._fixture_release(self.preview_data, self.first, "RESEARCH_CANDIDATE", b"candidate")
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "separate"):
            web_release.build_preview_site(frontend=self.frontend, site=self.formal, data=self.preview_data,
                                           release_id=self.first)

    def test_preview_two_versions_and_failed_switch_preserves_complete_previous(self):
        for release_id in (self.first, self.second):
            self._fixture_release(self.preview_data, release_id, "RESEARCH_CANDIDATE", release_id.encode())
        self._preview(self.first)
        before = web_release.verify_site(site=self.preview, mode="candidate")
        replace = web_release._replace

        def fail_pointer(path, payload):
            if path.name == "preview.json":
                raise OSError("simulated pointer write failure")
            replace(path, payload)

        with patch.object(web_release, "_replace", side_effect=fail_pointer):
            with self.assertRaisesRegex(OSError, "simulated"):
                self._preview(self.second)
        self.assertEqual(web_release.verify_site(site=self.preview, mode="candidate"), before)
        second = self._preview(self.second)
        self.assertEqual(second["previous_release_id"], self.first)
        self.assertEqual({item["release_id"] for item in second["releases"]}, {self.first, self.second})
        self.assertFalse((self.preview / "data/latest.json").exists())
        self.assertEqual((self.preview / "index.html").read_bytes(), (self.frontend / "index.html").read_bytes())

    def test_corrupt_new_artifact_fails_before_pointer_switch(self):
        self._fixture_release(self.preview_data, self.first, "RESEARCH_CANDIDATE", b"first")
        self._fixture_release(self.preview_data, self.second, "RESEARCH_CANDIDATE", b"second")
        self._preview(self.first)
        before = (self.preview / "data/preview.json").read_bytes()
        (self.preview_data / "releases" / self.second / "census.csv").write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "CSV fingerprint"):
            self._preview(self.second)
        self.assertEqual((self.preview / "data/preview.json").read_bytes(), before)

    def test_f5_blocked_research_cannot_issue_formal_latest(self):
        result = publication.publish_release(
            release_root=self.release_root, release_id=self.first, primary=self.store, replica=None,
            replica_failure_domain=None, out=self.root / "blocked", previous_release_id=self.existing,
            expected_current=None,
        )
        self.assertEqual(result["F5"], "BLOCKED")
        self.assertFalse((self.release_root / "authorizations").exists())
        with self.assertRaises(publication.PublicationNotAuthorized):
            self._sync(self.first)
        # Neither a forged pointer alone nor an unrelated receipt provides an F5 seal.
        (self.root / "blocked/publication_receipt.json").write_bytes(_encode({"F5": "PASS", "release_id": self.first}))
        (self.release_root / "current.json").write_bytes(_encode({"current": self.first, "previous": None}))
        with self.assertRaises(publication.PublicationNotAuthorized):
            self._sync(self.first)
        self.assertFalse(self.formal.exists())
        self.exporter.assert_not_called()

    def test_formal_linkage_two_versions_and_pointer_only_rollback(self):
        self._published_fixture(self.first, self.existing, None)
        self._sync(self.first)
        self._published_fixture(self.second, self.first, self.first)
        before = web_release.verify_site(site=self.formal, mode="formal")
        with self.assertRaisesRegex(ValueError, "unbound"):
            self._sync(self.second, self.first, self.builds[self.first])
        self.assertEqual(web_release.verify_site(site=self.formal, mode="formal"), before)
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "changed"):
            self._sync(self.second)
        updated = self._sync(self.second, self.first)
        self.assertEqual(updated, self._sync(self.second, self.first))  # failed deployment retry is idempotent
        snapshot = self._snapshot(self.second, "deployment")
        self.assertEqual(snapshot["current"]["release_id"], self.second)
        self.assertNotIn("data/preview.json", snapshot["files"])
        with self.assertRaises(publication.PublicationNotAuthorized):
            web_release.rollback_formal_site(
                site=self.formal, release_root=self.release_root, primary=self.store,
                release_id=self.first, expected_current=self.second,
            )
        publication.rollback_release(release_root=self.release_root, expected_current=self.second, storage=self.store)
        pointer = web_release.rollback_formal_site(
            site=self.formal, release_root=self.release_root, primary=self.store,
            release_id=self.first, expected_current=self.second,
        )
        self.assertEqual((pointer["release_id"], pointer["previous_release_id"]), (self.first, self.second))
        after = self._snapshot(self.first, "rolled-back-deployment")
        changed = {name for name, fingerprint in snapshot["files"].items() if after["files"][name] != fingerprint}
        self.assertEqual(changed, {"data/latest.json"})

    def test_forged_consistent_public_fingerprints_fail_trusted_crosscheck(self):
        self._published_fixture(self.first, self.existing, None)
        self._sync(self.first)
        data = self.formal / "data"
        self._fixture_release(data, self.first, "FORMAL_CURRENT", b"modified behind the website pointer")
        pointer_path = data / "latest.json"
        pointer = json.loads(pointer_path.read_bytes())
        replacement = _digest((data / "releases" / self.first / "manifest.json").read_bytes())
        pointer["manifest_sha256"] = replacement
        pointer["releases"][0]["manifest_sha256"] = replacement
        pointer_path.write_bytes(_encode(pointer))
        self.assertEqual(web_release.verify_site(site=self.formal, mode="formal")["status"], "PASS")
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "authorized research publication"):
            self._snapshot(self.first, "untrusted-deployment")
        self.assertFalse((self.root / "untrusted-deployment").exists())

    def test_formal_stage_refuses_manifest_mismatched_to_authorized_inputs(self):
        self._published_fixture(self.first, self.existing, None)

        def wrong_profile(**args):
            self._exporter_fixture(**args)
            path = args["out"] / "releases" / args["release_id"] / "manifest.json"
            manifest = json.loads(path.read_bytes())
            manifest["profile_sha256"] = "a" * 64
            path.write_bytes(_encode(manifest))

        self.exporter.side_effect = wrong_profile
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "authorized research artifact hashes"):
            self._sync(self.first)
        self.assertIsNone(web_release.verify_site(site=self.formal, mode="formal")["current"])

    def test_synthetic_authorized_archive_uses_real_exporter_and_verifier(self):
        from test_web_publication import PROFILE, _minimal_build

        self.export_patch.stop()
        self.verify_patch.stop()
        self.profile = PROFILE
        build = self.root / "real-export-synthetic-build"
        _minimal_build(build)
        release_id = self._archive("fixture", build)
        self._published_fixture(release_id, self.existing, None)
        pointer = self._sync(release_id)
        self.assertEqual(pointer["release_status"], "FORMAL_CURRENT")
        snapshot = self._snapshot(release_id, "real-export-synthetic-snapshot")
        artifact = snapshot["current"]["manifest_path"]
        manifest = json.loads((self.formal / "data" / artifact).read_bytes())
        self.assertEqual(manifest["census_sha256"], _digest((build / "census/census.csv").read_bytes()))
        self.assertEqual(manifest["project_count"], 93)
        self.assertFalse((self.formal / "data" / "preview.json").exists())

    def test_upload_tree_rejects_undeclared_private_file(self):
        web_release.build_empty_formal_site(frontend=self.frontend, site=self.formal)
        (self.formal / "private.db").write_bytes(b"private")
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "undeclared"):
            web_release.verify_site(site=self.formal, mode="formal")

    def test_static_assets_allow_legacy_site_and_require_imported_display_module(self):
        module = self.frontend / "assets/display.mjs"
        module.unlink()
        web_release.build_empty_formal_site(frontend=self.frontend, site=self.formal)
        result = web_release.verify_site(site=self.formal, mode="formal")
        self.assertNotIn("assets/display.mjs", result["files"])
        (self.formal / "assets/app.js").write_text('import "./display.mjs";', encoding="utf-8")
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "undeclared or missing files"):
            web_release.verify_site(site=self.formal, mode="formal")

    def test_formal_upload_refuses_tampered_frontend(self):
        self._published_fixture(self.first, self.existing, None)
        self._sync(self.first)
        (self.formal / "assets/app.js").write_bytes(b"changed after publication")
        with self.assertRaisesRegex(web_release.WebsiteReleaseError, "approved frontend"):
            self._snapshot(self.first, "tampered-frontend")
