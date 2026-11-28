"""Versioned, read-only Census display facts and their lossless evidence joins."""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path

from .census_metadata import public_source_url
from .contracts import canonical_json
from .registry import PROJECT_ID, PROJECT_ID_COLUMN, SHA256

SCHEMA = "census-display-columns-v2"
CONTRACT_SHA256 = "3cdc2e496fad9df321ca3e4cc8ff91a8734f6ea04b8f31dad7eda27e68e4f13e"
WEB_SCHEMA = "web-census-display-v2"
DISPLAY_USE = "CENSUS_DISPLAY_ONLY"
DISPLAY_LABEL = "SOURCE_REPORTED_DISPLAY"
FACT_ARTIFACT = "census_display_facts.jsonl"
WEB_ARTIFACTS = ("census_display.json", "census_display_extended.csv")
ROLES = frozenset({"REPORTED_RESOURCE", "COMPLETED_STUDY", "FORECAST", "ENGINEERING_PLAN"})
REVIEW_STATES = frozenset({"CANDIDATE", "SHADOW", "SCIENTIFIC_CONFLICT", "EVIDENCE_INSUFFICIENT"})
_FIELD_KEYS = frozenset({
    "field_id", "label_zh", "label_en", "group", "data_type", "unit", "basis", "purpose", "source_priority",
    "applicability", "missing_semantics", "validation_rule", "precision", "display_label", "evidence_roles", "version",
})
_FIELD_ID = re.compile(r"[a-z][a-z0-9_]+")
_SAFE_GROUPS = frozenset({"resource", "study", "forecast", "geology", "location"})
_NUMBER = re.compile(r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
_CONTEXT_KEY = re.compile(r"[a-z][a-z0-9_]+")
_REVIEW_KEYS = frozenset({
    "project_id", "data_role", "state", "label", "reason", "source_sha256", "source_capture_sha256", "source_url",
    "publication_date", "locator", "quote", "scope", "context",
})


@dataclass(frozen=True, slots=True)
class DisplayFact:
    """An exact published statement eligible for Census display as reported."""

    project_id: str
    field_id: str
    value: str
    raw_token: str
    raw_unit: str
    source_sha256: str
    source_capture_sha256: str
    source_url: str
    publication_date: str
    locator: str
    quote: str
    scope: str
    subject_id: str
    data_role: str
    context: dict[str, str]

    def as_record(self) -> dict:
        return asdict(self)

    @property
    def fact_id(self) -> str:
        return _hash(self.as_record())


def _hash(value) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _sha(value) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _text(value, *, limit: int = 4000) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit and all(
        char in "\n\t" or char.isprintable() for char in value
    )


def _safe_value(value) -> bool:
    return _text(value) and (value.lstrip()[0] not in "=+-@" or _NUMBER.fullmatch(value) is not None)


def _calendar(value) -> bool:
    if not isinstance(value, str) or re.fullmatch(r"\d{4}(?:-\d{2}(?:-\d{2})?)?", value) is None:
        return False
    try:
        date.fromisoformat(value + {4: "-01-01", 7: "-01", 10: ""}[len(value)])
    except ValueError:
        return False
    return True


def _context(value) -> bool:
    return isinstance(value, dict) and 0 < len(value) <= 40 and all(
        isinstance(key, str) and _CONTEXT_KEY.fullmatch(key) and _text(item) for key, item in value.items()
    )


def _evidence(record, *, review: bool = False) -> bool:
    publication = record["publication_date"]
    context = record["context"]
    if not _context(context):
        return False
    captured_date = ""
    if review and publication == "":
        if context.get("feature_effective_date") == "UNKNOWN_CONTEXT":
            captured_date = context.get("capture_date", "")
        if (record["data_role"] == "REPORTED_RESOURCE"
                and context.get("display_rule") == "as-reported-resource-review-v1"
                and context.get("publication_date_status") == "UNKNOWN_CONTEXT"):
            captured_date = context.get("first_proven_public_date", "")
    known_date = _calendar(publication) or (len(captured_date) == 10 and _calendar(captured_date))
    return (all(_text(record[key]) for key in ("locator", "quote", "scope"))
            and all(_sha(record[key]) for key in ("source_sha256", "source_capture_sha256"))
            and public_source_url(record["source_url"]) is not None
            and known_date)


def _valid_field(field: dict) -> bool:
    return (type(field) is dict and set(field) == _FIELD_KEYS
            and all(_text(field[key]) for key in _FIELD_KEYS - {"evidence_roles"})
            and _FIELD_ID.fullmatch(field["field_id"]) is not None
            and field["group"] in _SAFE_GROUPS and field["data_type"] in {"TEXT", "PARTIAL_DATE", "REPORTED_QUANTITY"}
            and field["purpose"] == DISPLAY_USE and field["display_label"] == DISPLAY_LABEL
            and field["version"] == "v2" and isinstance(field["evidence_roles"], list)
            and bool(field["evidence_roles"])
            and all(isinstance(role, str) and role in ROLES for role in field["evidence_roles"])
            and len(set(field["evidence_roles"])) == len(field["evidence_roles"]))


def _fields(columns, *, core_columns: tuple[str, ...] = ()) -> dict[str, dict]:
    if not isinstance(columns, list) or not columns or not all(_valid_field(field) for field in columns):
        raise ValueError("Invalid Census display field definition")
    fields = {field["field_id"]: field for field in columns}
    overlap = set(fields) & set(core_columns) or any(field["label_zh"] in core_columns for field in columns)
    if len(fields) != len(columns) or overlap:
        raise ValueError("Census display contract duplicates an existing field")
    if _hash({"schema_version": SCHEMA, "columns": columns}) != CONTRACT_SHA256:
        raise ValueError("Census display columns differ from the registered version")
    return fields


def load_display_columns(path: Path, *, core_columns: tuple[str, ...] = ()) -> list[dict]:
    doc = json.loads(path.read_bytes())
    if set(doc) != {"schema_version", "columns"} or doc["schema_version"] != SCHEMA:
        raise ValueError("Invalid Census display column contract")
    _fields(doc["columns"], core_columns=core_columns)
    return doc["columns"]


def _validate_fact(fact: DisplayFact, field: dict) -> None:
    value = fact.value
    context = fact.context
    if (
        fact.data_role not in field["evidence_roles"] or not _safe_value(value)
        or not _text(fact.raw_token) or not isinstance(fact.raw_unit, str) or len(fact.raw_unit) > 80
        or (fact.raw_unit and not _text(fact.raw_unit))
        or not _text(fact.subject_id) or not _evidence(fact.as_record())
        or context.get("as_reported_at") != fact.publication_date or not _text(context.get("source_kind"))
        or not (_sha(context.get("temporal_decision_id")) or context.get("temporal_status") == "ELIGIBLE")
        or context.get("temporal_status", "ELIGIBLE") != "ELIGIBLE"
        or field["data_type"] == "PARTIAL_DATE" and not _calendar(value)
        or field["data_type"] == "REPORTED_QUANTITY" and (_NUMBER.fullmatch(fact.raw_token) is None or not fact.raw_unit)
    ):
        raise ValueError("Unverified or inapplicable Census display fact")


def _project_rows(project_ids, field_ids, facts) -> tuple[list, dict]:
    by_cell = {}
    for identity, fact in sorted(facts.items(), key=lambda item: (
        item[1]["field_id"], item[1]["publication_date"], item[1]["subject_id"], item[1]["data_role"], item[0],
    )):
        by_cell.setdefault((fact["project_id"], fact["field_id"]), []).append(identity)
    rows = []
    coverage = {field_id: {"present": 0, "unknown": 0, "not_applicable": 0} for field_id in field_ids}
    for project_id in project_ids:
        cells = []
        for field_id in field_ids:
            links = by_cell.get((project_id, field_id), [])
            if links:
                cell = {"state": "PRESENT", "value": " | ".join(facts[link]["value"] for link in links), "fact_ids": links}
                coverage[field_id]["present"] += 1
            else:
                cell = {"state": "UNKNOWN", "value": None, "fact_ids": []}
                coverage[field_id]["unknown"] += 1
            cells.append(cell)
        rows.append({"id": project_id, "cells": cells})
    return rows, coverage


def display_document(*, release_id: str, columns: list[dict], project_ids: tuple[str, ...],
                     facts: tuple[DisplayFact, ...], review_only: tuple[dict, ...] = ()) -> dict:
    fields = _fields(columns)
    if not _sha(release_id) or not project_ids or len(project_ids) != len(set(project_ids)) or not all(
        PROJECT_ID.fullmatch(project) for project in project_ids
    ):
        raise ValueError("Invalid Census display release or Project IDs")
    projects = set(project_ids)
    records = {}
    by_subject = set()
    for fact in facts:
        field = fields.get(fact.field_id)
        if field is None or fact.project_id not in projects:
            raise ValueError("Display fact lies outside its column or Project registry")
        _validate_fact(fact, field)
        identity = (fact.project_id, fact.field_id, fact.subject_id, fact.source_sha256, fact.data_role)
        if identity in by_subject:
            raise ValueError("Ambiguous duplicate Census display statement")
        by_subject.add(identity)
        records[fact.fact_id] = fact.as_record()
    for record in review_only:
        if (not isinstance(record, dict) or set(record) != _REVIEW_KEYS or record["project_id"] not in projects
                or record["state"] not in REVIEW_STATES or not all(_text(record[key]) for key in ("data_role", "label", "reason"))
                or not _evidence(record, review=True)):
            raise ValueError("Unverified review-only Census evidence")
    review = sorted(review_only, key=lambda item: (item["project_id"], item["state"], _hash(item)))
    if len({_hash(entry) for entry in review}) != len(review):
        raise ValueError("Duplicate review-only Census evidence")
    rows, coverage = _project_rows(project_ids, fields, records)
    return {
        "schema_version": WEB_SCHEMA, "release_id": release_id, "columns": columns, "projects": rows, "facts": records,
        "coverage": coverage, "review_only": review, "contract_sha256": CONTRACT_SHA256,
    }


def display_csv(document: dict) -> bytes:
    sink = io.StringIO(newline="")
    writer = csv.writer(sink, lineterminator="\r\n")
    writer.writerow([PROJECT_ID_COLUMN, *(field["field_id"] for field in document["columns"])])
    for row in document["projects"]:
        writer.writerow([row["id"], *(cell["value"] for cell in row["cells"])])
    return sink.getvalue().encode("utf-8")


def verify_display_document(document: dict, csv_payload: bytes, *, release_id: str, project_ids: tuple[str, ...],
                            core_columns: tuple[str, ...] = ()) -> dict:
    """Reconstruct every column, cell, fact identity, count, and CSV record."""
    try:
        _fields(document["columns"], core_columns=core_columns)
        expected = display_document(
            release_id=release_id, columns=document["columns"], project_ids=project_ids,
            facts=tuple(DisplayFact(**record) for record in document["facts"].values()),
            review_only=tuple(document["review_only"]),
        )
        if document != expected or display_csv(expected) != csv_payload:
            raise ValueError("Census display matrix, evidence or CSV differs from its verified source records")
    except (AttributeError, KeyError, TypeError) as error:
        raise ValueError("Invalid Census display document") from error
    return expected
