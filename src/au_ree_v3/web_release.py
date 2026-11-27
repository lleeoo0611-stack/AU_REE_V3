"""Assemble separate static sites and atomically link authorized research versions."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import tempfile

from .contracts import canonical_json
from .publication import (
    FormalPublicationAuthorization, SHA256, _read_authorization, _sync_directory, _write_once, authorized_publication,
)
from .replica import ContentReplica

POINTER_SCHEMA = "web-release-pointer-v1"
SITE_SCHEMA = "web-site-mode-v1"
LINK_SCHEMA = "web-formal-research-link-v1"
STATIC_FILES = (
    "index.html", "assets/app.js", "assets/labels.mjs", "assets/release.mjs", "assets/views.mjs",
    "assets/styles.css", "assets/australia.geojson",
)
MODES = {"candidate": ("preview.json", "RESEARCH_CANDIDATE"), "formal": ("latest.json", "FORMAL_CURRENT")}


class WebsiteReleaseError(ValueError):
    """A static site pointer, manifest, or release failed verification."""


def _encode(value: dict) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _sha(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _read(path: Path) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise WebsiteReleaseError(f"expected a plain site file: {path.name}")
    return path.read_bytes()


def _replace(path: Path, payload: bytes) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="wb", delete=False) as output:
        temporary = Path(output.name)
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    try:
        temporary.chmod(0o644)
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _site_lock(site: Path):
    site.parent.mkdir(parents=True, exist_ok=True)
    with (site.parent / f".{site.name}.web-release.lock").open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def _plain_dir(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        raise WebsiteReleaseError(f"expected a plain site directory: {path.name}")


def _plain_id(release_id: str) -> str:
    if not isinstance(release_id, str) or not SHA256.fullmatch(release_id):
        raise WebsiteReleaseError("release ID must be a full SHA-256")
    return release_id


def _entry(release_id: str, status: str, cutoff: str, digest: str) -> dict:
    return {
        "release_id": release_id, "release_status": status, "cutoff": cutoff,
        "manifest_path": f"releases/{release_id}/manifest.json", "manifest_sha256": digest,
    }


def _validate_entry(row: dict, status: str) -> None:
    if not isinstance(row, dict):
        raise WebsiteReleaseError("catalog entry must be an object")
    release_id = _plain_id(row.get("release_id"))
    digest = _plain_id(row.get("manifest_sha256"))
    cutoff = row.get("cutoff")
    try:
        valid_cutoff = date.fromisoformat(cutoff).isoformat() == cutoff
    except (TypeError, ValueError):
        valid_cutoff = False
    if not valid_cutoff or row != _entry(release_id, status, cutoff, digest):
        raise WebsiteReleaseError("catalog entry contains an invalid status, cutoff or path")


def _pointer(site: Path, mode: str) -> dict | None:
    name, status = MODES[mode]
    path = site / "data" / name
    if not path.exists() and not path.is_symlink():
        return None
    try:
        value = json.loads(_read(path))
        if not isinstance(value, dict) or value.get("schema_version") != POINTER_SCHEMA:
            raise WebsiteReleaseError("web pointer schema mismatch")
        entries = value["releases"]
        if not isinstance(entries, list):
            raise WebsiteReleaseError("web pointer release catalog must be an array")
        for row in entries:
            _validate_entry(row, status)
        by_id = {row["release_id"]: row for row in entries}
        active = by_id[value["release_id"]]
        previous = value["previous_release_id"]
        if len(by_id) != len(entries) or previous == active["release_id"] or (
            previous is not None and previous not in by_id
        ):
            raise WebsiteReleaseError("web pointer catalog has an invalid history")
        timestamp = datetime.fromisoformat(value["updated_at"])
        if timestamp.utcoffset() != timezone.utc.utcoffset(timestamp) or value != {
            "schema_version": POINTER_SCHEMA, **active, "previous_release_id": previous,
            "releases": entries, "updated_at": value["updated_at"],
        }:
            raise WebsiteReleaseError("web pointer disagrees with its catalog")
    except (KeyError, TypeError, ValueError) as error:
        if isinstance(error, WebsiteReleaseError):
            raise
        raise WebsiteReleaseError("web pointer is malformed") from error
    return value


def _check_layout(site: Path, mode: str) -> None:
    for directory in (site, site / "assets", site / "data", site / "data" / "releases"):
        _plain_dir(directory)
    marker = {"schema_version": SITE_SCHEMA, "mode": mode}
    if _read(site / "data" / "publication_mode.json") != _encode(marker):
        raise WebsiteReleaseError("candidate and formal site roots must remain separate")
    for forbidden, _ in MODES.values():
        path = site / "data" / forbidden
        if forbidden != MODES[mode][0] and (path.exists() or path.is_symlink()):
            raise WebsiteReleaseError("site contains the other channel's pointer")


def _frontend_payloads(frontend: Path) -> dict[str, bytes]:
    _plain_dir(frontend)
    _plain_dir(frontend / "assets")
    return {name: _read(frontend / name) for name in STATIC_FILES}


def _build_static(site: Path, frontend: Path, mode: str) -> None:
    payloads = _frontend_payloads(frontend)
    if site.exists() or site.is_symlink():
        _check_layout(site, mode)
        if any(_read(site / name) != content for name, content in payloads.items()):
            raise WebsiteReleaseError("site frontend differs from the supplied immutable frontend build")
        return
    with tempfile.TemporaryDirectory(prefix=f".{site.name}-", dir=site.parent) as temporary:
        stage = Path(temporary) / "site"
        (stage / "assets").mkdir(parents=True)
        (stage / "data" / "releases").mkdir(parents=True)
        for name, payload in payloads.items():
            _replace(stage / name, payload)
        marker = {"schema_version": SITE_SCHEMA, "mode": mode}
        _replace(stage / "data" / "publication_mode.json", _encode(marker))
        os.rename(stage, site)
        _sync_directory(site.parent)


def build_empty_formal_site(*, frontend: Path, site: Path) -> dict:
    """Build the official empty homepage while no formal publication exists."""
    with _site_lock(site):
        _build_static(site, frontend, "formal")
        if _pointer(site, "formal") is not None:
            raise WebsiteReleaseError("site already has a formal pointer")
    return {"status": "NO_FORMAL_RELEASE", "mode": "formal", "formal_pointer_advanced": False}


def _verify_entry(data: Path, row: dict, status: str) -> dict:
    from .web_publication import verify_web_release

    _validate_entry(row, status)
    directory = data / "releases" / row["release_id"]
    _plain_dir(directory)
    manifest = verify_web_release(
        directory, expected_release_id=row["release_id"], expected_manifest_sha256=row["manifest_sha256"],
    )
    if manifest["release_status"] != status or manifest["cutoff"] != row["cutoff"]:
        raise WebsiteReleaseError("manifest role or cutoff differs from its catalog entry")
    return manifest


def _stage_release(
    site: Path, source: Path, release_id: str, status: str,
    authorization: FormalPublicationAuthorization | None = None,
) -> tuple[dict, bool]:
    from .web_publication import verify_web_release

    _plain_dir(source)
    manifest_bytes = _read(source / "manifest.json")
    manifest_sha = _sha(manifest_bytes)
    manifest = verify_web_release(source, expected_release_id=release_id, expected_manifest_sha256=manifest_sha)
    if manifest["release_status"] != status:
        raise WebsiteReleaseError("candidate and formal web releases cannot be interchanged")
    if authorization is not None:
        expected = {
            "authorization_sha256": authorization.authorization_sha256,
            "cutoff": authorization.cutoff,
            "research_build_receipt_sha256": authorization.build_receipt_sha256,
            "profile_sha256": authorization.profile_sha256,
            "census_sha256": authorization.output_hashes["census/census.csv"],
            "research_mcda_inputs_sha256": authorization.output_hashes["decision/mcda_inputs.json"],
            "research_mcda_results_sha256": authorization.output_hashes["decision/mcda_results.json"],
        }
        if any(manifest.get(key) != value for key, value in expected.items()):
            raise WebsiteReleaseError("formal manifest differs from its authorized research artifact hashes")
    data = site / "data"
    row = _entry(release_id, status, manifest["cutoff"], manifest_sha)
    destination = data / "releases" / release_id
    if destination.exists() or destination.is_symlink():
        _verify_entry(data, row, status)
        return row, False
    with tempfile.TemporaryDirectory(prefix=".web-verify-", dir=site.parent) as temporary:
        stage = Path(temporary) / release_id
        stage.mkdir()
        _replace(stage / "manifest.json", manifest_bytes)
        for artifact in manifest["artifacts"].values():
            name = artifact["path"]
            if not isinstance(name, str) or not name or name != Path(name).name or name in {".", "..", "manifest.json"}:
                raise WebsiteReleaseError("web artifact must have a safe release-relative basename")
            _replace(stage / name, _read(source / name))
        verify_web_release(stage, expected_release_id=release_id, expected_manifest_sha256=manifest_sha)
        os.rename(stage, destination)
        _sync_directory(destination.parent)
    return row, True


def _research_link(release_root: Path, row: dict, authorization_sha256: str) -> tuple[Path, bytes]:
    directory = release_root / "web_authorizations"
    if directory.is_symlink():
        raise WebsiteReleaseError("trusted web authorization directory must be plain")
    record = {"schema_version": LINK_SCHEMA, **row, "research_authorization_sha256": authorization_sha256}
    return directory / f"{row['release_id']}.json", _encode(record)


def _verify_formal_links(current: dict | None, release_root: Path, primary: ContentReplica) -> None:
    if current is None:
        return
    for row in current["releases"]:
        authorization = _read_authorization(release_root, row["release_id"], primary)
        path, expected = _research_link(release_root, row, authorization.authorization_sha256)
        if _read(path) != expected:
            raise WebsiteReleaseError("formal website fingerprint differs from its authorized research publication")


def _change_pointer(site: Path, mode: str, row: dict, before: dict | None) -> dict:
    active_id = before["release_id"] if before else None
    if row["release_id"] == active_id:
        if not all(before[key] == value for key, value in row.items()):
            raise WebsiteReleaseError("active web release fingerprint changed")
        return before
    entries = {item["release_id"]: item for item in before["releases"]} if before else {}
    observed = entries.setdefault(row["release_id"], row)
    if observed != row:
        raise WebsiteReleaseError("published web release fingerprint changed")
    pointer = {
        "schema_version": POINTER_SCHEMA, **row, "previous_release_id": active_id,
        "releases": [entries[release_id] for release_id in sorted(entries)],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    _replace(site / "data" / MODES[mode][0], _encode(pointer))
    return pointer


def _discard_unlinked(site: Path, mode: str, row: dict, created: bool) -> None:
    if not created:
        return
    current = _pointer(site, mode)
    if current is None or all(item["release_id"] != row["release_id"] for item in current["releases"]):
        directory = site / "data" / "releases" / row["release_id"]
        shutil.rmtree(directory)
        _sync_directory(directory.parent)


def _current_site(site: Path, mode: str) -> dict | None:
    current = _pointer(site, mode)
    if current:
        for row in current["releases"]:
            _verify_entry(site / "data", row, MODES[mode][1])
    return current


def build_preview_site(*, frontend: Path, site: Path, data: Path, release_id: str) -> dict:
    """Publish verified candidate data only into a separate controlled preview."""
    _plain_id(release_id)
    with _site_lock(site):
        _build_static(site, frontend, "candidate")
        before = _current_site(site, "candidate")
        row, created = _stage_release(site, data / "releases" / release_id, release_id, MODES["candidate"][1])
        try:
            return _change_pointer(site, "candidate", row, before)
        except Exception:
            _discard_unlinked(site, "candidate", row, created)
            raise


def sync_formal_site(
    *, frontend: Path, site: Path, profile: Path, research_build: Path, release_root: Path,
    primary: ContentReplica, release_id: str, expected_current: str | None,
) -> dict:
    """Verify an unchanged official research pointer throughout work and then switch."""
    from .web_publication import export_web

    _plain_id(release_id)
    if expected_current is not None:
        _plain_id(expected_current)
    with authorized_publication(release_root=release_root, release_id=release_id, primary=primary) as authorization:
        with _site_lock(site):
            _build_static(site, frontend, "formal")
            before = _current_site(site, "formal")
            _verify_formal_links(before, release_root, primary)
            if before and before["release_id"] == release_id and before["previous_release_id"] == expected_current:
                return before
            if (before["release_id"] if before else None) != expected_current:
                raise WebsiteReleaseError("formal web pointer changed since approval")
            with tempfile.TemporaryDirectory(prefix=".web-export-", dir=site.parent) as temporary:
                data = Path(temporary)
                export_web(profile=profile, research_build=research_build, release_id=release_id, out=data,
                           authorization=authorization)
                row, created = _stage_release(
                    site, data / "releases" / release_id, release_id, MODES["formal"][1], authorization,
                )
            try:
                link, payload = _research_link(release_root, row, authorization.authorization_sha256)
                if link.is_symlink():
                    raise WebsiteReleaseError("trusted web authorization file must be plain")
                _write_once(link, payload)
                _sync_directory(link.parent)
                return _change_pointer(site, "formal", row, before)
            except Exception:
                _discard_unlinked(site, "formal", row, created)
                raise


def rollback_formal_site(
    *, site: Path, release_root: Path, primary: ContentReplica, release_id: str, expected_current: str,
) -> dict:
    """Align with an authorized research rollback by changing only the prior valid web pointer."""
    _plain_id(release_id)
    _plain_id(expected_current)
    with authorized_publication(release_root=release_root, release_id=release_id, primary=primary):
        with _site_lock(site):
            _check_layout(site, "formal")
            before = _current_site(site, "formal")
            _verify_formal_links(before, release_root, primary)
            if before and before["release_id"] == release_id and before["previous_release_id"] == expected_current:
                return before
            if not before or before["release_id"] != expected_current or before["previous_release_id"] != release_id:
                raise WebsiteReleaseError("requested formal rollback is not the recorded previous site version")
            row = next(row for row in before["releases"] if row["release_id"] == release_id)
            return _change_pointer(site, "formal", row, before)


def verify_site(*, site: Path, mode: str) -> dict:
    """Validate the complete static upload tree before external deployment."""
    _check_layout(site, mode)
    current = _pointer(site, mode)
    expected = set(STATIC_FILES) | {"data/publication_mode.json"}
    if current:
        name, status = MODES[mode]
        expected.add(f"data/{name}")
        for row in current["releases"]:
            manifest = _verify_entry(site / "data", row, status)
            release_path = f"data/releases/{row['release_id']}"
            expected.add(f"{release_path}/manifest.json")
            expected.update(f"{release_path}/{item['path']}" for item in manifest["artifacts"].values())
    actual = set()
    for path in site.rglob("*"):
        if path.is_symlink() or (not path.is_file() and not path.is_dir()):
            raise WebsiteReleaseError("site has an unsupported filesystem object")
        if path.is_file():
            actual.add(path.relative_to(site).as_posix())
    if actual != expected:
        raise WebsiteReleaseError("site upload tree contains undeclared or missing files")
    return {
        "status": "PASS", "mode": mode, "current": current,
        "files": {name: _sha(_read(site / name)) for name in sorted(expected)},
    }


def snapshot_formal_site(
    *, site: Path, snapshot: Path, frontend: Path, release_root: Path, primary: ContentReplica, release_id: str,
) -> dict:
    """Freeze one verified formal upload tree under the real research and web pointer locks."""
    _plain_id(release_id)
    with authorized_publication(release_root=release_root, release_id=release_id, primary=primary):
        with _site_lock(site):
            receipt = verify_site(site=site, mode="formal")
            if not receipt["current"] or receipt["current"]["release_id"] != release_id:
                raise WebsiteReleaseError("upload tree would disagree with the official research pointer")
            _verify_formal_links(receipt["current"], release_root, primary)
            if any(receipt["files"][name] != _sha(payload) for name, payload in _frontend_payloads(frontend).items()):
                raise WebsiteReleaseError("upload tree does not use the approved frontend build")
            if snapshot.exists() or snapshot.is_symlink():
                raise WebsiteReleaseError("deployment snapshot must be new")
            snapshot.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".formal-site-", dir=snapshot.parent) as temporary:
                stage = Path(temporary) / "site"
                shutil.copytree(site, stage)
                if verify_site(site=stage, mode="formal") != receipt:
                    raise WebsiteReleaseError("upload snapshot failed artifact verification")
                os.rename(stage, snapshot)
                _sync_directory(snapshot.parent)
            return receipt
