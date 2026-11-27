"""Versioned Census column contracts and attributable project display metadata."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from .contracts import ContractViolation, canonical_json
from .registry import load_census_columns, load_profile, load_registry

POLICY_FILENAME = "census_projection_policy_v1.json"
POLICY_SCHEMA = "census-projection-policy-v1"
POLICY_VERSION = "census-projection-v1"
SHA256 = re.compile(r"[0-9a-f]{64}")
_COLUMN_ID = re.compile(r"[a-z][a-z0-9_]*")
_POLICY_KEYS = {
    "column_id", "display_name", "display_name_en", "data_type", "display_unit", "data_role",
    "source_priority", "input_contract", "projection_method", "required_evidence", "temporal_role",
    "scope_requirements", "null_policy", "applicability_rule", "validation_rule", "display_group",
    "display_precision", "provenance_required", "source_edition_required", "mcda_use", "criterion_links",
}
_REGISTRY_FIELDS = {"developer": "developer", "project_name": "name", "state": "state", "asx_code": "listing_code"}
_METADATA_METHODS = {
    "registry_snapshot", "registry_state_mapping", "profile_country_rule", "verified_source_manifest",
    "bounded_metadata_summary",
}
METADATA_ORIGINS = {"IDENTITY", "REGISTRY_SNAPSHOT", "REGISTRY_MAPPING", "SOURCE_EVIDENCE", "METADATA_SUMMARY"}


@dataclass(frozen=True, slots=True)
class CensusProjectionPolicy:
    version: str
    sha256: str
    rows: tuple[dict, ...]
    country: dict
    state_labels: dict[str, str]

    @property
    def by_name(self) -> dict[str, dict]:
        return {row["display_name"]: row for row in self.rows}


@dataclass(frozen=True, slots=True)
class CensusMetadata:
    policy: CensusProjectionPolicy
    profile_sha256: str
    registry_sha256: str
    cells: dict[tuple[str, str], dict]
    source_reasons: dict[str, str]


def load_projection_policy(columns: tuple[str, ...] | list[str]) -> CensusProjectionPolicy:
    """Load the installed contract and verify each role against the fixed ordered headings."""
    payload = files(__package__).joinpath(POLICY_FILENAME).read_bytes()
    document = json.loads(payload)
    rows = tuple(document["columns"])
    if document["schema_version"] != POLICY_SCHEMA or document["projection_policy_version"] != POLICY_VERSION:
        raise ContractViolation("unsupported Census projection policy")
    if len(rows) != 51 or tuple(row["display_name"] for row in rows) != tuple(columns):
        raise ContractViolation("Census projection policy headings differ from the pinned axes")
    identities = [row["column_id"] for row in rows]
    if len(set(identities)) != len(identities) or any(not _COLUMN_ID.fullmatch(value) for value in identities):
        raise ContractViolation("Census projection requires stable unique column IDs")
    for row in rows:
        if (set(row) != _POLICY_KEYS or row["data_type"] not in {"text", "decimal", "integer", "boolean_text", "https_url"}
            or row["null_policy"] != "UNKNOWN_WITH_REASON_NO_IMPUTATION" or row["provenance_required"] is not True
            or row["mcda_use"] is not False):
            raise ContractViolation(f"invalid projection definition for {row['column_id']}")
    return CensusProjectionPolicy(
        POLICY_VERSION, sha256(payload).hexdigest(), rows, document["cohort_country"], document["state_labels"],
    )


def public_source_url(value) -> str | None:
    if isinstance(value, str) and not any(char == "\\" or ord(char) <= 32 or ord(char) == 127 for char in value):
        try:
            url = urlsplit(value)
            if url.scheme == "https" and url.hostname and not url.username and not url.password:
                return value
        except ValueError:
            pass
    return None


def _registry_metadata(project, policy, registry_sha, snapshot_cutoff) -> dict[str, dict]:
    cells = {}

    def write(key, value, native_field, native_value, *, assertion, origin="REGISTRY_SNAPSHOT"):
        if value is None or value == "":
            return
        evidence = {
            "evidence_type": "REGISTRY_SNAPSHOT", "registry_sha256": registry_sha,
            "snapshot_cutoff": snapshot_cutoff, "native_field": native_field, "native_value": native_value,
            "assertion": assertion, "display_only": True,
        }
        if origin == "REGISTRY_MAPPING":
            evidence["mapping_sha256"] = policy.sha256
        cells[key] = {
            "value": value, "value_state": "PRESENT", "value_origin": origin, "reason": assertion,
            "registry_sha256": registry_sha, "temporal_status": "REGISTRY_SNAPSHOT",
            "evidence_state": "DESCRIPTIVE_ONLY", "metadata_evidence": canonical_json(evidence),
        }

    for column_id, field in _REGISTRY_FIELDS.items():
        value = getattr(project, field)
        assertion = "HISTORICAL_LISTING_CODE_ONLY" if column_id == "asx_code" else "REGISTRY_SNAPSHOT"
        write(column_id, value, field, value, assertion=assertion)
    write("project_id", project.project_id, "project_id", project.project_id, assertion="REGISTRY_IDENTITY", origin="IDENTITY")
    if project.state in policy.state_labels:
        for key, value in (("location_zh", project.state), ("location_en", policy.state_labels[project.state])):
            write(key, value, "state", project.state, assertion="ADMINISTRATIVE_REGION_ONLY", origin="REGISTRY_MAPPING")
    write(
        "country", policy.country["display"], "profile_country_rule", policy.country["native"],
        assertion=policy.country["rule"], origin="REGISTRY_MAPPING",
    )
    return cells


def metadata_projection(definition: dict) -> bool:
    return definition["projection_method"] in _METADATA_METHODS


def _source_metadata(manifest, manifest_sha, source_receipt) -> tuple[dict[str, dict], dict[str, str]]:
    if manifest is None or source_receipt is None:
        return {}, {}
    if not (isinstance(manifest_sha, str) and SHA256.fullmatch(manifest_sha)):
        raise ContractViolation("verified source display metadata requires the exact manifest SHA")
    verified = set(source_receipt["verified_capture_sha256"])
    events = {row["receipt_sha256"]: row for row in manifest["events"] if row["status"] == "PUBLIC_CAPTURED"}
    choices = defaultdict(list)
    reasons = {}
    noneligible = {
        "CANDIDATE": (1, "EVIDENCE_INSUFFICIENT"), "INELIGIBLE": (2, "TEMPORAL_INELIGIBLE"),
        "CONFLICT": (3, "SCIENTIFIC_CONFLICT"),
    }
    for edition in manifest["editions"]:
        receipt_sha = edition["receipt_sha256"]
        if receipt_sha not in verified:
            continue
        event = events[receipt_sha]
        decision = edition["temporal_decision"]
        if (event["sha256"], event["project_ids"]) != (edition["sha256"], edition["project_ids"]):
            raise ContractViolation("source display metadata requires matching capture provenance")
        for project_id in edition["project_ids"]:
            if decision["status"] != "ELIGIBLE":
                reasons[project_id] = max(reasons.get(project_id, (0, "")), noneligible[decision["status"]])
                continue
            url = public_source_url(event["requested_url"])
            publisher = event.get("publisher")
            choices[project_id].append((edition, url, publisher if isinstance(publisher, str) else None))
    results = {}
    for project_id, captures in choices.items():
        edition, public_url, publisher = min(captures, key=lambda item: (
            not bool(item[1]), not bool(item[2]), item[0]["kind"] != "pdf", item[1] or "",
            item[0]["sha256"], item[0]["receipt_sha256"],
        ))
        decision = edition["temporal_decision"]
        source_count = len({item[0]["sha256"] for item in captures})
        evidence = {
            "evidence_type": "PROJECT_SOURCE_DISPLAY", "source_manifest_sha256": manifest_sha,
            "source_capture_sha256": edition["receipt_sha256"], "source_sha256": edition["sha256"],
            "temporal_decision_id": decision["decision_id"], "verified_distinct_source_count": source_count,
            "scope": "PROJECT_REFERENCE_ONLY", "display_only": True,
        }
        common = {
            "value_state": "PRESENT", "value_origin": "SOURCE_EVIDENCE", "reason": "VERIFIED_PROJECT_SOURCE",
            "source_sha256": edition["sha256"], "source_key": edition["source_key"], "source_url": public_url,
            "temporal_status": "ELIGIBLE", "temporal_policy_version": decision["policy_version"],
            "temporal_decision_id": decision["decision_id"],
            "temporal_evidence_ids": canonical_json(decision["matching_evidence_ids"]),
            "evidence_state": "DESCRIPTIVE_ONLY", "metadata_evidence": canonical_json(evidence),
        }
        results[project_id] = {
            key: {**common, "value": value} for key, value in (
                ("has_source_document", "是"), ("source_url", public_url), ("source_name", publisher),
            ) if isinstance(value, str) and value.strip()
        }
        reasons.pop(project_id, None)
    return results, {project_id: reason for project_id, (_, reason) in reasons.items()}


def prepare_census_metadata(
    profile: Path, source_manifest: dict | None = None, source_receipt: dict | None = None,
    *, source_manifest_sha256: str | None = None,
) -> CensusMetadata:
    """Project Registry snapshot display and already verified exact source captures."""
    settings = load_profile(profile)
    registry = load_registry(settings.registry_path)
    policy = load_projection_policy(load_census_columns(settings.census_columns_path))
    snapshot = json.loads(settings.registry_path.read_bytes()).get("historical_reference", {}).get("historical_cutoff")
    sources, reasons = _source_metadata(source_manifest, source_manifest_sha256, source_receipt)
    headings = {row["column_id"]: row["display_name"] for row in policy.rows}
    cells = {}
    for project in registry.projects:
        projected = _registry_metadata(project, policy, registry.sha256, snapshot)
        project_sources = sources.get(project.project_id, {})
        projected.update(project_sources)
        evidence = {
            "evidence_type": "BOUNDED_METADATA_SUMMARY", "registry_sha256": registry.sha256,
            "snapshot_cutoff": snapshot, "native_field": "state", "native_value": project.state,
            "has_verified_source": bool(project_sources), "display_only": True,
        }
        note = f"登錄快照，行政區域為{project.state}。"
        if project_sources:
            evidence.update(json.loads(project_sources["has_source_document"]["metadata_evidence"]))
            evidence["evidence_type"] = "BOUNDED_METADATA_SUMMARY"
            note += f"核對 {evidence['verified_distinct_source_count']} 份來源檔案。"
        projected["overview_note"] = {
            "value": note, "value_state": "PRESENT", "value_origin": "METADATA_SUMMARY",
            "reason": "BOUNDED_METADATA_SUMMARY", "registry_sha256": registry.sha256,
            "temporal_status": "REGISTRY_SNAPSHOT", "evidence_state": "DESCRIPTIVE_ONLY",
            "metadata_evidence": canonical_json(evidence),
        }
        cells.update({(project.project_id, headings[key]): value for key, value in projected.items()})
    return CensusMetadata(policy, settings.sha256, registry.sha256, cells, reasons)


def _coverage(present: int, denominator: int) -> dict:
    return {
        "present": present, "denominator": denominator,
        "rate": format(Decimal(present) / denominator, ".6f") if denominator else None,
    }


def coverage_report(cells: list[dict], policy: CensusProjectionPolicy) -> dict:
    present = [cell for cell in cells if cell["value_state"] == "PRESENT"]
    direct = [cell for cell in present if cell["evidence_state"] == "FORMAL_DIRECT"]
    derived = [cell for cell in present if cell["evidence_state"] == "FORMAL_DERIVED"]
    supported = {column["display_name"] for column in policy.rows if metadata_projection(column)}
    metadata = [cell for cell in present if cell["value_origin"] in METADATA_ORIGINS]
    states = defaultdict(Counter)
    reasons = defaultdict(Counter)
    for cell in cells:
        states[cell["column"]][cell["value_state"]] += 1
        if cell["value_state"] == "UNKNOWN":
            reasons[cell["column"]][cell["reason"]] += 1
    applicable = {}
    for column in policy.rows:
        if column["data_role"] not in {"FORMAL_OBSERVATION", "FORMAL_DERIVED"}:
            continue
        key = column["display_name"]
        counts = states[key]
        applicable[column["column_id"]] = {
            "present": counts["PRESENT"], "unknown": counts["UNKNOWN"],
            "not_applicable_with_evidence": counts["NOT_APPLICABLE"],
            "demonstrated_applicable_denominator": counts["PRESENT"],
            "applicability_undetermined": counts["UNKNOWN"],
            "unexcluded_grid_denominator": counts["PRESENT"] + counts["UNKNOWN"],
            "unknown_reasons": dict(reasons[key]),
            "applicability_rule": column["applicability_rule"],
        }
    return {
        "raw": _coverage(len(present), len(cells)),
        "metadata": _coverage(len(metadata), sum(cell["column"] in supported for cell in cells)),
        "origins": dict(Counter(cell["value_origin"] for cell in present)),
        "scientific": {
            "direct_cells": len(direct), "unique_direct_observations": len({cell["observation_id"] for cell in direct}),
            "derived_cells": len(derived), "unique_derived_values": len({cell["lineage_id"] for cell in derived}),
            "by_column": applicable,
        },
    }
