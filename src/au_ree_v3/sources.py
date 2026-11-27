"""Project-scoped public source lookup and immutable exact-byte capture."""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import sha256
from http.client import HTTPException
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
from tempfile import NamedTemporaryFile
from threading import BoundedSemaphore
from time import monotonic, perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .contracts.temporal import TemporalEvidence, evaluate_temporal


_SHA256 = re.compile(r"[a-f0-9]{64}")
SCHEMA = "fresh-source-manifest-v1"
CAPTURE_SCHEMA = "public-response-capture-v1"


class IntegrityError(ValueError):
    """A content-addressed object's bytes disagree with its SHA-256."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _immutable_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent) as staged:
        staged.write(payload)
        staged.flush()
        try:
            os.link(staged.name, path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes() != payload:
                raise IntegrityError(f"Conflicting bytes at {path}") from None


@dataclass(frozen=True, slots=True)
class ContentStore:
    root: Path

    def path(self, digest: str) -> Path:
        if not _SHA256.fullmatch(digest):
            raise IntegrityError("Invalid content SHA-256")
        return self.root / "objects" / digest

    def read(self, digest: str) -> bytes:
        path = self.path(digest)
        if path.is_symlink():
            raise IntegrityError(f"Content object is a symbolic link: {digest}")
        payload = path.read_bytes()
        if sha256(payload).hexdigest() != digest:
            raise IntegrityError(f"Content SHA-256 mismatch: {digest}")
        return payload

    def put(self, payload: bytes) -> str:
        digest = sha256(payload).hexdigest()
        _immutable_write(self.path(digest), payload)
        self.read(digest)
        return digest

    def register_capture(self, receipt: dict) -> str:
        digest = self.put(_json_bytes(receipt))
        marker = self.root / "captures" / f"{digest}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(self.path(digest), marker)
        except FileExistsError:
            if marker.is_symlink() or marker.read_bytes() != _json_bytes(receipt):
                raise IntegrityError(f"Conflicting capture index entry: {digest}") from None
        return digest

    def prior_captures(self) -> dict[str, list[tuple[str, dict]]]:
        by_url = defaultdict(list)
        for p in sorted((self.root / "captures").glob("*.json")):
            data = self.read(p.stem)
            receipt = json.loads(data)
            if receipt["schema"] == CAPTURE_SCHEMA:
                by_url[receipt["requested_url"]].append((p.stem, receipt))
        return by_url


def _public_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return False
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if host == "localhost" or host.endswith((".localhost", ".local")):
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return "." in host


class _PublicRedirect(HTTPRedirectHandler):
    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _public_url(newurl):
            raise HTTPError(newurl, code, "SOURCE_POLICY_RESTRICTED", headers, fp)
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


@dataclass(frozen=True, slots=True)
class HttpResult:
    status: str
    final_url: str
    at: str
    http_status: int | None = None
    response_mime: str | None = None
    payload: bytes | None = None
    detail: str | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_public(url: str, method: str, timeout: float, max_bytes: int) -> HttpResult:
    """Fetch an anonymous HTTPS response without authentication or access workarounds."""
    if not _public_url(url):
        return HttpResult("SOURCE_POLICY_RESTRICTED", url, _now())
    request = Request(url, method=method, headers={
        "User-Agent": "AU_REE_V3/1.0 (public research source verification)",
        "Accept-Encoding": "identity",
    })
    started = monotonic()
    try:
        with build_opener(_PublicRedirect).open(request, timeout=timeout) as response:
            final = response.url
            mime = response.headers.get_content_type()
            status = response.status
            if method == "HEAD":
                return HttpResult("SOURCE_LOCATOR_REACHABLE", final, _now(), status, mime)
            if status != 200 or response.headers.get("Content-Range"):
                return HttpResult("INCOMPLETE_RESPONSE", final, _now(), status, mime)
            encoding = response.headers.get("Content-Encoding", "identity").lower()
            if encoding != "identity":
                return HttpResult("UNSUPPORTED_CONTENT_ENCODING", final, _now(), status, mime, detail=encoding)
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdecimal() or int(length) > max_bytes):
                return HttpResult("RESPONSE_TOO_LARGE", final, _now(), status, mime)
            chunks = []
            size = 0
            while chunk := response.read(min(256 * 1024, max_bytes - size + 1)):
                chunks.append(chunk)
                size += len(chunk)
                if size > max_bytes:
                    return HttpResult("RESPONSE_TOO_LARGE", final, _now(), status, mime)
                if monotonic() - started > timeout:
                    return HttpResult("TIMEOUT", final, _now(), status, mime)
            if length is not None and size != int(length):
                return HttpResult("INCOMPLETE_RESPONSE", final, _now(), status, mime)
            return HttpResult("FETCHED", final, _now(), status, mime, b"".join(chunks))
    except HTTPError as error:
        status = "SOURCE_POLICY_RESTRICTED" if error.reason == "SOURCE_POLICY_RESTRICTED" else f"HTTP_{error.code}"
        return HttpResult(status, error.url, _now(), error.code, detail=str(error.reason))
    except (TimeoutError, socket.timeout) as error:
        return HttpResult("TIMEOUT", url, _now(), detail=str(error))
    except URLError as error:
        status = "TIMEOUT" if isinstance(error.reason, TimeoutError) else "NETWORK_ERROR"
        return HttpResult(status, url, _now(), detail=str(error.reason))
    except (HTTPException, OSError) as error:
        return HttpResult("DOWNLOAD_INTERRUPTED", url, _now(), detail=str(error))


def _kind(url: str, planned: str | None, body: bytes, mime: str | None) -> str | None:
    mime = mime or ""
    prefix = body[:4096].lstrip()
    if not prefix:
        return None
    if prefix.startswith(b"%PDF-"):
        detected = "pdf"
    elif prefix.lower().startswith((b"<!doctype html", b"<html", b"<?xml")):
        detected = "html" if not prefix.lower().startswith(b"<?xml") else "xml"
        lower = prefix.lower()
        if any(marker in lower for marker in (b"<title>access denied", b"<title>just a moment", b"<title>request rejected")):
            return None
    elif prefix.startswith(b"PK\x03\x04"):
        detected = "xlsx" if planned == "xlsx" or mime.endswith("spreadsheetml.sheet") else "zip"
    elif mime in {"application/json", "application/geo+json"} and prefix.startswith((b"[", b"{")):
        detected = "geojson" if planned == "geojson" or mime == "application/geo+json" else "json"
    elif mime in {"text/csv", "application/csv", "text/tab-separated-values"}:
        detected = "csv"
    else:
        return None
    parts = urlsplit(url)
    if planned is None and (parts.path.lower().endswith(".pdf") or "pdf" in parse_qs(parts.query).get("display", [])):
        planned = "pdf"
    return detected if planned in (None, detected) else None


def validate_capture_proof(store: ContentStore, receipt_sha256: str, source_sha256: str, research_timezone: ZoneInfo) -> TemporalEvidence:
    """Verify both immutable objects and the anonymous response's timestamp and source identity."""
    receipt = json.loads(store.read(receipt_sha256))
    raw = store.read(source_sha256)
    if (receipt["schema"] != CAPTURE_SCHEMA or receipt["sha256"] != source_sha256
            or receipt["status"] != "PUBLIC_CAPTURED" or receipt["http_status"] != 200
            or receipt["method"] != "ANONYMOUS_HTTPS_GET"
            or not _public_url(receipt["requested_url"]) or not _public_url(receipt["final_url"])
            or receipt["bytes"] != len(raw)
            or _kind(receipt["requested_url"], receipt["kind"], raw, receipt["response_mime"]) is None):
        raise IntegrityError("Public response does not prove the exact source edition")
    original = datetime.fromisoformat(receipt["acquired_at"])
    if original.utcoffset() is None or original.astimezone(timezone.utc).isoformat() != receipt["acquired_at_utc"]:
        raise IntegrityError("Capture timestamp normalization mismatch")
    public_date = original.astimezone(research_timezone).date().isoformat()
    return TemporalEvidence(source_sha256, "ARCHIVED_PUBLIC_CAPTURE", public_date,
                            f"sha256:{receipt_sha256}", receipt_sha256)


def validate_edition_proofs(store: ContentStore, edition: dict, timezone: ZoneInfo) -> tuple[TemporalEvidence, ...]:
    """Verify exact edition lineage, including the first and current capture clocks."""
    verified = {}
    for claim in edition["temporal_evidence"]:
        proof = validate_capture_proof(store, claim["reference_sha256"], edition["sha256"], timezone)
        receipt = json.loads(store.read(proof.reference_sha256))
        if asdict(proof) != claim:
            raise IntegrityError("Public proof differs from the exact edition")
        verified[proof.reference_sha256] = proof, receipt
    if not verified:
        return ()
    if edition["receipt_sha256"] not in verified:
        raise IntegrityError("Current acquisition receipt is not pinned by the edition")
    current_event = verified[edition["receipt_sha256"]][1]
    current_fields = {
        "requested_url": "url", "final_url": "final_url", "project_ids": "project_ids",
        "kind": "kind", "bytes": "bytes", "acquired_at": "event_acquired_at",
    }
    if any(current_event[field] != edition.get(target) for field, target in current_fields.items()):
        raise IntegrityError("Edition lineage disagrees with its current capture event")
    current = current_event["acquired_at"]
    first = min((item[1]["acquired_at"] for item in verified.values()), key=datetime.fromisoformat)
    reacquired = current if datetime.fromisoformat(current) != datetime.fromisoformat(first) else None
    if edition["acquired_at"] != first or edition["reacquired_at"] != reacquired:
        raise IntegrityError("Edition acquisition dates disagree with the immutable capture receipts")
    return tuple(sorted((value[0] for value in verified.values()), key=lambda item: item.evidence_id))


def _profile_inputs(profile_path: Path):
    from .registry import load_profile

    profile = load_profile(profile_path)
    registry = json.loads(profile.registry_path.read_bytes())
    catalog = json.loads(profile.source_catalog_path.read_bytes())
    ids = tuple(row["project_id"] for row in registry["projects"])
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("Invalid project cohort in research profile")
    return profile, ids, catalog["seed_urls"]


def _planned_seeds(ids: tuple[str, ...], seeds: list[dict], limit: int) -> list[dict]:
    if limit < 1:
        raise ValueError("Per-project lookup limit must be positive")
    by_project = defaultdict(list)
    for source in seeds:
        for project in source["project_ids"]:
            if project not in ids:
                raise ValueError(f"Source seed points outside the registry: {project}")
            by_project[project].append(source)
    selected = {}
    for project in ids:
        priority = lambda source: (source.get("priority_by_project", {}).get(project, 999), source["source_key"], source["url"])
        for source in sorted(by_project[project], key=priority)[:limit]:
            key = source["url"]
            record = selected.get(key)
            if record is None:
                record = {**source, "project_ids": [], "source_keys": [], "references": [], "historical_sha256": []}
                selected[key] = record
            if record.get("kind") is None:
                record["kind"] = source.get("kind")
            elif source.get("kind") and source["kind"] != record["kind"]:
                raise ValueError(f"Source catalog assigns contradictory media kinds to {key}")
            if project not in record["project_ids"]:
                record["project_ids"].append(project)
            record["source_keys"] = sorted(set(record["source_keys"]) | {source["source_key"]})
            if source.get("reference") and source["reference"] not in record["references"]:
                record["references"].append(source["reference"])
            record["historical_sha256"] = sorted(set(record["historical_sha256"]) | set(source.get("historical_sha256", [])))
    return list(selected.values())


def _timestamp_fields(original: str, research_timezone: ZoneInfo) -> dict:
    instant = datetime.fromisoformat(original)
    if instant.utcoffset() is None:
        raise ValueError("Acquisition timestamp must include its original offset")
    return {"acquired_at": original,
            "acquired_at_utc": instant.astimezone(timezone.utc).isoformat(),
            "acquired_at_research_timezone": instant.astimezone(research_timezone).isoformat()}


def _request_receipt(seed: dict, response: HttpResult, research_timezone: ZoneInfo) -> dict:
    return {
        "source_key": seed["source_key"], "source_keys": seed["source_keys"], "project_ids": seed["project_ids"],
        "seed_references": seed["references"], "publisher": seed.get("publisher"),
        "requested_url": seed["url"], "final_url": response.final_url,
        "expected_kind": seed.get("kind"), "status": response.status, "http_status": response.http_status,
        "response_mime": response.response_mime, "reason": response.detail,
        **_timestamp_fields(response.at, research_timezone),
    }


def _projects(ids: tuple[str, ...], events: list[dict], edition_by_receipt: dict | None = None) -> list[dict]:
    observed = defaultdict(list)
    for event in events:
        for project in event["project_ids"]:
            observed[project].append(event)
    inventory = []
    for project in ids:
        attempts = observed[project]
        acquired = [event for event in attempts if event["status"] == "PUBLIC_CAPTURED"]
        reachable = [event for event in attempts if event["status"] == "SOURCE_LOCATOR_REACHABLE"]
        if not attempts:
            status = "NOT_SEARCHED"
        elif acquired:
            status = "SOURCE_ACQUIRED"
        elif reachable:
            status = "SOURCE_LOCATOR_REACHABLE"
        elif all(event["status"] in {"HTTP_404", "HTTP_410"} for event in attempts):
            status = "NO_SOURCE_FOUND"
        else:
            status = "SOURCE_LOOKUP_BLOCKED"
        inventory.append({
            "project_id": project, "status": status,
            "attempt_count": len(attempts),
            "source_urls": [event["requested_url"] for event in attempts],
            "attempt_statuses": sorted({event["status"] for event in attempts}),
            "source_sha256": sorted({event["sha256"] for event in acquired}),
            "eligible_source_sha256": sorted({
                event["sha256"] for event in acquired
                if edition_by_receipt[event["receipt_sha256"]]["temporal_decision"]["status"] == "ELIGIBLE"
            }) if edition_by_receipt is not None else [],
        })
    return inventory


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value
                             for key, value in row.items()})


def _run_requests(plans, method, workers, timeout, max_bytes, fetcher):
    if workers < 1 or timeout <= 0 or max_bytes <= 0:
        raise ValueError("Invalid public capture resource limits")

    host_limits = {urlsplit(seed["url"]).hostname: BoundedSemaphore(2) for seed in plans}

    def lookup(seed):
        with host_limits[urlsplit(seed["url"]).hostname]:
            return fetcher(seed["url"], method, timeout, max_bytes)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(plans), workers * 2):
            yield from pool.map(lookup, plans[start:start + workers * 2])


def discover_sources(
    profile_path: Path, out: Path, *, workers: int = 4, timeout: float = 12, per_project_limit: int = 2,
    fetcher=fetch_public,
) -> dict:
    """Test reachability of publisher/official source locators for every registered project."""
    started = perf_counter()
    profile, ids, seeds = _profile_inputs(profile_path)
    plans = _planned_seeds(ids, seeds, per_project_limit)
    out.mkdir(parents=True, exist_ok=True)
    events = [_request_receipt(seed, response, profile.timezone)
              for seed, response in zip(plans, _run_requests(plans, "HEAD", workers, timeout, 1, fetcher), strict=True)]
    projects = _projects(ids, events)
    result = {
        "schema": "fresh-source-discovery-v1", "profile_id": profile.profile_id,
        "cutoff": profile.research_cutoff.isoformat(), "timezone": profile.timezone.key,
        "status": "PASS" if all(row["attempt_count"] for row in projects) else "BLOCKED",
        "strategy": "PROJECT_SCOPED_SEED_LOCATOR_CHECK",
        "elapsed_seconds": perf_counter() - started,
        "event_statuses": dict(Counter(event["status"] for event in events)),
        "projects": projects, "events": events,
    }
    _write_json(out / "discovery_receipt.json", result)
    _csv(out / "project_source_inventory.csv", projects)
    return result


def _capture(seed: dict, response: HttpResult, store: ContentStore, profile, prior: dict, by_sha: dict) -> tuple[dict, dict | None]:
    event = _request_receipt(seed, response, profile.timezone)
    if response.status != "FETCHED":
        return event, None
    if (response.http_status != 200 or response.payload is None
            or not _public_url(seed["url"]) or not _public_url(response.final_url)):
        return {**event, "status": "SOURCE_POLICY_RESTRICTED"}, None
    kind = _kind(seed["url"], seed.get("kind"), response.payload, response.response_mime)
    if kind is None:
        return {**event, "status": "MIME_MISMATCH", "bytes": len(response.payload)}, None
    digest = sha256(response.payload).hexdigest()
    prior_bytes = store.path(digest).is_file()
    store.put(response.payload)
    receipt = {
        **event, "schema": CAPTURE_SCHEMA, "status": "PUBLIC_CAPTURED", "method": "ANONYMOUS_HTTPS_GET",
        "kind": kind, "sha256": digest, "bytes": len(response.payload),
    }
    receipt_sha = store.register_capture(receipt)
    seen = prior[seed["url"]]
    known = by_sha[digest]
    proofs = [validate_capture_proof(store, identifier, digest, profile.timezone)
              for identifier, _ in [*known, (receipt_sha, receipt)]]
    old = sorted({item[1]["sha256"] for item in seen} - {digest})
    historical = seed["historical_sha256"]
    reused = bool(known or prior_bytes or digest in historical)
    version = "EXACT_SHA_REUSED" if reused else "NEW_SHA"
    first_observed = min((item[1]["acquired_at_utc"] for item in [*known, (receipt_sha, receipt)]))
    acquired = next(item[1]["acquired_at"] for item in [*known, (receipt_sha, receipt)]
                    if item[1]["acquired_at_utc"] == first_observed)
    reacquired = receipt["acquired_at"] if receipt["acquired_at_utc"] != first_observed else None
    decision = evaluate_temporal(
        source_sha256=digest, cutoff=profile.research_cutoff.isoformat(),
        observation_role="DOCUMENT", acquired_at=acquired, reacquired_at=reacquired, evidence=proofs,
    )
    edition_id = sha256(_json_bytes([seed["url"], digest])).hexdigest()
    seen.append((receipt_sha, receipt))
    known.append((receipt_sha, receipt))
    edition = {
        "edition_id": edition_id, "source_key": seed["source_key"], "source_keys": seed["source_keys"],
        "project_ids": seed["project_ids"], "publisher": seed.get("publisher"), "url": seed["url"],
        "final_url": response.final_url, "kind": kind, "sha256": digest, "bytes": len(response.payload),
        "source_version": digest, "integrity": "EXACT_SHA", "version_status": version,
        "same_url_predecessor_sha256": old,
        "source_changed": bool(old or (historical and digest not in historical)),
        "historical_reference_sha256": historical, "receipt_sha256": receipt_sha,
        "publication_date": None, "available_date": None, "acquired_at": acquired, "reacquired_at": reacquired,
        "event_acquired_at": receipt["acquired_at"],
        "temporal_evidence": [asdict(proof) for proof in proofs], "temporal_decision": decision.to_dict(),
    }
    return {**receipt, "receipt_sha256": receipt_sha}, edition


def _replica_inventory(primary: ContentStore, replica_path: Path | None, digests: set[str]) -> dict:
    if replica_path is None:
        return {"status": "REPLICA_UNAVAILABLE", "objects_read_back": 0}
    if replica_path.resolve() == primary.root.resolve():
        raise ValueError("A replica must have a separate storage root")
    replica = ContentStore(replica_path)
    rows = []
    for digest in sorted(digests):
        try:
            replica.put(primary.read(digest))
            replica.read(digest)
        except (OSError, IntegrityError) as error:
            rows.append({"sha256": digest, "status": "REPLICA_UNAVAILABLE", "reason": str(error)})
        else:
            rows.append({"sha256": digest, "status": "EXACT_SHA"})
    exact = sum(row["status"] == "EXACT_SHA" for row in rows)
    independent_device = bool(exact and replica_path.stat().st_dev != primary.root.stat().st_dev)
    return {"status": "EXACT_SHA" if exact == len(rows) and independent_device else "REPLICA_UNAVAILABLE",
            "objects_read_back": exact, "independent_device": independent_device, "objects": rows}


def acquire_sources(
    profile_path: Path, out: Path, *, store: Path, replica: Path | None = None, workers: int = 4,
    timeout: float = 20, per_project_limit: int = 2, max_bytes: int = 64 * 1024 * 1024,
    fetcher=fetch_public,
) -> dict:
    """Capture prioritized public editions and bind their bytes, lookup attempts, and proof."""
    started = perf_counter()
    profile, ids, seeds = _profile_inputs(profile_path)
    plans = _planned_seeds(ids, seeds, per_project_limit)
    out.mkdir(parents=True, exist_ok=True)
    primary = ContentStore(store)
    prior = primary.prior_captures()
    by_sha = defaultdict(list)
    for captures in prior.values():
        for capture in captures:
            by_sha[capture[1]["sha256"]].append(capture)
    events = []
    editions = []
    for seed, response in zip(plans, _run_requests(plans, "GET", workers, timeout, max_bytes, fetcher), strict=True):
        event, edition = _capture(seed, response, primary, profile, prior, by_sha)
        events.append(event)
        if edition is not None:
            editions.append(edition)
    return _write_manifest(
        profile, ids, events, editions, primary, replica, out,
        elapsed_seconds=perf_counter() - started,
    )


def _write_manifest(profile, ids, events, editions, primary, replica, out, **metadata) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    edition_by_receipt = {edition["receipt_sha256"]: edition for edition in editions}
    projects = _projects(ids, events, edition_by_receipt)
    digests = {edition["sha256"] for edition in editions}
    for edition in editions:
        digests.update(proof["reference_sha256"] for proof in edition["temporal_evidence"])
    replica_receipt = _replica_inventory(primary, replica, digests)
    result = {
        "schema": SCHEMA,
        "profile_id": profile.profile_id, "cutoff": profile.research_cutoff.isoformat(), "timezone": profile.timezone.key,
        "status": "PASS" if all(row["attempt_count"] for row in projects) else "BLOCKED",
        "counts": {
            "projects": len(ids), "projects_searched": sum(bool(row["attempt_count"]) for row in projects),
            "projects_with_source": sum(bool(row["source_sha256"]) for row in projects),
            "projects_with_temporally_eligible_source": sum(bool(row["eligible_source_sha256"]) for row in projects),
            "unique_source_sha256": len({edition["sha256"] for edition in editions}),
            "event_statuses": dict(Counter(event["status"] for event in events)),
            "version_statuses": dict(Counter(edition["version_status"] for edition in editions)),
            "temporal_statuses": dict(Counter(edition["temporal_decision"]["status"] for edition in editions)),
            "same_url_changed_editions": sum(edition["source_changed"] for edition in editions),
        },
        "replica_inventory": replica_receipt,
        **metadata,
        "projects": projects, "events": events, "editions": editions,
    }
    manifest_path = out / "source_manifest.json"
    _write_json(manifest_path, result)
    manifest_digest = primary.put(manifest_path.read_bytes())
    _write_json(out / "source_manifest_integrity.json", {
        "manifest_sha256": manifest_digest, "object": f"objects/{manifest_digest}", "status": "EXACT_SHA",
    })
    _csv(out / "project_source_inventory.csv", projects)
    _csv(out / "source_edition_crosswalk.csv", [{
        key: edition[key] for key in ("edition_id", "source_key", "project_ids", "url", "sha256", "version_status",
                                      "historical_reference_sha256", "same_url_predecessor_sha256", "source_changed", "receipt_sha256")
    } for edition in editions])
    return result



def combine_acquisition(
    profile_path: Path, manifests: list[Path], out: Path, *, store: Path, replica: Path | None = None,
) -> dict:
    """Join scoped real attempts and revalidate each exact object and temporal decision."""
    profile, ids, _ = _profile_inputs(profile_path)
    project_ids = set(ids)
    primary = ContentStore(store)
    cutoff = profile.research_cutoff.isoformat()
    evidence_inputs = []
    events = []
    editions = []
    readbacks = set()
    seen_input_digests = set()
    for path in manifests:
        data = path.read_bytes()
        digest = sha256(data).hexdigest()
        if digest in seen_input_digests:
            continue
        seen_input_digests.add(digest)
        raw = json.loads(data)
        if (raw["schema"], raw["profile_id"], raw["cutoff"], raw["timezone"]) != (
            SCHEMA, profile.profile_id, cutoff, profile.timezone.key,
        ):
            raise IntegrityError(f"Acquisition manifest has a different research profile: {path}")
        evidence_inputs.append({"sha256": digest, "purpose": raw.get("purpose", "PRIMARY_PROFILE_ACQUISITION")})
        successful = {}
        for event in raw["events"]:
            if not event["project_ids"] or not set(event["project_ids"]) <= project_ids:
                raise IntegrityError("Source event references a project outside the frozen cohort")
            if event["status"] == "PUBLIC_CAPTURED":
                capture_id = event["receipt_sha256"]
                preserved = {key: val for key, val in event.items() if key != "receipt_sha256"}
                if json.loads(primary.read(capture_id)) != preserved or capture_id in successful:
                    raise IntegrityError(f"Source event disagrees with immutable capture {capture_id}")
                successful[capture_id] = event
            events.append(event)
        for edition in raw["editions"]:
            capture_id = edition["receipt_sha256"]
            event = successful.pop(capture_id)
            if (event["sha256"], event["requested_url"], event["project_ids"], event["kind"]) != (
                edition["sha256"], edition["url"], edition["project_ids"], edition["kind"],
            ):
                raise IntegrityError(f"Source edition disagrees with immutable capture {capture_id}")
            proofs = []
            for serialized in edition["temporal_evidence"]:
                fingerprint = serialized["reference_sha256"]
                proof = validate_capture_proof(primary, fingerprint, edition["sha256"], profile.timezone)
                if asdict(proof) != serialized:
                    raise IntegrityError(f"Temporal evidence mismatch: {fingerprint}")
                proofs.append(proof)
                readbacks.add(fingerprint)
            if capture_id not in {proof.reference_sha256 for proof in proofs}:
                raise IntegrityError(f"Source edition lacks evidence from its own acquisition: {capture_id}")
            recomputed = evaluate_temporal(
                source_sha256=edition["sha256"], cutoff=cutoff, observation_role="DOCUMENT",
                acquired_at=edition["acquired_at"], reacquired_at=edition["reacquired_at"], evidence=proofs,
            )
            if _json_bytes(recomputed.to_dict()) != _json_bytes(edition["temporal_decision"]):
                raise IntegrityError(f"Temporal decision cannot be reproduced: {capture_id}")
            readbacks.add(edition["sha256"])
            editions.append(edition)
        if successful:
            raise IntegrityError("Acquisition contains a capture without a source edition")
        primary.put(data)
    if not evidence_inputs:
        raise ValueError("Supply at least one acquisition manifest")
    return _write_manifest(profile, ids, events, editions, primary, replica, out,
                           input_manifests=evidence_inputs,
                           primary_readback={"status": "PASS", "verified_objects": len(readbacks)})
