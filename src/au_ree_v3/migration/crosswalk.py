"""Read the crosswalk against immutable V2 declarations and archived records."""

import ast
import csv
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

MAPPING_VERSION = "schema-crosswalk-v1"
COLUMNS = (
    "entity_kind", "v2_table_or_type", "v2_field", "v2_schema_version", "v2_type",
    "v2_null_semantics", "v3_table_or_type", "v3_field", "v3_type", "mapping_rule",
    "transform_version", "lossless", "comparison_rule", "reversibility",
    "evidence_required", "status", "reason",
)
STATES = {"MAPPED", "PRESERVED", "DEFERRED", "LOSSY_OR_UNRESOLVED"}
RAW_SCHEMA_VERSION = "observation-v3"
RAW_ID_VERSION = "observation-v1"


def repository_file(relative_path):
    checkout = Path(__file__).resolve().parents[3] / relative_path
    if checkout.is_file():
        return checkout
    return Path(sys.prefix) / "share" / "au_ree_v3" / Path(relative_path).name


def load_spec(path=None):
    path = Path(path) if path else repository_file("config/migration/schema_crosswalk_v1.json")
    spec = json.loads(path.read_text(encoding="utf-8"))
    if spec["schema_version"] != MAPPING_VERSION:
        raise ValueError("Unsupported crosswalk schema")
    return spec


def digest(value):
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def observation_raw_id(record):
    """Reproduce the V2 raw binding independently of the published observation ID."""
    anchor = record["evidence"]
    return digest([
        RAW_ID_VERSION, record["observation_id"], anchor.get("sha256") or anchor.get("source_id"),
        anchor.get("page"), anchor.get("table"), anchor.get("row"), anchor.get("column"),
        anchor.get("bbox"), anchor.get("offset"), record["variable"],
        record["extractor_version"], record.get("extraction_fingerprint"),
    ])


def project_fields(fields, source_type, record):
    """Project fields documented as direct, with an exact-value round trip."""
    mapped = {}
    for row in fields:
        if row["v2_table_or_type"] != source_type or row["mapping_rule"] != "COPY":
            continue
        value = record[row["v2_field"]]
        if (value is None or value == "") and row["v2_null_semantics"] == "REQUIRED":
            raise ValueError(f"{source_type}.{row['v2_field']} cannot be null")
        if value is not None and row["v3_type"] == "text" and not isinstance(value, str):
            raise ValueError(f"{source_type}.{row['v2_field']} must preserve its original text")
        field = row["v3_field"]
        if field in mapped:
            raise ValueError(f"Crosswalk would merge fields into {row['v3_table_or_type']}.{field}")
        mapped[field] = value
    return mapped


def _frozen_git_blob(root, source):
    result = subprocess.run(
        ["git", "-C", str(root), "cat-file", "blob", f"{source['commit']}:{source['path']}"],
        capture_output=True, check=False, env=_offline_git_environment(),
    )
    if result.returncode:
        raise FileNotFoundError(source["path"])
    data = result.stdout
    git_blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if git_blob != source["git_blob"] or actual_sha256 != source["sha256"]:
        raise ValueError(f"Unexpected frozen Git blob or SHA-256 for {source['path']}")
    return data.decode("utf-8-sig")


def _offline_git_environment():
    return {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"}


def _observation_declarations(text):
    tree = ast.parse(text)
    versions = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"SCHEMA_VERSION", "RAW_ID_VERSION"}
    }
    classes = {
        node.name: {
            field.target.id: ast.unparse(field.annotation)
            for field in node.body
            if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name)
            and not ast.unparse(field.annotation).startswith("InitVar")
        }
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }
    classes["RawObservation"]["raw_id"] = "str"
    classes["RawObservation"]["schema_version"] = "str"
    return versions, classes


def _sqlite_declarations(text):
    tree = ast.parse(text)
    schema = next(
        ast.literal_eval(node.value) for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "SCHEMA"
    )
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(schema)
        return {
            table: {column[1]: column[2] for column in conn.execute(
                "SELECT * FROM pragma_table_info(?)", (table,)
            )}
            for (table,) in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")
        }
    finally:
        conn.close()


def _csv_records(text):
    reader = csv.DictReader(io.StringIO(text, newline=""))
    headers = reader.fieldnames or []
    if not headers or len(set(headers)) != len(headers):
        raise ValueError("CSV has missing or repeated headers")
    rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError("CSV data does not match column count")
    return headers, rows


def _field_differences(fields, declarations):
    differences = []
    by_type = {}
    for row in fields:
        by_type.setdefault(row["v2_table_or_type"], []).append(row)
    for record, declaration in declarations.items():
        if record not in by_type:
            differences.append({"record": record, "issue": "MISSING_RECORD_MAPPING"})
            continue
        declared = {row["v2_field"]: row["v2_type"] for row in by_type[record]}
        for field in sorted(declared.keys() | declaration.keys()):
            if declared.get(field) != declaration.get(field):
                differences.append({
                    "record": record, "field": field, "issue": "V2_SCHEMA_MISMATCH",
                    "crosswalk_type": declared.get(field), "actual_type": declaration.get(field),
                })
    return differences


def _check_contract(spec, csv_rows):
    fields = spec["fields"]
    if [dict(row) for row in csv_rows] != fields:
        raise ValueError("Human CSV crosswalk differs from the executable JSON")
    keys = [(row["v2_table_or_type"], row["v2_field"]) for row in fields]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate source field mappings")
    targets = [
        (row["v2_table_or_type"], row["v3_table_or_type"], row["v3_field"])
        for row in fields if row["mapping_rule"] == "COPY"
    ]
    if len(targets) != len(set(targets)):
        raise ValueError("Direct mappings would merge multiple V2 columns")
    for row in fields:
        if set(row) != set(COLUMNS) or row["status"] not in STATES:
            raise ValueError(f"Invalid crosswalk entry: {row.get('v2_field')}")
        if row["status"] == "LOSSY_OR_UNRESOLVED" and (
            row["lossless"] != "NO" or not row["reason"]
        ):
            raise ValueError("Unresolved mappings require a reason and loss flag")


def _census_round_trip(census, states, provenance):
    projects = [row["專案ID"] for row in census]
    if any(not project for project in projects) or len(projects) != len(set(projects)):
        raise ValueError("Census stable project IDs must be unique and nonempty")
    cells = {(row["專案ID"], key): value for row in census for key, value in row.items()}
    state_by_cell = {(row["project_id"], row["column"]): row for row in states}
    if len(states) != len(state_by_cell) or cells.keys() != state_by_cell.keys():
        raise ValueError("Census and cell-state keys lack a one-to-one mapping")
    counts = Counter()
    reasons = Counter()
    for key, value in cells.items():
        row = state_by_cell[key]
        value_state = row["value_state"]
        if (value != "") != (value_state == "PRESENT"):
            raise ValueError(f"Cell value disagrees with its source state: {key}")
        reason = row["status"]
        if (value_state == "PRESENT") != (reason == "VALUE_PRESENT"):
            raise ValueError(f"Cell's specific status contradicts its value state: {key}")
        state = value_state
        if value_state == "BLANK":
            state = "NOT_APPLICABLE" if reason.startswith("NOT_APPLICABLE_") else "APPLICABLE_MISSING"
        counts[state] += 1
        reasons[reason] += 1
    proof_values = {(row["project_id"], row["column"]): row["value"] for row in provenance}
    if len(proof_values) != len(provenance) or proof_values != cells:
        raise ValueError("Census provenance values or stable keys differ from the Census")
    return {"projects": len(projects), "cells": len(cells), "states": dict(counts),
            "specific_statuses": dict(reasons), "provenance_rows": len(provenance)}


def _observation_archive_round_trip(fields, rows):
    observations = set()
    tokens = 0
    for row in rows:
        mapped = project_fields(fields, "ResourceStatementExport", row)
        observation_id = mapped["observation_id"]
        if not observation_id or observation_id in observations:
            raise ValueError("Resource export contains missing or duplicate Observation IDs")
        observations.add(observation_id)
        if mapped["project_id"] != row["project_id"] or mapped["source_sha"] != row["source_sha256"]:
            raise ValueError(f"Observation identity or source changed: {observation_id}")
        if len(mapped["source_sha"]) != 64 or any(c not in "0123456789abcdef" for c in mapped["source_sha"]):
            raise ValueError(f"Resource export source lacks a SHA-256: {observation_id}")
        if json.loads(row["source_statement_key"])[:5] != [
            row["project_id"], row["source_sha256"], row["statement_id"], row["scope"], row["effective_date"]
        ]:
            raise ValueError(f"Resource statement context changed: {observation_id}")
        if mapped["raw_value"] != row["original_token"] or mapped["raw_unit"] != row["original_unit"]:
            raise ValueError(f"Resource export token or unit changed: {observation_id}")
        tokens += bool(mapped["raw_value"])
    return {"observation_ids": len(observations), "original_token_strings": tokens}


def verify_crosswalk(v2_root, out, *, spec_path=None, csv_path=None):
    """Verify source coverage and exact cell/observation keys without running V2."""
    spec = load_spec(spec_path)
    csv_path = Path(csv_path) if csv_path else repository_file("docs/migration/schema_crosswalk.csv")
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": MAPPING_VERSION,
        "status": "PASS",
        "sources": [],
        "mapping_status_counts": dict(Counter(row["status"] for row in spec["fields"])),
        "differences": [],
    }
    documents = {}
    declarations = {}
    archives = {}
    try:
        with csv_path.open(encoding="utf-8", newline="") as src:
            _check_contract(spec, list(csv.DictReader(src)))
        for source in spec["sources"]:
            detail = {**source, "status": "PASS"}
            try:
                documents[source["record"]] = _frozen_git_blob(v2_root, source)
            except FileNotFoundError:
                detail["status"] = "BLOCKED"
            except ValueError as exc:
                detail.update(status="FAIL", reason=str(exc))
            receipt["sources"].append(detail)
        for source in spec["sources"]:
            record = source["record"]
            if record not in documents:
                continue
            content = documents[record]
            if source["kind"] == "sqlite":
                declarations.update(_sqlite_declarations(content))
            elif source["kind"] == "python":
                versions, classes = _observation_declarations(content)
                if versions != {"SCHEMA_VERSION": RAW_SCHEMA_VERSION, "RAW_ID_VERSION": RAW_ID_VERSION}:
                    raise ValueError("Frozen V2 observation schema or raw ID version differs")
                declarations.update(classes)
            elif source["kind"] == "json":
                keys = json.loads(content)
                declarations[record] = {key: "JSON" for key in keys}
            elif source["kind"] == "csv":
                headers, rows = _csv_records(content)
                declarations[record] = dict.fromkeys(headers, "CSV_TEXT")
                archives[record] = rows
        receipt["differences"] = _field_differences(spec["fields"], declarations)
        if {"CensusExport", "CensusStatus", "CensusProvenance"} <= archives.keys():
            receipt["census_round_trip"] = _census_round_trip(
                archives["CensusExport"], archives["CensusStatus"], archives["CensusProvenance"]
            )
        if "ResourceStatementExport" in archives:
            receipt["observation_round_trip"] = _observation_archive_round_trip(
                spec["fields"], archives["ResourceStatementExport"]
            )
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, SyntaxError) as exc:
        receipt["differences"].append({"issue": "CROSSWALK_CONTRACT_ERROR", "reason": str(exc)})
    if receipt["differences"] or any(row["status"] == "FAIL" for row in receipt["sources"]):
        receipt["status"] = "FAIL"
    elif any(row["status"] == "BLOCKED" for row in receipt["sources"]):
        receipt["status"] = "BLOCKED"
    (out / "schema_diff.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt
