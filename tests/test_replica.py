"""Exercise the AWS command boundary without an account or outbound network."""

import base64
import hashlib
import json
from pathlib import Path
from subprocess import CompletedProcess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from au_ree_v3.contracts import canonical_json
from au_ree_v3.publication import RELEASE_SCHEMA, restore_release_replica, sync_release_replica, verify_replicas
from au_ree_v3.replica import ReplicaUnavailable, S3Replica
from au_ree_v3.sources import ContentStore


class AWSFixture:
    """Model the authenticated API output, including conditional failure and bad bytes."""

    owner = "123456789012"
    caller = "210987654321"
    region = "ap-southeast-2"

    def __init__(self):
        self.objects = {}
        self.commands = []

    def __call__(self, args, *, env, **_options):
        self.commands.append(args)
        assert env["AWS_IGNORE_CONFIGURED_ENDPOINT_URLS"] == "true"
        assert "--no-verify-ssl" not in args and "--no-sign-request" not in args
        if "sts" in args:
            assert args[args.index("--endpoint-url") + 1] == "https://sts.ap-southeast-2.amazonaws.com"
            response = {"Account": self.caller, "Arn": f"arn:aws:sts::{self.caller}:assumed-role/Fixture/Reader"}
            return CompletedProcess(args, 0, json.dumps(response).encode(), b"")
        operation = args[args.index("s3api") + 1]
        assert args[args.index("--endpoint-url") + 1] == "https://s3.ap-southeast-2.amazonaws.com"
        owner = args[args.index("--expected-bucket-owner") + 1]
        if owner != self.owner:
            return self._error(args, "AccessDenied", operation)
        if operation in {"head-bucket", "get-bucket-location"}:
            return CompletedProcess(args, 0, json.dumps({"LocationConstraint": self.region}).encode(), b"")
        key = args[args.index("--key") + 1]
        if operation == "get-object":
            if key not in self.objects:
                return self._error(args, "NoSuchKey", operation)
            payload = self.objects[key]
            Path(args[-1]).write_bytes(payload)
            return CompletedProcess(args, 0, json.dumps({"ContentLength": len(payload)}).encode(), b"")
        assert operation == "put-object"
        assert args[args.index("--if-none-match") + 1] == "*"
        payload = Path(args[args.index("--body") + 1]).read_bytes()
        checksum = base64.b64encode(hashlib.sha256(payload).digest()).decode()
        assert args[args.index("--checksum-sha256") + 1] == checksum
        if key in self.objects:
            return self._error(args, "PreconditionFailed", operation)
        self.objects[key] = payload
        return CompletedProcess(args, 0, b"{}", b"")

    @staticmethod
    def _error(args, code, operation):
        return CompletedProcess(
            args, 254, b"", f"An error occurred ({code}) when calling {operation}: private service diagnostic".encode(),
        )


class S3BackupTests(unittest.TestCase):
    def setUp(self):
        self.client = AWSFixture()
        which = patch("au_ree_v3.replica.shutil.which", return_value="/example/aws")
        call = patch("au_ree_v3.replica.subprocess.run", side_effect=self.client)
        which.start()
        call.start()
        self.addCleanup(which.stop)
        self.addCleanup(call.stop)
        root = TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.store = ContentStore(self.root / "primary")
        self.replica = S3Replica(
            bucket="research-replica", region=AWSFixture.region, expected_bucket_owner=AWSFixture.owner,
            prefix="v3/frozen", profile="research-backup",
        )

    def test_verified_backup_restore_and_no_release_mutation(self):
        members = [self.store.put(b"original bytes"), self.store.put(b"capture evidence")]
        manifest = (canonical_json({"schema": RELEASE_SCHEMA, "required_object_sha256": members}) + "\n").encode()
        release_id = self.store.put(manifest)
        result = sync_release_replica(
            release_root=self.root / "unavailable-index", release_id=release_id, primary=self.store,
            replica=self.replica, out=self.root / "backup",
        )
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["F5"], "NOT_RUN")
        self.assertEqual(result["replica_inventory"]["replica_verified"], 3)
        self.assertEqual(result["replica_inventory"]["replica_attestation"]["provider"], "aws-s3")
        self.assertEqual(self.store.read(release_id), self.replica.read(release_id))
        destination = ContentStore(self.root / "restored")
        restored = restore_release_replica(
            release_root=self.root / "unavailable-index", release_id=release_id, replica=self.replica,
            destination=destination, out=self.root / "restoration",
        )
        self.assertEqual(restored["status"], "PASS")
        self.assertEqual([destination.read(sha) for sha in (*members, release_id)],
                         [self.store.read(sha) for sha in (*members, release_id)])
        self.assertFalse((self.root / "current.json").exists())

    def test_corrupt_object_cannot_be_replaced_or_confirmed(self):
        payload = b"an immutable source PDF"
        sha = self.store.put(payload)
        self.assertEqual(self.replica.put(payload), sha)
        self.assertEqual(self.replica.put(payload), sha)  # Existing exact object is idempotent.
        self.client.objects[self.replica._key(sha)] = b"invalid bytes"
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.replica.put(payload)
        self.assertEqual(self.client.objects[self.replica._key(sha)], b"invalid bytes")
        inventory = verify_replicas({sha}, self.store, self.replica)
        self.assertEqual(inventory["status"], "FAIL")
        self.assertEqual(inventory["reason"], "REPLICA_DIGEST_MISMATCH")

    def test_missing_cli_and_wrong_bucket_owner_are_blocked_without_copy(self):
        sha = self.store.put(b"raw bytes")
        wrong_owner = S3Replica(
            bucket=self.replica.bucket, region=self.replica.region, expected_bucket_owner="000000000000",
        )
        with self.assertRaisesRegex(ReplicaUnavailable, "AccessDenied") as error:
            wrong_owner.attest_remote()
        self.assertNotIn("private service diagnostic", str(error.exception))
        mismatched = verify_replicas({sha}, self.store, wrong_owner)
        with patch("au_ree_v3.replica.shutil.which", return_value=None):
            unavailable = verify_replicas({sha}, self.store, self.replica)
        self.assertEqual(unavailable["reason"], "REPLICA_ATTESTATION_UNAVAILABLE")
        self.assertEqual(mismatched["status"], "BLOCKED")
        self.assertFalse(any("put-object" in cmd or "get-object" in cmd for cmd in self.client.commands))

    def test_local_labels_and_untyped_network_mount_cannot_assert_independence(self):
        payload = b"original"
        sha = self.store.put(payload)
        second_local = ContentStore(self.root / "second-local")
        second_local.put(payload)
        result = verify_replicas({sha}, self.store, second_local, replica_failure_domain="claimed-remote")
        self.assertEqual(result["status"], "BLOCKED")
        self.assertEqual(result["replica_verified"], 1)
        self.replica.put(payload)
        with patch("au_ree_v3.publication._local_filesystem_type", return_value="fuse.s3fs"):
            result = verify_replicas({sha}, self.store, self.replica)
        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["distinct_failure_domain"])

    def test_configuration_accepts_only_standard_credential_route(self):
        path = self.root / "replica.json"
        path.write_text(json.dumps({
            "schema_version": "s3-research-replica-v1", "bucket": self.replica.bucket, "region": self.replica.region,
            "expected_bucket_owner": self.replica.expected_bucket_owner, "endpoint_url": "http://localhost:9999",
        }))
        with self.assertRaisesRegex(ValueError, "unknown field"):
            S3Replica.from_file(path)


if __name__ == "__main__":
    unittest.main()
