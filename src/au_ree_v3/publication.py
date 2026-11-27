"""Immutable V3 research archives, independent replica checks and offline replay."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from time import perf_counter

from .contracts import canonical_json
from .contracts.temporal import TEMPORAL_POLICY_VERSION
from .research_environment import environment_lock
from .replica import AuthenticatedRemoteReplica, ContentReplica
from .sources import ContentStore

RELEASE_SCHEMA = "v3-native-research-release-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"[0-9a-f]{40}")
INPUT_NAMES = {"profile": "profile.json", "bindings": "bindings.json", "sources": "source_manifest.json"}


class ArchiveError(ValueError):
    """An immutable research artifact or release requirement failed validation."""


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _encode(value: object) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _checked_read(store: ContentReplica, sha256: str) -> bytes:
    if not isinstance(sha256, str) or not SHA256.fullmatch(sha256):
        raise ArchiveError(f"invalid content SHA-256: {sha256!r}")
    content = store.read(sha256)
    actual = _digest(content)
    if actual != sha256:
        raise ArchiveError(f"content mismatch: expected {sha256}, read {actual}")
    return content


def _read_json(path: Path) -> dict:
    result = json.loads(path.read_bytes())
    if not isinstance(result, dict):
        raise ArchiveError(f"expected JSON object in {path.name}")
    return result


def _artifact_path(directory: Path, filename: str) -> Path:
    relative = PurePosixPath(filename)
    if relative.is_absolute() or not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise ArchiveError(f"invalid release file path: {filename!r}")
    path = directory.joinpath(*relative.parts)
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ArchiveError(f"release file escapes its directory: {filename!r}")
    return path


def _put(store: ContentReplica, content: bytes) -> str:
    sha256 = _digest(content)
    if store.put(content) != sha256 or _checked_read(store, sha256) != content:
        raise ArchiveError(f"replica failed immutable write/read verification for {sha256}")
    return sha256


def _availability(store: ContentReplica | None, sha256: str) -> dict:
    if store is None:
        return {"status": "REPLICA_UNAVAILABLE"}
    try:
        payload = _checked_read(store, sha256)
    except FileNotFoundError:
        return {"status": "MISSING"}
    except OSError:
        return {"status": "READ_ERROR"}
    except ValueError:
        return {"status": "WRONG_SHA"}
    return {"status": "EXACT_SHA", "byte_count": len(payload)}


@dataclass(frozen=True)
class _ReplicaAccess:
    readable: ContentReplica | None
    independent: bool
    attestation: dict


def _local_filesystem_type(primary: ContentStore) -> str | None:
    """Distinguish a local CAS from an untyped network or object-store mount."""
    mounts = Path("/proc/self/mountinfo")
    if not mounts.is_file():
        return None
    matches = []
    root = primary.root.resolve()
    for line in mounts.read_text(encoding="utf-8").splitlines():
        mount, filesystem = line.split(" - ", 1)
        mountpoint = Path(re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), mount.split()[4]))
        if root.is_relative_to(mountpoint):
            matches.append((len(mountpoint.parts), filesystem.split()[0]))
    return max(matches)[1] if matches else None


def _replica_access(primary: ContentReplica, replica: ContentReplica | None) -> _ReplicaAccess:
    if replica is None:
        return _ReplicaAccess(None, False, {"status": "NOT_CONFIGURED"})
    if not isinstance(replica, AuthenticatedRemoteReplica):
        return _ReplicaAccess(replica, False, {"status": "LOCAL_COPY_ONLY"})
    try:
        evidence = replica.attest_remote()
        if isinstance(primary, AuthenticatedRemoteReplica):
            first = primary.attest_remote()
            independent = first.failure_domain_sha256 != evidence.failure_domain_sha256 and (
                first.provider != evidence.provider or first.region != evidence.region
            )
            basis = f"{first.provider}:{first.region}"
        else:
            basis = _local_filesystem_type(primary) if isinstance(primary, ContentStore) else None
            independent = basis in {"apfs", "btrfs", "ext2", "ext3", "ext4", "overlay", "tmpfs", "xfs", "zfs"}
    except (OSError, ValueError):
        return _ReplicaAccess(None, False, {"status": "REMOTE_ATTESTATION_UNAVAILABLE"})
    return _ReplicaAccess(
        replica, independent,
        {"status": "AUTHENTICATED_REMOTE", "primary_storage": basis, **asdict(evidence)},
    )


def verify_replicas(
    required_sha256: set[str], primary: ContentReplica, replica: ContentReplica | None = None,
    *, replica_failure_domain: str | None = None,
) -> dict:
    """Read and hash every required object from each configured storage copy."""
    return _inventory(required_sha256, primary, _replica_access(primary, replica), replica_failure_domain)


def _inventory(
    required_sha256: set[str], primary: ContentReplica, access: _ReplicaAccess, label: str | None,
    *, checked: Sequence[dict] = (),
) -> dict:
    by_sha = {item["sha256"]: item for item in checked}
    items = [
        by_sha.get(sha) or {
            "sha256": sha, "primary": _availability(primary, sha), "replica": _availability(access.readable, sha),
        }
        for sha in sorted(required_sha256)
    ]
    states = {item[location]["status"] for item in items for location in ("primary", "replica")}
    if "WRONG_SHA" in states:
        status, reason = "FAIL", "REPLICA_DIGEST_MISMATCH"
    elif access.attestation["status"] == "REMOTE_ATTESTATION_UNAVAILABLE":
        status, reason = "BLOCKED", "REPLICA_ATTESTATION_UNAVAILABLE"
    elif access.readable is None:
        status, reason = "BLOCKED", "REPLICA_UNAVAILABLE"
    elif states - {"EXACT_SHA"}:
        status, reason = "BLOCKED", "REPLICA_READBACK_INCOMPLETE"
    elif not items or not access.independent:
        status, reason = "BLOCKED", "REPLICA_INDEPENDENCE_UNVERIFIED"
    else:
        status, reason = "PASS", "INDEPENDENT_READBACK_VERIFIED"
    return {
        "status": status, "reason": reason, "distinct_failure_domain": access.independent,
        "replica_failure_domain": label, "replica_attestation": access.attestation, "required_objects": len(items),
        "primary_verified": sum(item["primary"]["status"] == "EXACT_SHA" for item in items),
        "replica_verified": sum(item["replica"]["status"] == "EXACT_SHA" for item in items),
        "objects": items,
    }


def _copy_missing(contents: set[str], primary: ContentReplica, replica: ContentReplica | None) -> None:
    if replica is None:
        return
    for sha256 in sorted(contents):
        if _availability(replica, sha256)["status"] == "MISSING":
            try:
                _put(replica, _checked_read(primary, sha256))
            except (OSError, ValueError):
                # A missing or corrupt second copy is retained in the next inventory.
                continue


def _input_objects(source_manifest: dict, excluded_receipts: set[str]) -> set[str]:
    objects = set()
    for edition in source_manifest["editions"]:
        if edition.get("receipt_sha256") in excluded_receipts:
            continue
        objects.add(edition["sha256"])
        if edition.get("receipt_sha256"):
            objects.add(edition["receipt_sha256"])
        objects.update(proof["reference_sha256"] for proof in edition.get("temporal_evidence", ()))
    return objects


def _profile_artifacts(profile: Path, document: dict, store: ContentReplica) -> dict[str, str]:
    result = {}
    for reference in document["inputs"].values():
        path, expected = reference["path"], reference["sha256"]
        if path == INPUT_NAMES["profile"] or path in result:
            raise ArchiveError("profile input paths must be distinct")
        data = _artifact_path(profile.parent, path).read_bytes()
        if _digest(data) != expected:
            raise ArchiveError(f"profile component differs from its pinned SHA-256: {path}")
        result[path] = _put(store, data)
    return result


def _write_once(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != payload:
                raise ArchiveError(f"immutable artifact already has different contents: {path.name}")
    finally:
        temporary.unlink()


def _code_archive(repository: Path, commit: str) -> bytes:
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ArchiveError("research archive requires a full V3 code commit")
    verified = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "--verify", f"{commit}^{{commit}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if verified != commit:
        raise ArchiveError("code commit does not identify the pinned repository object")
    return subprocess.run(
        ["git", "-C", str(repository), "archive", "--format=tar", commit],
        check=True, capture_output=True,
    ).stdout


def _manifest_path(release_root: Path, release_id: str) -> Path:
    if not isinstance(release_id, str) or not SHA256.fullmatch(release_id):
        raise ArchiveError("release ID must be a full SHA-256")
    return release_root / "releases" / f"{release_id}.json"


def load_release(release_root: Path, release_id: str, storage: ContentReplica | None = None) -> dict:
    path = _manifest_path(release_root, release_id)
    try:
        payload = path.read_bytes()
    except FileNotFoundError:
        if storage is None:
            raise
        payload = _checked_read(storage, release_id)
    manifest = json.loads(payload)
    if (
        not isinstance(manifest, dict) or manifest.get("schema") != RELEASE_SCHEMA
        or _digest(payload) != release_id or _encode(manifest) != payload
    ):
        raise ArchiveError("release manifest digest or schema mismatch")
    return manifest


def archive_release(
    *, repository: Path, release_root: Path, profile: Path, bindings: Path, sources: Path, build: Path,
    primary: ContentReplica, replica: ContentReplica | None = None, replica_failure_domain: str | None = None,
) -> dict:
    """Package one V3 build, indexing a canonical manifest by content hash."""
    receipt = _read_json(build / "build_receipt.json")
    profile_doc = _read_json(profile)
    source_doc = _read_json(sources)
    input_paths = {"profile": profile, "bindings": bindings, "sources": sources}
    inputs = {name: _put(primary, path.read_bytes()) for name, path in input_paths.items()}
    if inputs != receipt["input_artifacts"] or (
        profile_doc["research_cutoff"], profile_doc["timezone"]
    ) != (source_doc["cutoff"], source_doc["timezone"]):
        raise ArchiveError("build receipt or source cutoff differs from the supplied release inputs")
    configuration = _profile_artifacts(profile, profile_doc, primary)

    declared = receipt["scientific_artifacts"]
    if not declared or "census/census.csv" not in declared or "census/cell_status.csv" not in declared:
        raise ArchiveError("research release requires a Census and its cell-state output")
    outputs = {
        name: _put(primary, _artifact_path(build, name).read_bytes())
        for name in sorted(declared)
    }
    if outputs != declared:
        raise ArchiveError("build scientific artifact differs from its receipt")
    exclusions = receipt.get("source_verification_failures", [])
    excluded_captures = {item["receipt_sha256"] for item in exclusions}
    if not excluded_captures.issubset({edition.get("receipt_sha256") for edition in source_doc["editions"]}):
        raise ArchiveError("build lists a quarantined source outside its source manifest")
    official_objects = _input_objects(source_doc, excluded_captures)
    for sha256 in sorted(official_objects):
        _checked_read(primary, sha256)

    current_environment = environment_lock()
    if receipt["environment"] != current_environment:
        raise ArchiveError("current environment differs from the reviewed build")
    code_commit = receipt["code_commit"]
    code = _put(primary, _code_archive(repository, code_commit))
    environment = _put(primary, _encode(current_environment))
    build_receipt = _put(primary, (build / "build_receipt.json").read_bytes())
    contents = {*inputs.values(), *configuration.values(), *outputs.values(), *official_objects, code, environment, build_receipt}
    access = _replica_access(primary, replica)
    _copy_missing(contents, primary, access.readable)

    inventory = _inventory(contents, primary, access, replica_failure_domain)
    manifest = {
        "schema": RELEASE_SCHEMA, "release_mode": "RESEARCH_CANDIDATE",
        "cutoff": profile_doc["research_cutoff"], "timezone": profile_doc["timezone"],
        "code_commit": code_commit, "code_archive_sha256": code,
        "environment_hash": environment, "build_receipt_sha256": build_receipt,
        "project_registry_hash": receipt["project_registry_hash"],
        "source_manifest_hash": inputs["sources"], "temporal_policy_version": TEMPORAL_POLICY_VERSION,
        "evidence_policy_version": receipt["evidence_policy_version"],
        "parser_version": receipt["parser_version"], "database_schema": receipt["database_schema"],
        "selection_rules_hash": receipt["selection_rules_hash"], "census_hash": outputs["census/census.csv"],
        "mcda_inputs_hash": receipt["mcda_inputs_hash"], "input_artifacts": inputs, "output_hashes": outputs,
        "profile_artifacts": configuration,
        "source_and_evidence_sha256": sorted(official_objects), "required_object_sha256": sorted(contents),
        "quarantined_source_captures": exclusions,
        "replica_inventory": inventory, "build_gates": receipt["gates"],
    }
    payload = _encode(manifest)
    release_id = _put(primary, payload)
    _copy_missing({release_id}, primary, access.readable)
    _write_once(_manifest_path(release_root, release_id), payload)
    full_inventory = _inventory(
        contents | {release_id}, primary, access, replica_failure_domain, checked=inventory["objects"],
    )
    return {
        "status": "PASS", "release_id": release_id,
        "manifest": str(_manifest_path(release_root, release_id)),
        "replica_inventory": full_inventory,
        "F5": full_inventory["status"] if full_inventory["status"] != "PASS" else "NOT_RUN",
    }


def sync_release_replica(
    *, release_root: Path, release_id: str, primary: ContentReplica, replica: ContentReplica | None,
    out: Path, replica_failure_domain: str | None = None,
) -> dict:
    """Copy and read back an already sealed archive without creating a new release."""
    manifest = load_release(release_root, release_id, primary)
    required = set(manifest["required_object_sha256"]) | {release_id}
    access = _replica_access(primary, replica)
    _copy_missing(required, primary, access.readable)
    inventory = _inventory(required, primary, access, replica_failure_domain)
    receipt = {
        "schema": "research-replica-verification-v1", "release_id": release_id,
        "status": inventory["status"], "reason": inventory["reason"],
        "F5": "NOT_RUN" if inventory["status"] == "PASS" else inventory["status"],
        "replica_inventory": inventory,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "replica_verification.json").write_bytes(_encode(receipt))
    return receipt


def restore_release_replica(
    *, release_root: Path, release_id: str, replica: AuthenticatedRemoteReplica, destination: ContentStore, out: Path,
) -> dict:
    """Recover verified objects to a local store before invoking offline replay."""
    access = _replica_access(destination, replica)
    receipt = {
        "schema": "research-replica-restoration-v1", "release_id": release_id,
        "status": "BLOCKED", "reason": "REPLICA_ATTESTATION_UNAVAILABLE",
        "replica_attestation": access.attestation,
    }
    if access.readable is not None:
        try:
            manifest = load_release(release_root, release_id, replica)
            required = set(manifest["required_object_sha256"]) | {release_id}
            _copy_missing(required, replica, destination)
            inventory = _inventory(required, destination, access, None)
            complete = inventory["primary_verified"] == inventory["replica_verified"] == len(required)
            receipt.update(
                status="PASS" if complete else inventory["status"],
                reason="VERIFIED_LOCAL_RESTORE" if complete else inventory["reason"],
                inventory=inventory,
            )
        except OSError:
            receipt["reason"] = "REMOTE_RELEASE_UNAVAILABLE"
        except ValueError:
            receipt.update(status="FAIL", reason="REMOTE_RELEASE_DIGEST_OR_SCHEMA_MISMATCH")
    out.mkdir(parents=True, exist_ok=True)
    (out / "restoration_receipt.json").write_bytes(_encode(receipt))
    return receipt


def _extract_code(tar_data: bytes, target: Path) -> None:
    """Restore only plain files from a pinned Git archive."""
    target.mkdir()
    with tarfile.open(fileobj=BytesIO(tar_data), mode="r:") as package:
        for member in package:
            path = _artifact_path(target, member.name)
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ArchiveError(f"unsupported code archive object: {member.name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as source, path.open("xb") as output:
                shutil.copyfileobj(source, output)


_OFFLINE_BUILD = """
import sys

def deny_network(event, *args):
    if event.startswith("socket."):
        raise RuntimeError("network access is disabled during cold replay")

sys.addaudithook(deny_network)
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
from au_ree_v3.fresh_research import build_research
result = build_research(
    profile=Path(sys.argv[1]), bindings=Path(sys.argv[2]), sources=Path(sys.argv[3]),
    assets=Path(sys.argv[4]), out=Path(sys.argv[5]), code_commit=sys.argv[6],
)
if not result.get("scientific_artifacts"):
    raise RuntimeError("rebuild produced no scientific artifacts")
"""


def replay_release(*, release_root: Path, release_id: str, storage: ContentReplica, out: Path) -> dict:
    """Cold-rebuild using the archived V3 code, exact CAS objects and locked environment."""
    started = perf_counter()
    out.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": RELEASE_SCHEMA, "release_id": release_id, "offline": True, "status": "FAIL", "F4": "FAIL"}
    try:
        manifest = load_release(release_root, release_id, storage)
        if _checked_read(storage, release_id) != _encode(manifest):
            raise ArchiveError("CAS copy of the release manifest differs from the index")
        for sha256 in manifest["required_object_sha256"]:
            _checked_read(storage, sha256)
        environment = json.loads(_checked_read(storage, manifest["environment_hash"]))
        if environment != environment_lock(include_pdf_images="pdfimages" in environment["tools"]):
            receipt.update(status="BLOCKED", F4="BLOCKED", reason="REPLAY_ENVIRONMENT_MISMATCH")
        else:
            with tempfile.TemporaryDirectory(prefix="v3-research-cold-") as temporary:
                working = Path(temporary)
                code = working / "v3"
                _extract_code(_checked_read(storage, manifest["code_archive_sha256"]), code)
                pinned_inputs = working / "inputs"
                pinned_inputs.mkdir()
                for name, sha256 in manifest["profile_artifacts"].items():
                    path = _artifact_path(pinned_inputs, name)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(_checked_read(storage, sha256))
                materialized = {}
                for name, sha256 in manifest["input_artifacts"].items():
                    path = working / INPUT_NAMES[name] if name != "profile" else pinned_inputs / INPUT_NAMES[name]
                    path.write_bytes(_checked_read(storage, sha256))
                    materialized[name] = path
                objects = working / "assets" / "objects"
                objects.mkdir(parents=True)
                for sha256 in manifest["source_and_evidence_sha256"]:
                    (objects / sha256).write_bytes(_checked_read(storage, sha256))
                rebuilt = working / "build"
                process = subprocess.run(
                    [
                        sys.executable, "-I", "-B", "-c", _OFFLINE_BUILD,
                        *(str(materialized[name]) for name in INPUT_NAMES),
                        str(objects.parent), str(rebuilt), manifest["code_commit"],
                    ],
                    check=False, capture_output=True, text=True, timeout=600, cwd=code,
                )
                if process.returncode:
                    raise ArchiveError(f"COLD_REBUILD_FAILED: {process.stderr.strip()[-2000:]}")
                actual_receipt = _read_json(rebuilt / "build_receipt.json")
                original_receipt = json.loads(_checked_read(storage, manifest["build_receipt_sha256"]))
                exclusions = manifest["quarantined_source_captures"]
                non_scientific = {"scientific_artifacts"}
                if exclusions and {
                    item["receipt_sha256"] for item in actual_receipt.get("source_verification_failures", ())
                } == {item["receipt_sha256"] for item in exclusions}:
                    # Unusable capture objects are excluded from the replay scope.
                    # A rejected corrupt capture is absent in the restored store.
                    non_scientific.add("source_verification_failures")
                    actual_gates = dict(actual_receipt["gates"])
                    original_gates = original_receipt["gates"]
                    if {actual_gates["F1"], original_gates["F1"]} <= {"FAIL", "BLOCKED"}:
                        actual_gates["F1"] = original_gates["F1"]
                        actual_receipt = {**actual_receipt, "gates": actual_gates}
                receipt_differences = [
                    name for name in sorted(actual_receipt.keys() | original_receipt.keys())
                    if name not in non_scientific and actual_receipt.get(name) != original_receipt.get(name)
                ]
                declared = actual_receipt["scientific_artifacts"]
                expected = manifest["output_hashes"]
                if set(declared) != set(expected):
                    raise ArchiveError("cold rebuild reported a different scientific artifact set")
                actual = {
                    name: _digest(_artifact_path(rebuilt, name).read_bytes()) for name in sorted(declared)
                }
                differing = [
                    {"artifact": name, "expected": expected[name], "actual": actual[name]}
                    for name in sorted(expected) if expected[name] != actual[name]
                ]
                receipt.update(
                    expected=expected, actual=actual, differences=differing,
                    compared_artifacts=len(actual), scientific_output_count=actual_receipt["scientific_output_count"],
                    build_receipt_difference_fields=receipt_differences,
                    excluded_unusable_capture_count=len(exclusions),
                    mcda_panels=actual_receipt.get("mcda_panels", {}),
                )
                differed = bool(differing or receipt_differences) or actual != declared
                if differed:
                    receipt.update(status="FAIL", F4="FAIL", reason="SCIENTIFIC_ARTIFACT_MISMATCH")
                elif not actual_receipt["scientific_output_count"]:
                    receipt.update(status="BLOCKED", F4="BLOCKED", reason="NO_GOVERNED_VALUES_FOR_SOURCE_TO_CENSUS_COMPARISON")
                elif actual_receipt["gates"].get("F3") != "PASS":
                    receipt.update(status="BLOCKED", F4="BLOCKED", reason="CENSUS_CONTRACT_UNVERIFIED")
                else:
                    receipt.update(status="PASS", F4="PASS", reason="COLD_SCIENTIFIC_ARTIFACTS_MATCH")
                if not differed:
                    for name in sorted(actual):
                        output_path = _artifact_path(out / "rebuilt", name)
                        _write_once(output_path, _artifact_path(rebuilt, name).read_bytes())
    except FileNotFoundError as error:
        receipt.update(status="BLOCKED", F4="BLOCKED", reason="REPLAY_OBJECT_UNAVAILABLE", detail=str(error))
    except (ArchiveError, OSError, ValueError, KeyError, subprocess.SubprocessError, tarfile.TarError) as error:
        receipt.update(status="FAIL", F4="FAIL", reason="REPLAY_VERIFICATION_FAILED", detail=str(error))
    receipt["elapsed_seconds"] = perf_counter() - started
    (out / "replay_receipt.json").write_bytes(_encode(receipt))
    return receipt


@contextmanager
def _pointer_lock(release_root: Path):
    release_root.mkdir(parents=True, exist_ok=True)
    with (release_root / ".pointer.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _read_pointer(path: Path) -> dict:
    if not path.exists():
        return {"current": None, "previous": None}
    return _read_json(path)


def _replace_pointer(path: Path, current: str, previous: str | None) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(_encode({"current": current, "previous": previous}))
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checked_target(release_root: Path, release_id: str, storage: ContentReplica) -> dict:
    target = load_release(release_root, release_id, storage)
    for sha256 in [release_id, *target["required_object_sha256"]]:
        _checked_read(storage, sha256)
    return target


def _rollback(release_root: Path, path: Path, expected_current: str, storage: ContentReplica) -> dict:
    before = _read_pointer(path)
    if before["current"] != expected_current or not before["previous"]:
        raise ArchiveError("current changed or no verified previous release is recorded")
    _checked_target(release_root, before["previous"], storage)
    _replace_pointer(path, before["previous"], before["current"])
    return {"before": before, "after": _read_pointer(path)}


def rehearse_rollback(
    *, release_root: Path, previous_release_id: str, release_id: str, storage: ContentReplica, out: Path,
) -> dict:
    """Exercise the production rollback operation against an isolated pointer."""
    if previous_release_id == release_id:
        raise ArchiveError("rollback rehearsal requires two distinct immutable releases")
    first = _checked_target(release_root, previous_release_id, storage)
    second = _checked_target(release_root, release_id, storage)
    official = release_root / "current.json"
    before_official = official.read_bytes() if official.exists() else None
    with tempfile.TemporaryDirectory(prefix="v3-rollback-") as temporary:
        pointer = Path(temporary) / "current.json"
        _replace_pointer(pointer, release_id, previous_release_id)
        result = _rollback(release_root, pointer, release_id, storage)
    after_official = official.read_bytes() if official.exists() else None
    if after_official != before_official or (
        first != _checked_target(release_root, previous_release_id, storage)
        or second != _checked_target(release_root, release_id, storage)
    ):
        raise ArchiveError("rollback modified an archived release or the formal pointer")
    receipt = {"status": "PASS", **result, "pointer_only": True, "formal_pointer_unchanged": True}
    out.mkdir(parents=True, exist_ok=True)
    (out / "rollback_receipt.json").write_bytes(_encode(receipt))
    return receipt


def publish_release(
    *, release_root: Path, release_id: str, primary: ContentReplica, replica: ContentReplica | None,
    replica_failure_domain: str | None, out: Path, previous_release_id: str, expected_current: str | None,
) -> dict:
    """Advance the formal pointer only after replica, cold replay and rollback verification."""
    manifest = load_release(release_root, release_id, primary)
    inventory = verify_replicas(
        set(manifest["required_object_sha256"]) | {release_id}, primary, replica,
        replica_failure_domain=replica_failure_domain,
    )
    result = {"release_id": release_id, "F5": inventory["status"], "replica_inventory": inventory}
    if inventory["status"] == "PASS":
        gates = manifest["build_gates"]
        if any(gates.get(gate) != "PASS" for gate in ("F0", "F1", "F2", "F3")):
            result.update(F5="BLOCKED", reason="BUILD_GATES_INCOMPLETE")
        else:
            replay = replay_release(release_root=release_root, release_id=release_id, storage=replica, out=out / "cold")
            result.update(F5=replay["F4"], replay=replay)
            if replay["F4"] == "PASS":
                result["rollback"] = rehearse_rollback(
                    release_root=release_root, previous_release_id=previous_release_id,
                    release_id=release_id, storage=replica, out=out / "rollback",
                )
                with _pointer_lock(release_root):
                    pointer = release_root / "current.json"
                    before = _read_pointer(pointer)
                    if before["current"] != expected_current or (
                        expected_current is not None and previous_release_id != expected_current
                    ):
                        raise ArchiveError("formal pointer changed during publication")
                    _replace_pointer(pointer, release_id, expected_current)
                result.update(reason="FORMAL_POINTER_ADVANCED")
    out.mkdir(parents=True, exist_ok=True)
    (out / "publication_receipt.json").write_bytes(_encode(result))
    return result


def rollback_release(*, release_root: Path, expected_current: str, storage: ContentReplica) -> dict:
    """Point to the prior verified release and preserve both immutable editions."""
    with _pointer_lock(release_root):
        return _rollback(release_root, release_root / "current.json", expected_current, storage)
