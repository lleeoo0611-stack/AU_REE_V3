"""Authenticated remote content replicas for immutable research archives."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .contracts import canonical_json

S3_REPLICA_SCHEMA = "s3-research-replica-v1"
_CONTENT_SHA = re.compile(r"[0-9a-f]{64}")
_ACCOUNT = re.compile(r"[0-9]{12}")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_REGION = re.compile(r"(?!cn-|us-iso|us-gov)[a-z]{2}(?:-[a-z0-9]+)+-[0-9]+")
_PREFIX_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")
_CLI_PROFILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_@.+-]{0,127}")
_AWS_ERROR = re.compile(r"An error occurred \(([A-Za-z0-9_.-]+)\)")


class ContentReplica(Protocol):
    def read(self, sha256: str) -> bytes: ...
    def put(self, payload: bytes) -> str: ...


@dataclass(frozen=True)
class RemoteAttestation:
    """Authenticated provider and storage namespace measured during inventory."""

    provider: str
    endpoint: str
    failure_domain_sha256: str
    bucket_owner_sha256: str
    principal_account_sha256: str
    region: str


@runtime_checkable
class AuthenticatedRemoteReplica(ContentReplica, Protocol):
    def attest_remote(self) -> RemoteAttestation: ...


class ReplicaUnavailable(OSError):
    """Replica configuration, client version, authorization, or network access is unavailable."""


class _S3RequestError(ReplicaUnavailable):
    def __init__(self, operation: str, code: str):
        self.code = code
        super().__init__(f"remote replica request {operation} failed ({code})")


@dataclass(frozen=True)
class S3Replica:
    """Use normal AWS CLI credentials and AWS-owned HTTPS endpoints for a CAS."""

    bucket: str
    region: str
    expected_bucket_owner: str
    prefix: str = "au-ree-v3"
    profile: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.bucket, str) or not _BUCKET.fullmatch(self.bucket):
            raise ValueError("S3 replica requires a lowercase, nondotted general-purpose bucket name")
        if not isinstance(self.region, str) or not _REGION.fullmatch(self.region):
            raise ValueError("S3 replica requires an AWS commercial Region")
        if not isinstance(self.expected_bucket_owner, str) or not _ACCOUNT.fullmatch(self.expected_bucket_owner):
            raise ValueError("S3 replica requires the expected 12-digit bucket-owner account")
        if not isinstance(self.prefix, str) or self.prefix and any(
            not _PREFIX_COMPONENT.fullmatch(part) or part in (".", "..") for part in self.prefix.split("/")
        ):
            raise ValueError("S3 replica prefix must contain safe slash-separated object-key components")
        if self.profile is not None and (not isinstance(self.profile, str) or not _CLI_PROFILE.fullmatch(self.profile)):
            raise ValueError("S3 replica AWS CLI profile name is invalid")

    @classmethod
    def from_file(cls, path: Path) -> S3Replica:
        config = json.loads(path.read_bytes())
        if not isinstance(config, dict) or config.pop("schema_version", None) != S3_REPLICA_SCHEMA:
            raise ValueError(f"S3 replica configuration requires {S3_REPLICA_SCHEMA}")
        try:
            return cls(**config)
        except TypeError as error:
            raise ValueError("S3 replica configuration contains an unknown field or lacks a required field") from error

    def _endpoint(self, service: str) -> str:
        return f"https://{service}.{self.region}.amazonaws.com"

    def _key(self, sha256: str) -> str:
        if not isinstance(sha256, str) or not _CONTENT_SHA.fullmatch(sha256):
            raise ValueError("remote replica object key must be a full SHA-256")
        return "/".join(part for part in (self.prefix, "objects", sha256) if part)

    def _call(self, service: str, operation: str, *arguments: str) -> dict:
        client = shutil.which("aws")
        if client is None:
            raise ReplicaUnavailable("AWS CLI v2 is unavailable")
        host = "s3" if service == "s3api" else "sts"
        command = [
            client, "--region", self.region, "--endpoint-url", self._endpoint(host),
            "--output", "json", "--no-cli-pager", "--no-cli-auto-prompt",
            "--cli-connect-timeout", "10", "--cli-read-timeout", "60",
        ]
        if self.profile:
            command.extend(("--profile", self.profile))
        command.extend((service, operation, *arguments))
        environment = {
            **os.environ, "AWS_IGNORE_CONFIGURED_ENDPOINT_URLS": "true", "AWS_PAGER": "",
            "AWS_RETRY_MODE": "standard", "AWS_MAX_ATTEMPTS": "3",
        }
        try:
            result = subprocess.run(command, capture_output=True, env=environment, timeout=240, check=False)
        except (subprocess.TimeoutExpired, OSError) as error:
            raise ReplicaUnavailable(f"remote replica request {operation} timed out or could not start") from error
        if result.returncode:
            match = _AWS_ERROR.search(result.stderr.decode("utf-8", "replace"))
            raise _S3RequestError(operation, match[1] if match else "AWS_CLI_REQUEST_FAILED")
        try:
            response = json.loads(result.stdout) if result.stdout.strip() else {}
        except (ValueError, UnicodeError):
            raise ReplicaUnavailable(f"remote replica request {operation} returned invalid response metadata") from None
        if not isinstance(response, dict):
            raise ReplicaUnavailable(f"remote replica request {operation} returned invalid response metadata")
        return response

    def _bucket_arguments(self) -> tuple[str, ...]:
        return ("--bucket", self.bucket, "--expected-bucket-owner", self.expected_bucket_owner)

    def attest_remote(self) -> RemoteAttestation:
        identity = self._call("sts", "get-caller-identity")
        account = identity.get("Account")
        if not isinstance(account, str) or not _ACCOUNT.fullmatch(account) or not identity.get("Arn"):
            raise ReplicaUnavailable("AWS STS did not verify a caller account")
        self._call("s3api", "head-bucket", *self._bucket_arguments())
        location = self._call("s3api", "get-bucket-location", *self._bucket_arguments())
        if "LocationConstraint" not in location:
            raise ReplicaUnavailable("AWS S3 did not return the bucket Region")
        region = location["LocationConstraint"]
        region = "us-east-1" if region is None else "eu-west-1" if region == "EU" else region
        if region != self.region:
            raise ReplicaUnavailable("authenticated bucket Region differs from the configured AWS Region")
        domain = hashlib.sha256(
            canonical_json(["aws-s3", self.expected_bucket_owner, self.bucket, region]).encode()
        ).hexdigest()
        return RemoteAttestation(
            provider="aws-s3", endpoint=self._endpoint("s3"), failure_domain_sha256=domain,
            bucket_owner_sha256=hashlib.sha256(self.expected_bucket_owner.encode()).hexdigest(),
            principal_account_sha256=hashlib.sha256(account.encode()).hexdigest(), region=region,
        )

    def read(self, sha256: str) -> bytes:
        key = self._key(sha256)
        with tempfile.TemporaryDirectory(prefix="research-s3-get-") as directory:
            target = Path(directory) / "object"
            try:
                response = self._call(
                    "s3api", "get-object", *self._bucket_arguments(), "--key", key, str(target),
                )
            except _S3RequestError as error:
                if error.code in {"NoSuchKey", "NotFound", "404"}:
                    raise FileNotFoundError(f"remote replica lacks {sha256}") from None
                raise
            try:
                payload = target.read_bytes()
            except OSError:
                raise ReplicaUnavailable("authenticated remote read returned no local bytes") from None
        if hashlib.sha256(payload).hexdigest() != sha256 or response.get("ContentLength") != len(payload):
            raise ValueError(f"remote replica SHA-256 or content length differs for {sha256}")
        return payload

    def put(self, payload: bytes) -> str:
        digest = hashlib.sha256(payload).digest()
        sha256 = digest.hex()
        with tempfile.TemporaryDirectory(prefix="research-s3-put-") as directory:
            asset = Path(directory) / "object"
            asset.write_bytes(payload)
            try:
                self._call(
                    "s3api", "put-object", *self._bucket_arguments(), "--key", self._key(sha256),
                    "--body", str(asset), "--if-none-match", "*", "--checksum-algorithm", "SHA256",
                    "--checksum-sha256", base64.b64encode(digest).decode("ascii"),
                )
            except _S3RequestError as error:
                if error.code not in {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}:
                    raise
                if self.read(sha256) != payload:
                    raise ValueError(f"remote replica has different contents for {sha256}") from None
        return sha256
