"""Read V2 evidence and import only explicit raw bindings into the V3 store."""

import json
import sqlite3
from pathlib import Path

from ..contracts import EvidenceAnchor, RawObservation, ResourceScope, Source
from ..store import SourceDigestMismatch, Store
from .crosswalk import RAW_ID_VERSION, RAW_SCHEMA_VERSION, digest, load_spec, observation_raw_id, project_fields


class ImportBlocked(ValueError):
    """A V2 record cannot be linked to a V3 identity without more evidence."""

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason = reason


def read_v2_sqlite(database, observation_ids):
    """Read selected legacy rows and their unmodified metadata from one read snapshot.

    Legacy SQLite REAL values remain legacy measurements. This reader never turns
    their decimal rendering into an original source token.
    """
    uri = Path(database).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA trusted_schema = OFF")
        connection.execute("BEGIN")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise ValueError(f"Unsupported V2 canonical SQLite schema: {version}")
        for table in ("observations", "sources"):
            entry = connection.execute(
                "SELECT type FROM sqlite_master WHERE name=?", (table,)
            ).fetchone()
            if entry is None or entry[0] != "table":
                raise ValueError(f"Missing V2 canonical SQLite table: {table}")
        records = []
        for observation_id in dict.fromkeys(observation_ids):
            observation = connection.execute(
                "SELECT * FROM observations WHERE obs_id=?", (observation_id,)
            ).fetchone()
            if observation is None:
                records.append({"observation_id": observation_id, "status": "NOT_FOUND"})
                continue
            source = connection.execute("SELECT * FROM sources WHERE sha=?", (observation["source_sha"],)).fetchone()
            records.append({
                "observation_id": observation_id, "status": "READ" if source else "SOURCE_UNAVAILABLE",
                "observation": dict(observation), "source": dict(source) if source else None,
            })
        return records
    finally:
        connection.close()


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate V2 payload key: {key}")
        result[key] = value
    return result


def _raw_anchor(fields, payload):
    data = project_fields(fields, "EvidenceAnchor", payload)
    if not data["source_key"] or not data["source_sha"]:
        raise ImportBlocked("SOURCE_UNAVAILABLE", "V2 evidence lacks a source key or exact SHA")
    if data["page"] is None and not data["locator"]:
        return None, (data["source_key"], data["source_sha"])
    anchor = EvidenceAnchor(anchor_id=digest(["v2-anchor-v1", payload]), **data)
    return anchor, (anchor.source_key, anchor.source_sha)


def _prepare_raw(fields, payload, original_payload, store, scope_links):
    if payload["schema_version"] != RAW_SCHEMA_VERSION:
        raise ValueError(f"Unsupported V2 observation schema: {payload['schema_version']}")
    data = project_fields(fields, "RawObservation", payload)
    anchor, source_identity = _raw_anchor(fields, payload["evidence"])
    raw_id = observation_raw_id(payload)
    if "raw_id" in payload and payload["raw_id"] != raw_id:
        raise ImportBlocked("SCIENTIFIC_CONFLICT", "V2 native raw ID disagrees with frozen identity inputs")
    project_id, scope_id = data["project_id"], data["scope_id"]
    if project_id and scope_id:
        data["scope_id"] = scope_id = scope_links.get((project_id, scope_id), scope_id)
    scope = store.get(ResourceScope, scope_id) if project_id and scope_id else None
    if scope is None:
        raise ImportBlocked("EVIDENCE_INSUFFICIENT", "A mapped project and explicit scope are required")
    if scope.project_id != project_id or (data["deposit_id"] is not None and scope.deposit_id != data["deposit_id"]):
        raise ImportBlocked("SCIENTIFIC_CONFLICT", "V2 project or deposit conflicts with the V3 scope binding")
    context = dict(payload.get("context", {}).get("conditions", []))
    raw = RawObservation(
        **data,
        source_key=source_identity[0],
        source_sha=source_identity[1],
        anchor_id=anchor.anchor_id if anchor else None,
        original_payload=original_payload,
        native_raw_id=raw_id,
        id_version=RAW_ID_VERSION,
        category=context.get("category"),
        null_reason=(payload.get("source_value_state") or "V2_ORIGINAL_TOKEN_NOT_REPORTED")
        if data["raw_value"] is None else None,
        status="SHADOW",
    )
    return raw, anchor, source_identity


def _register_source(store, source, assets):
    identity = (source.source_key, source.sha256)
    asset = assets.get(identity)
    if asset is None:
        store.add(source)
        if store.source_verified(*identity):
            return "EXACT_SHA", None
        return "SOURCE_UNAVAILABLE", "Original asset was not provided"
    try:
        store.add_source(source, Path(asset).read_bytes())
    except OSError as exc:
        store.add(source)
        return "SOURCE_UNAVAILABLE", f"Original asset could not be read: {type(exc).__name__}"
    except SourceDigestMismatch:
        store.add(source)
        return "SCIENTIFIC_CONFLICT", "Asset SHA differs from the V2 edition"
    return "EXACT_SHA", None


def import_v2_observations(store: Store, input_path, *, sources, assets=None, scope_links=None):
    """Import frozen observation-v3 JSONL using prebound sources and scopes.

    Sources is a mapping of (source key, SHA-256) to Source metadata. Assets is a
    mapping from the same key to explicit local paths. Scope links optionally
    map (V2 project ID, V2 scope ID) to a specific V3 scope. Identity and evidence gaps
    return the original row for quarantine. Store failures roll back the batch.
    This process creates shadow observations and never makes a formal selection.
    """
    fields = load_spec()["fields"]
    assets = assets or {}
    scope_links = scope_links or {}
    if len(scope_links) != len(set(scope_links.values())):
        raise ValueError("V2 scope links would merge distinct legacy scopes")
    results = []
    source_checks = {}
    with Path(input_path).open(encoding="utf-8", newline="") as input_stream, store.transaction():
        for line_number, line in enumerate(input_stream, 1):
            if not line.strip():
                continue
            original = line.rstrip("\r\n")
            payload = json.loads(original, object_pairs_hook=_unique_keys)
            if not isinstance(payload, dict):
                raise ValueError(f"V2 observation input line {line_number} is not a JSON object")
            result = {"line": line_number, "observation_id": payload.get("observation_id")}
            try:
                raw, anchor, identity = _prepare_raw(fields, payload, original, store, scope_links)
                source = sources.get(identity)
                if source is None:
                    raise ImportBlocked("SOURCE_UNAVAILABLE", "No authenticated metadata for the V2 edition")
                if not isinstance(source, Source) or (source.source_key, source.sha256) != identity:
                    raise ValueError(f"Configured V2 source metadata disagrees with binding: {identity}")
            except ImportBlocked as exc:
                results.append({
                    **result, "status": exc.reason, "reason": str(exc), "inserted": False,
                    "original_payload": original,
                })
                continue
            if identity not in source_checks:
                source_checks[identity] = _register_source(store, source, assets)
            status, reason = source_checks[identity]
            if anchor is not None:
                store.add(anchor)
            elif status == "EXACT_SHA":
                status, reason = "EVIDENCE_INSUFFICIENT", "The observation has no resolvable source locator"
            inserted = store.add(raw)
            results.append({
                **result, "status": "IMPORTED" if status == "EXACT_SHA" else status,
                "reason": reason, "native_raw_id": raw.native_raw_id, "inserted": inserted,
            })
    statuses = {result["status"] for result in results}
    overall = "NOT_RUN" if not results else "PASS"
    if "SCIENTIFIC_CONFLICT" in statuses:
        overall = "FAIL"
    elif statuses - {"IMPORTED"}:
        overall = "BLOCKED"
    return {"schema_version": RAW_SCHEMA_VERSION, "status": overall, "results": results}
