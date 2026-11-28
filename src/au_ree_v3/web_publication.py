"""Export and verify immutable, browser-safe views of one V3 research build."""

from __future__ import annotations

import csv
import ipaddress
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit

from .census_metadata import METADATA_ORIGINS, load_projection_policy
from .contracts import canonical_json
from .fresh_research import BUILD_SCHEMA
from .observations import OXIDE_VARIABLES
from .publication import load_release
from .registry import PROJECT_ID, PROJECT_ID_COLUMN, SHA256, load_census_columns, load_profile, load_registry
from .research_artifacts import ARTIFACTS

if TYPE_CHECKING:
    from .publication import FormalPublicationAuthorization

MANIFEST_SCHEMA = "web-research-manifest-v1"
CANDIDATE = "RESEARCH_CANDIDATE"
FORMAL = "FORMAL_CURRENT"
_SCHEMAS = {
    "census.json": "web-census-v1",
    "census_evidence.json": "web-evidence-v1",
    "coverage.json": "web-coverage-v1",
    "mcda.json": "web-mcda-v1",
    "gis.geojson": "web-gis-v1",
}
FILES = (*_SCHEMAS, "census.csv")
_STATES = ("PRESENT", "UNKNOWN", "NOT_APPLICABLE")
_COMPOSITION = {*OXIDE_VARIABLES.values(), "mreo_ppm"}
_COORDINATES = ("經度", "緯度")
_POINT_ROLES = {"VERIFIED_MINE_POINT", "VERIFIED_DEPOSIT_POINT"}
_URL_HEADING = "資料來源URL"
_RESOURCE_REFERENCE_HEADING = "有TREO資源估計"
_RESOURCE_MEASUREMENT_HEADINGS = {"礦石蘊藏量_Mt", "TREO品位_pct", "總TREO蘊藏量_kt"}
_URL_QUERY_KEYS = {"announcementid", "asxcode", "documentid", "file", "headlineid", "id", "inline", "page", "pdf", "v", "version"}
_INTERNAL_PATH = re.compile(r"(?:file://|(?:^|[\s'\"])(?:/(?:tmp|workspace|home|root|mnt)/|[A-Za-z]:[\\/]))", re.I)
_CREDENTIAL = re.compile(r"(?i)(?:bearer\s+[A-Za-z0-9._~+/=-]+|(?:api[_-]?key|secret|password|token)\s*[:=]\s*\S+)")
_EVIDENCE_FIELDS = {
    "anchor_id", "assessment_id", "candidate_id", "category", "chemical_basis", "crs", "data_role", "denominator_basis",
    "derived_value_id", "effective_date", "evidence_state", "formula", "formula_id", "formula_sha256", "geometry_role", "locator",
    "material_basis", "observation_id", "projection_policy_sha256", "projection_policy_version",
    "formula_registry_sha256", "publication_date", "raw_record_id", "raw_unit", "raw_value", "reason", "registry_sha256", "source_capture_sha256",
    "rules_sha256", "scope", "scope_id", "selection_id", "source_column", "source_row", "source_search_status",
    "source_sha256", "source_table", "source_url", "statement_id", "temporal_decision_id",
    "temporal_policy_version", "temporal_status", "unit", "validation_id",
}
_METADATA_FIELDS = {
    "assertion", "display_only", "evidence_type", "has_verified_source", "mapping_sha256",
    "native_field", "native_value", "registry_sha256", "scope", "verified_distinct_source_count",
    "snapshot_cutoff", "source_capture_sha256", "source_manifest_sha256", "source_sha256",
    "temporal_status", "temporal_decision_id", "geometry_role",
    "attribute_kind", "attribute_role", "policy_sha256", "publication_date", "event_date_token",
}
_CONTEXT_FIELDS = {
    "category", "chemical_basis", "cutoff_analyte", "cutoff_grade", "cutoff_unit", "denominator_basis",
    "effective_date", "material_basis", "quantity", "reported_chemical_label", "reporting_standard",
    "scope_id", "source_sha256", "statement_id",
}
_PRECISION_FIELDS = {"decimal_places", "raw_value", "raw_unit", "unit", "mode", "token", "rounding_half_width", "lower", "upper"}


class WebPublicationError(ValueError):
    """The build or its public projection does not meet the release contract."""


def _digest(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _encode(value) -> bytes:
    return (canonical_json(value) + "\n").encode("utf-8")


def _pairs(items):
    result = dict(items)
    if len(result) != len(items):
        raise WebPublicationError("JSON contains duplicate fields")
    return result


def _read_json(payload: bytes, *, canonical: bool = False):
    value = json.loads(payload, object_pairs_hook=_pairs)
    if not isinstance(value, dict) or (canonical and _encode(value) != payload):
        raise WebPublicationError("Invalid or noncanonical publication JSON")
    # Canonical serialization also excludes NaN and Infinity in input numbers.
    _encode(value)
    return value


def _sha(value) -> bool:
    return isinstance(value, str) and SHA256.fullmatch(value) is not None


def _file(directory: Path, name: str) -> bytes:
    child = directory / name
    if child.is_symlink() or not child.is_file() or not child.resolve().is_relative_to(directory.resolve()):
        raise WebPublicationError(f"Missing or unsafe artifact: {name}")
    return child.read_bytes()


def _safe_text(value):
    return isinstance(value, str) and not _INTERNAL_PATH.search(value) and not _CREDENTIAL.search(value)


def _public_url(value) -> bool:
    if not _safe_text(value) or not value or any(ord(character) < 33 for character in value) or "\\" in value:
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not host or "." not in host or parsed.username is not None
                or parsed.password is not None or (parsed.port is not None and parsed.port not in {80, 443})
                or host.lower().endswith((".local", ".localhost", ".internal", ".example", ".test", ".invalid"))
                or any(key.lower() not in _URL_QUERY_KEYS or not _safe_text(text)
                       for key, text in parse_qsl(parsed.query, keep_blank_values=True))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return all(re.fullmatch(r"[A-Za-z0-9-]+", part) for part in host.split("."))
    except ValueError:
        return False


def _scalar_fields(row: dict, keys: set[str]) -> dict:
    return {
        key: value for key, value in row.items() if key in keys and value not in (None, "")
        and (type(value) in {bool, int, float} and (not isinstance(value, float) or math.isfinite(value))
             or _safe_text(value))
        and (key != "source_url" or _public_url(value))
    }


def _json_field(row, key):
    value = row.get(key)
    return json.loads(value, object_pairs_hook=_pairs) if isinstance(value, str) and value else value


def _id_list(value) -> list[str]:
    return list(value) if isinstance(value, (list, tuple)) and all(_sha(item) for item in value) else []


def _scaled_number(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[Ee][+-]?[0-9]{1,4})?", value):
        return False
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def _public_value(heading, value):
    return _safe_text(value) and (
        _public_url(value) if heading == _URL_HEADING and value else
        not value.lstrip().startswith(("=", "+", "-", "@")) or _scaled_number(value)
    )


def _csv(payload: bytes):
    reader = csv.DictReader(StringIO(payload.decode("utf-8"), newline=""))
    rows = list(reader)
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames) or any(
        None in row or None in row.values() for row in rows
    ):
        raise WebPublicationError("Census CSV has an invalid shape")
    return tuple(reader.fieldnames), rows


def _evidence(row: dict) -> dict:
    if row.get("evidence_state") == "VALIDATED_DESCRIPTIVE":
        from .project_attributes import validate_attribute_cell

        validate_attribute_cell(row)
    evidence = _scalar_fields(row, _EVIDENCE_FIELDS)
    evidence["reason"] = evidence.get("reason") or "EVIDENCE_NOT_DISCLOSED"
    origin = row.get("value_origin")
    evidence.setdefault("evidence_state", {
        "IDENTITY": "REGISTRY_SNAPSHOT", "FORMAL_SELECTION": "FORMAL_DIRECT",
        "FORMAL_DIRECT": "FORMAL_DIRECT", "FORMAL_DERIVED": "FORMAL_DERIVED",
    }.get(origin, "EVIDENCE_INSUFFICIENT"))
    if row.get("physical_page"):
        page = int(row["physical_page"])
        if page < 1:
            raise WebPublicationError("Evidence physical page must be positive")
        evidence["physical_page"] = page
    metadata = _json_field(row, "metadata_evidence")
    if isinstance(metadata, dict) and (public := _scalar_fields(metadata, _METADATA_FIELDS)):
        evidence["metadata"] = public
    if origin == "FORMAL_DERIVED" and isinstance(metadata, dict):
        evidence.update(_scalar_fields(metadata, {"derived_value_id", "assessment_id", "formula", "formula_registry_sha256"}))
    for field in ("input_observation_ids", "temporal_evidence_ids"):
        if ids := _id_list(_json_field(row, field)):
            evidence[field] = ids
    return evidence


def _precision(row: dict) -> dict | None:
    precision = _json_field(row, "precision")
    selected = _scalar_fields(row, {"raw_value", "raw_unit", "unit"})
    if isinstance(precision, dict):
        selected = {**_scalar_fields(precision, _PRECISION_FIELDS), **selected}
        interval = precision.get("rounding_interval")
        if isinstance(interval, list) and len(interval) == 2 and all(_scaled_number(item) for item in interval):
            selected["rounding_interval"] = interval
    elif type(precision) is int or _scaled_number(precision):
        selected["decimal_places"] = int(Decimal(precision))
    return selected or None


def _column_view(headings) -> tuple[list[dict], str | None]:
    # Older releases use the same fixed headings and this versioned display policy.
    policy = load_projection_policy(headings)
    columns = [{
        "id": name, "column_id": rule["column_id"], "label": rule["display_name"],
        "label_en": rule["display_name_en"],
        "display_group": rule["display_group"], "data_type": rule["data_type"],
        "unit": rule["display_unit"],
    } for name, rule in zip(headings, policy.rows, strict=True)]
    return columns, policy.sha256


def _build_grid(headings, project_ids, census_payload, ledger_payload):
    csv_headings, rows = _csv(census_payload)
    ledger_headings, status = _csv(ledger_payload)
    if csv_headings != headings or tuple(row[PROJECT_ID_COLUMN] for row in rows) != project_ids:
        raise WebPublicationError("Census axes differ from the pinned Registry and column order")
    if not {"project_id", "column", "value", "value_state", "value_origin", "reason"} <= set(ledger_headings):
        raise WebPublicationError("Census cell ledger omits required entries")
    if len(status) != len(project_ids) * len(headings):
        raise WebPublicationError("Census and cell ledger dimensions differ")

    projects, evidence = [], {}
    for project_id, row in zip(project_ids, rows, strict=True):
        cells = []
        for index, heading in enumerate(headings):
            raw = status[len(evidence)]
            value, state, origin = raw["value"], raw["value_state"], raw["value_origin"]
            if (raw["project_id"], raw["column"], value) != (project_id, heading, row[heading]):
                raise WebPublicationError("Census cell value or identity differs from its ledger")
            if (
                state not in _STATES or (state == "PRESENT") != bool(value) or not _public_value(heading, value)
                or (state == "PRESENT" and not origin) or not raw["reason"] or not _safe_text(origin)
            ):
                raise WebPublicationError(f"Census has an invalid or nonpublic value for {project_id} / {heading}")
            lineage_id = f"{project_id}:{index}"
            evidence[lineage_id] = {"project_id": project_id, "column_id": heading, **_evidence(raw)}
            cells.append({
                "value": value if state == "PRESENT" else None, "state": state, "origin": origin or None,
                "precision": _precision(raw), "lineage_id": lineage_id,
            })
        projects.append({"id": project_id, "cells": cells})
    return projects, evidence


def _composition_view(payload, evidence, project_ids, decision_inputs):
    # The selected artifact contains the display measurements. The same exact
    # Project+edition Census evidence can supply the public URL for their PDF.
    urls = {
        (item["project_id"], item["source_sha256"]): item["source_url"]
        for item in evidence.values() if item.get("source_sha256") and item.get("source_url")
    }
    lineage = {}
    for project_id, cells in decision_inputs["criteria_by_project"].items():
        for row in cells.values():
            for item in row.get("lineage", ()):
                lineage[(project_id, item["observation_id"], item["source_sha256"])] = item
    output = defaultdict(list)
    for line in payload.splitlines():
        row = _read_json(line)
        variable, project_id = row.get("variable"), row.get("project_id")
        if variable not in _COMPOSITION or row.get("selected_for_census") is not True:
            continue
        if (row.get("outcome") != "VALIDATED" or row.get("selection_status") != "SELECTED"
                or project_id not in project_ids or not _sha(row.get("source_sha256"))
                or not _sha(row.get("observation_id")) or not _scaled_number(row.get("value"))):
            raise WebPublicationError("Composition display observation lacks governed selection lineage")
        lineage_id = f"observation:{row['observation_id']}"
        if lineage_id in evidence:
            raise WebPublicationError("Composition display observation has duplicate identity")
        bound = lineage.get((project_id, row["observation_id"], row["source_sha256"]), {})
        temporal = bound.get("temporal_decision") or {}
        selected = {
            **_scalar_fields(bound, {"anchor_id", "candidate_id", "selection_id", "validation_id"}),
            **row, "temporal_status": temporal.get("status"), "temporal_decision_id": temporal.get("decision_id"),
            "temporal_policy_version": temporal.get("policy_version"),
            "temporal_evidence_ids": temporal.get("matching_evidence_ids"), "source_url": urls.get((project_id, row["source_sha256"])),
            "physical_page": row.get("page"), "source_table": row.get("table"), "source_row": row.get("row"),
            "source_column": row.get("column"), "evidence_state": "FORMAL_DIRECT",
        }
        evidence[lineage_id] = {
            "project_id": project_id, "column_id": variable, **_evidence(selected),
        }
        output[project_id].append({
            **_scalar_fields(row, {"variable", "value", "unit", "chemical_basis", "material_basis", "denominator_basis"}),
            "lineage_id": lineage_id,
        })
    return {project_id: sorted(rows, key=lambda row: (row["variable"], row["lineage_id"]))
            for project_id, rows in sorted(output.items())}


def _derivation(row):
    if not isinstance(row, dict):
        return None
    fields = _scalar_fields(row, {"formula", "formula_version", "method", "numerator_ppm", "denominator_ppm", "treo_token", "treo_unit"})
    if ids := _id_list(row.get("input_observation_ids")):
        fields["input_observation_ids"] = ids
    interval = row.get("rounding_interval_pct")
    if isinstance(interval, list) and len(interval) == 2 and all(_scaled_number(item) for item in interval):
        fields["rounding_interval_pct"] = interval
    tokens = row.get("component_tokens")
    if isinstance(tokens, dict):
        fields["component_tokens"] = _scalar_fields(tokens, {*OXIDE_VARIABLES, "MREO"})
    return fields


def _finite(value):
    return type(value) in {float, int} and math.isfinite(value)


def _mcda_view(inputs, results, decision_receipt, project_ids):
    if (set(inputs["criteria_by_project"]) != set(project_ids)
            or set(inputs["panels"]) != set(results["panels"])
            or set(inputs["panels"]) != set(decision_receipt["panels"])):
        raise WebPublicationError("Decision artifacts disagree on cohort or panels")
    policy = inputs["policy"]
    criterion_codes = set(policy["criteria"])
    criteria = {}
    for project_id, cells in inputs["criteria_by_project"].items():
        if set(cells) != criterion_codes:
            raise WebPublicationError("Decision criteria do not cover the pinned policy")
        criteria[project_id] = {
            code: {
                **_scalar_fields(raw, {"state", "reason", "accepted_value", "unit", "source_token"}),
                "scientific_context": _scalar_fields(raw.get("scientific_context") or {}, _CONTEXT_FIELDS) or None,
                "derivation": _derivation(raw.get("derivation")),
            } for code, raw in cells.items()
        }
    definitions = {code: _scalar_fields(rule, {"code", "domain", "interpretation", "unit", "definition_version"})
                   for code, rule in policy["criteria"].items()}
    panels = []
    for panel_id in sorted(inputs["panels"]):
        panel = inputs["panels"][panel_id]
        result = results["panels"][panel_id]
        safe_panel = {
            "id": panel_id,
            **{key: panel[key] for key in ("criterion_codes", "eligible_projects", "scenario_weights")},
            **{key: result[key] for key in ("status", "eligible_count", "minimum_cohort", "point_rankings", "leave_one_out")},
        }
        if any(safe_panel[field] != decision_receipt["panels"][panel_id][field]
               for field in ("status", "eligible_count", "minimum_cohort")):
            raise WebPublicationError("Decision panel evidence differs from its verified receipt")
        _check_panel(safe_panel, set(project_ids), criterion_codes)
        panels.append(safe_panel)
    return {
        "policy_sha256": decision_receipt["policy_sha256"], "policy_version": decision_receipt["policy_version"],
        "status": decision_receipt["status"], "method": results["point_method_version"],
        "sensitivity_method": results["deterministic_sensitivity_version"],
        "criterion_definitions": definitions, "panels": panels, "project_criteria": criteria,
    }


def _check_panel(panel, project_ids, criterion_codes):
    expected = {
        "id", "criterion_codes", "eligible_projects", "scenario_weights", "status",
        "eligible_count", "minimum_cohort", "point_rankings", "leave_one_out",
    }
    if set(panel) != expected or not re.fullmatch(r"[a-z][a-z0-9_]*", panel["id"]):
        raise WebPublicationError("Invalid MCDA panel schema")
    projects = panel["eligible_projects"]
    codes = panel["criterion_codes"]
    weights, rankings, sensitivity = (panel[key] for key in ("scenario_weights", "point_rankings", "leave_one_out"))
    if (
        not isinstance(projects, list) or len(projects) != len(set(projects)) or not set(projects) <= project_ids
        or not isinstance(codes, list) or not codes or len(codes) != len(set(codes)) or not set(codes) <= criterion_codes
        or type(panel["eligible_count"]) is not int or panel["eligible_count"] != len(projects)
        or type(panel["minimum_cohort"]) is not int or panel["minimum_cohort"] < 2
        or not all(isinstance(mapping, dict) for mapping in (weights, rankings, sensitivity))
    ):
        raise WebPublicationError("Invalid MCDA panel cohort or criteria")
    if any(
        not re.fullmatch(r"[a-z][a-z0-9_]*", name) or not isinstance(scenario, dict)
        or set(scenario) != set(codes) or any(not _finite(value) or value < 0 for value in scenario.values())
        for name, scenario in weights.items()
    ):
        raise WebPublicationError("Invalid MCDA scenario weights")
    if len(projects) < panel["minimum_cohort"]:
        if panel["status"] != "INSUFFICIENT_COHORT" or rankings or sensitivity:
            raise WebPublicationError("MCDA must not rank an insufficient cohort")
        return
    if panel["status"] != "PASS" or not weights or set(rankings) != set(weights) or set(sensitivity) != set(weights):
        raise WebPublicationError("Ranked MCDA panel is missing matching scenarios")
    for methods in rankings.values():
        if set(methods) != {"promethee", "saw", "topsis"}:
            raise WebPublicationError("Unexpected MCDA method")
        for rows in methods.values():
            if set(rows) != set(projects) or any(
                set(row) != {"rank", "score"} or not all(map(_finite, row.values())) for row in rows.values()
            ):
                raise WebPublicationError("MCDA rankings differ from the eligible cohort")
    for scenario in sensitivity.values():
        if not isinstance(scenario, list) or any(
            set(entry) != {"removed", "type", "projects"}
            or entry["type"] not in {"criterion", "domain"} or not _safe_text(entry["removed"])
            or set(entry["projects"]) != set(projects)
            or any(set(row) != {"rank", "rank_change"} or not all(map(_finite, row.values()))
                   for row in entry["projects"].values())
            for entry in scenario
        ):
            raise WebPublicationError("MCDA sensitivity differs from the eligible cohort")


def _point(longitude, latitude, left, right):
    shared = ("source_sha256", "geometry_role", "temporal_decision_id", "locator", "scope_id", "crs")
    if (left.get("geometry_role") not in _POINT_ROLES or left.get("temporal_status") != "ELIGIBLE"
            or left.get("crs") not in {"EPSG:4326", "OGC:CRS84"}
            or right.get("temporal_status") != "ELIGIBLE"
            or not _sha(left.get("source_sha256")) or not _sha(left.get("temporal_decision_id"))
            or not all(left.get(key) and left[key] == right.get(key) for key in shared)):
        raise WebPublicationError("Present GIS coordinates lack one qualified site Point")
    if not all(_scaled_number(value) for value in (longitude, latitude)):
        raise WebPublicationError("Census coordinates are not finite decimal values")
    x, y = map(Decimal, (longitude, latitude))
    if not -180 <= x <= 180 or not -90 <= y <= 90:
        raise WebPublicationError("Census coordinates fall outside WGS 84")
    return [float(x), float(y)]


def _gis_view(columns, projects, evidence):
    headings = [item["id"] for item in columns]
    features = []
    if all(column in headings for column in _COORDINATES):
        ordinals = [headings.index(column) for column in _COORDINATES]
        for project in projects:
            lon, lat = [project["cells"][index] for index in ordinals]
            if lon["state"] != "PRESENT" or lat["state"] != "PRESENT":
                continue
            if lon["origin"] not in {"FORMAL_DIRECT", "FORMAL_SELECTION"} or lat["origin"] != lon["origin"]:
                raise WebPublicationError("Present GIS coordinates are not governed site observations")
            left, right = (evidence[cell["lineage_id"]] for cell in (lon, lat))
            coordinates = _point(lon["value"], lat["value"], left, right)
            features.append({
                "type": "Feature", "id": project["id"],
                "geometry": {"type": "Point", "coordinates": coordinates},
                "properties": {
                    "project_id": project["id"], "geometry_role": left["geometry_role"],
                    "longitude_lineage_id": lon["lineage_id"], "latitude_lineage_id": lat["lineage_id"],
                },
            })
    return {"type": "FeatureCollection", "status": "PRESENT" if features else "NO_VERIFIED_COORDINATES", "features": features}


def _coverage_applicability(receipt, columns, column_states, column_reasons):
    source = receipt.get("coverage") or {}
    rules = (source.get("scientific") or {}).get("by_column") or {}
    headings = {row["column_id"]: row["id"] for row in columns}
    projected = {}
    for column_id, rule in rules.items():
        heading = headings[column_id]
        counts = column_states[heading]
        present, unknown, not_applicable = (counts[state] for state in _STATES)
        expected = {
            "present": present, "unknown": unknown, "not_applicable_with_evidence": not_applicable,
            "demonstrated_applicable_denominator": present, "applicability_undetermined": unknown,
            "unexcluded_grid_denominator": present + unknown, "unknown_reasons": dict(column_reasons[heading]),
        }
        if not _safe_text(rule.get("applicability_rule")) or any(rule.get(key) != value for key, value in expected.items()):
            raise WebPublicationError("Scientific applicability receipt differs from the Census ledger")
        projected[column_id] = {**expected, "applicability_rule": rule["applicability_rule"]}
    metadata = source.get("metadata")
    if metadata is not None:
        if (not isinstance(metadata, dict) or set(metadata) != {"present", "denominator", "rate"}
                or any(type(metadata[key]) is not int for key in ("present", "denominator"))
                or not 0 <= metadata["present"] <= metadata["denominator"]
                or (metadata["rate"] is not None and not _scaled_number(metadata["rate"]))):
            raise WebPublicationError("Metadata coverage receipt does not have a validated denominator")
    return metadata, projected


def _coverage_view(columns, projects, evidence, receipt, build_receipt, mcda):
    states, reasons, search, origins = Counter(), Counter(), Counter(), Counter()
    column_states = {column["id"]: Counter() for column in columns}
    column_reasons = defaultdict(Counter)
    observation_ids = set()
    derived_ids = set()
    for project in projects:
        for column, cell in zip(columns, project["cells"], strict=True):
            state, lineage = cell["state"], evidence[cell["lineage_id"]]
            states[state] += 1
            column_states[column["id"]][state] += 1
            if state == "UNKNOWN":
                reasons[lineage["reason"]] += 1
                column_reasons[column["id"]][lineage["reason"]] += 1
            if column["id"] == PROJECT_ID_COLUMN:
                search[lineage.get("source_search_status", "STATUS_NOT_PROVIDED")] += 1
            if state != "PRESENT":
                continue
            origin = cell["origin"]
            origins[origin] += 1
            if origin in {"FORMAL_SELECTION", "FORMAL_DIRECT"} and _sha(lineage.get("observation_id")):
                observation_ids.add(lineage["observation_id"])
            if origin == "FORMAL_DERIVED":
                derived_ids.add(lineage.get("derived_value_id", cell["lineage_id"]))
    identity = column_states[PROJECT_ID_COLUMN]["PRESENT"]
    direct = origins["FORMAL_SELECTION"] + origins["FORMAL_DIRECT"]
    derived = origins["FORMAL_DERIVED"]
    metadata = sum(origins[origin] for origin in METADATA_ORIGINS) - identity
    references = origins["EVIDENCE_REFERENCE"]
    comparable = sum(cell.get("state") == "COMPARABLE" for row in mcda["project_criteria"].values() for cell in row.values())
    if (dict(states) != {key: value for key, value in receipt["cell_states"].items() if value}
            or comparable != build_receipt["comparable_project_criteria"]
            or derived != receipt.get("formal_derived_cells", 0)
            or ("formal_scientific_cells" in receipt and direct + derived != receipt["formal_scientific_cells"])):
        raise WebPublicationError("Coverage disagrees with verified research artifacts")
    metadata_coverage, applicability = _coverage_applicability(receipt, columns, column_states, column_reasons)
    if metadata_coverage is not None and metadata_coverage["present"] != identity + metadata:
        raise WebPublicationError("Metadata coverage differs from the Census ledger")
    return {
        "projects": len(projects), "columns": len(columns), "cells": len(projects) * len(columns),
        "cell_states": {state: states[state] for state in _STATES},
        "identity_cells": identity, "metadata_cells": metadata, "identity_or_metadata_cells": identity + metadata,
        "formal_direct_cells": direct, "formal_derived_cells": derived, "evidence_reference_cells": references,
        "other_present_cells": states["PRESENT"] - identity - metadata - direct - derived - references,
        "unique_direct_observation_count": len(observation_ids), "unique_formal_derivation_count": len(derived_ids),
        "present_source_count": build_receipt["verified_source_count"],
        "eligible_source_count": build_receipt["eligible_source_count"],
        "comparable_project_criteria": comparable, "project_source_search_statuses": dict(search),
        "metadata_coverage": metadata_coverage, "scientific_applicability": applicability,
        "unknown_reasons": dict(reasons), "states_by_column": {
            heading: {state: counts[state] for state in _STATES} for heading, counts in column_states.items()
        },
        "panel_cohorts": [{key: panel[key] for key in ("id", "status", "eligible_count", "minimum_cohort")}
                         for panel in mcda["panels"]],
    }


def _research_inputs(profile, build, release_id, release_root, authorization):
    if not _sha(release_id):
        raise WebPublicationError("Web release ID must be a full lowercase SHA-256")
    settings = load_profile(profile)
    registry = load_registry(settings.registry_path)
    columns = load_census_columns(settings.census_columns_path)
    if len(registry.projects) != settings.project_count or len(columns) != settings.census_column_count:
        raise WebPublicationError("Profile does not pin the declared Census shape")
    receipt_bytes = _file(build, "build_receipt.json")
    receipt = _read_json(receipt_bytes)
    receipt_sha = _digest(receipt_bytes)
    cutoff = settings.research_cutoff.isoformat()
    expected_build = (
        BUILD_SCHEMA, settings.profile_id, cutoff, registry.sha256, _digest(settings.census_columns_path.read_bytes()),
        settings.sha256,
    )
    found_build = (
        receipt.get("schema"), receipt.get("profile_id"), receipt.get("research_cutoff"), receipt.get("project_registry_hash"),
        receipt.get("census_columns_sha256"), receipt.get("input_artifacts", {}).get("profile"),
    )
    if found_build != expected_build or any(receipt.get("gates", {}).get(gate) != "PASS" for gate in ("F0", "F3")):
        raise WebPublicationError("Web export requires a verified Census build for the exact profile")
    declared = receipt.get("scientific_artifacts", {})
    if set(declared) != set(ARTIFACTS):
        raise WebPublicationError("Research receipt does not pin the expected scientific artifacts")
    artifacts = {name: _file(build, name) for name in ARTIFACTS}
    if {name: _digest(data) for name, data in artifacts.items()} != declared:
        raise WebPublicationError("Research artifact digest differs from the build receipt")

    status = CANDIDATE
    if authorization is not None:
        from .publication import FormalPublicationAuthorization

        if not isinstance(authorization, FormalPublicationAuthorization) or (
            authorization.release_id, authorization.cutoff, authorization.build_receipt_sha256,
            authorization.profile_sha256, authorization.output_hashes,
        ) != (release_id, cutoff, receipt_sha, settings.sha256, declared):
            raise WebPublicationError("Formal authorization does not bind this research build")
        status = FORMAL
    elif release_root is not None:
        archive = load_release(release_root, release_id)
        if (archive.get("build_receipt_sha256"), archive.get("output_hashes"), archive.get("cutoff")) != (
            receipt_sha, declared, cutoff,
        ):
            raise WebPublicationError("Archived release ID does not bind this research build")
    elif release_id != receipt.get("observation_release_id"):
        raise WebPublicationError("Release ID differs from the input build; use the verified archive for an archive ID")
    return settings, registry, columns, receipt, receipt_sha, artifacts, status


def _confirm_research_views(settings, registry_sha, build_receipt, data):
    census_receipt = _read_json(data["census/census_receipt.json"])
    decision_receipt = _read_json(data["decision/decision_receipt.json"])
    decision_inputs = _read_json(data["decision/mcda_inputs.json"])
    decision_results = _read_json(data["decision/mcda_results.json"])
    extraction = _read_json(data["observations/extraction.json"])
    observation_release_id = build_receipt["observation_release_id"]
    expected = (settings.profile_id, settings.research_cutoff.isoformat(), registry_sha, observation_release_id)
    for row in (census_receipt, decision_receipt, decision_inputs, decision_results):
        found = (row.get("profile_id"), row.get("research_cutoff"), row.get("registry_sha256"), row.get("release_id"))
        if found != expected:
            raise WebPublicationError("Research views come from different profiles or observation releases")
    declared = build_receipt["scientific_artifacts"]
    inputs_sha, results_sha = declared["decision/mcda_inputs.json"], declared["decision/mcda_results.json"]
    if (
        not _sha(observation_release_id) or extraction.get("release_id") != observation_release_id
        or census_receipt.get("outputs") != {
            name: declared[f"census/{name}"] for name in ("census.csv", "cell_status.csv")
        }
        or census_receipt.get("status") != "PASS"
        or decision_receipt.get("mcda_inputs_sha256") != inputs_sha
        or decision_receipt.get("mcda_results_sha256") != results_sha
        or decision_results.get("mcda_inputs_sha256") != inputs_sha
        or build_receipt.get("mcda_inputs_hash") != inputs_sha
        or not _sha(decision_receipt.get("policy_sha256"))
        or any(item.get("policy_sha256") != decision_receipt["policy_sha256"]
               for item in (decision_inputs, decision_results))
    ):
        raise WebPublicationError("Research view does not bind its exact input artifacts")
    return census_receipt, decision_receipt, decision_inputs, decision_results


def _document(schema, release_id, **fields):
    return {"schema_version": schema, "release_id": release_id, **fields}


def export_web(
    *, profile: Path, research_build: Path, release_id: str, out: Path,
    release_root: Path | None = None, authorization: FormalPublicationAuthorization | None = None,
) -> dict:
    """Project one verified build into a content-checked, immutable view without changing a pointer."""
    try:
        return _export_web(
            profile=profile, research_build=research_build, release_id=release_id, out=out,
            release_root=release_root, authorization=authorization,
        )
    except (KeyError, TypeError, UnicodeError, OverflowError) as error:
        raise WebPublicationError("Invalid research build or projected data schema") from error


def _export_web(*, profile, research_build, release_id, out, release_root, authorization):
    settings, registry, headings, receipt, receipt_sha, artifacts, status = _research_inputs(
        profile, Path(research_build), release_id, release_root, authorization,
    )
    census_receipt, decision_receipt, decision_inputs, decision_results = _confirm_research_views(
        settings, registry.sha256, receipt, artifacts,
    )
    project_ids = tuple(project.project_id for project in registry.projects)
    columns, display_policy_sha = _column_view(headings)
    projects, evidence = _build_grid(headings, project_ids, artifacts["census/census.csv"], artifacts["census/cell_status.csv"])
    composition = _composition_view(
        artifacts["observations/observations.jsonl"], evidence, set(project_ids), decision_inputs,
    )
    mcda = _mcda_view(decision_inputs, decision_results, decision_receipt, project_ids)
    views = {
        "census.json": {"columns": columns, "projects": projects, "selected_composition": composition},
        "census_evidence.json": {"entries": evidence},
        "coverage.json": _coverage_view(columns, projects, evidence, census_receipt, receipt, mcda),
        "mcda.json": mcda,
        "gis.geojson": _gis_view(columns, projects, evidence),
    }
    files = {name: _encode(_document(_SCHEMAS[name], release_id, **value)) for name, value in views.items()}
    files["census.csv"] = artifacts["census/census.csv"]
    manifest = _document(
        MANIFEST_SCHEMA, release_id,
        release_status=status, cutoff=settings.research_cutoff.isoformat(), timezone=settings.timezone.key,
        profile_id=settings.profile_id, profile_sha256=settings.sha256,
        research_code_commit=receipt["code_commit"], project_count=len(project_ids), column_count=len(columns),
        project_registry_sha256=registry.sha256, census_column_contract_sha256=receipt["census_columns_sha256"],
        census_projection_policy_sha256=census_receipt.get("projection_policy_sha256"),
        display_policy_sha256=display_policy_sha,
        census_sha256=_digest(files["census.csv"]), decision_policy_sha256=mcda["policy_sha256"],
        research_mcda_inputs_sha256=receipt["mcda_inputs_hash"],
        research_mcda_results_sha256=receipt["scientific_artifacts"]["decision/mcda_results.json"],
        research_build_receipt_sha256=receipt_sha, research_observation_release_id=receipt["observation_release_id"],
        authorization_sha256=authorization.authorization_sha256 if authorization is not None else None,
        artifacts={name: {"path": name, "sha256": _digest(data)} for name, data in files.items()},
    )
    manifest_bytes = _encode(manifest)
    manifest_sha = _digest(manifest_bytes)
    root = Path(out) / "releases"
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink():
        raise WebPublicationError("Web release directory cannot be a symbolic link")
    target = root / release_id
    if target.exists() or target.is_symlink():
        verify_web_release(target, expected_release_id=release_id, expected_manifest_sha256=manifest_sha)
        return manifest
    with TemporaryDirectory(prefix=".web-release-", dir=root) as temporary:
        stage = Path(temporary) / release_id
        stage.mkdir()
        for name, data in {**files, "manifest.json": manifest_bytes}.items():
            (stage / name).write_bytes(data)
        verify_web_release(stage, expected_release_id=release_id, expected_manifest_sha256=manifest_sha)
        try:
            stage.rename(target)
        except FileExistsError:
            verify_web_release(target, expected_release_id=release_id, expected_manifest_sha256=manifest_sha)
    return manifest


def _allowed_public_evidence(row):
    scalars = row.keys() - {
        "project_id", "column_id", "physical_page", "metadata", "input_observation_ids", "temporal_evidence_ids",
    }
    filtered = _scalar_fields(row, _EVIDENCE_FIELDS)
    if (
        scalars != set(filtered) or not _safe_text(row.get("reason")) or not _safe_text(row.get("evidence_state"))
        or ("physical_page" in row and (type(row["physical_page"]) is not int or row["physical_page"] < 1))
        or ("metadata" in row and row["metadata"] != _scalar_fields(row["metadata"], _METADATA_FIELDS))
        or any(key in row and row[key] != _id_list(row[key]) for key in ("input_observation_ids", "temporal_evidence_ids"))
    ):
        raise WebPublicationError("Public evidence contains prohibited metadata or has no reason")


def _check_census(manifest, census, evidence, csv_payload):
    columns = census["columns"]
    projects = census["projects"]
    valid_ids = {project["id"] for project in projects}
    if (
        len(columns) != manifest["column_count"] or len(projects) != manifest["project_count"]
        or len(valid_ids) != len(projects) or any(not PROJECT_ID.fullmatch(project_id) for project_id in valid_ids)
    ):
        raise WebPublicationError("Web Census shape differs from the manifest")
    headings = [row["id"] for row in columns]
    csv_headings, raw = _csv(csv_payload)
    if (tuple(headings) != csv_headings or headings[0] != PROJECT_ID_COLUMN
            or len(raw) != len(projects) or any(
                set(rule) != {"id", "column_id", "label", "label_en", "display_group", "data_type", "unit"}
                or any(value is not None and not _safe_text(value) for value in rule.values())
                for rule in columns
            )):
        raise WebPublicationError("Web Census does not preserve the pinned CSV headings")
    used = set()
    direct = {}
    references, derived = [], []
    for project, row in zip(projects, raw, strict=True):
        if set(project) != {"id", "cells"} or project["id"] != row[PROJECT_ID_COLUMN] or len(project["cells"]) != len(columns):
            raise WebPublicationError("Web Census Project does not preserve its CSV row")
        for heading, cell in zip(headings, project["cells"], strict=True):
            value, state, lineage_id = cell["value"], cell["state"], cell["lineage_id"]
            entry = evidence[lineage_id]
            if (
                set(cell) != {"value", "state", "origin", "precision", "lineage_id"}
                or state not in _STATES or (state == "PRESENT") != (value is not None)
                or ("" if value is None else value) != row[heading] or not _public_value(heading, row[heading])
                or (cell["origin"] is not None and not _safe_text(cell["origin"]))
                or (state == "PRESENT" and not cell["origin"])
                or (entry["project_id"], entry["column_id"]) != (project["id"], heading) or lineage_id in used
            ):
                raise WebPublicationError("Web Census value, null, identity, or lineage differs from its CSV or grid")
            if cell["precision"] is not None and _precision({"precision": cell["precision"]}) != cell["precision"]:
                raise WebPublicationError("Invalid public Census precision")
            _allowed_public_evidence(entry)
            if state == "PRESENT" and entry["evidence_state"] == "VALIDATED_DESCRIPTIVE":
                descriptive = entry.get("metadata", {})
                attribute = "DEPOSIT_TYPE" if heading in {"礦床型態代碼", "礦床型態"} else (
                    "DEVELOPMENT_STAGE" if heading in {"開發階段代碼", "開發階段類別", "開發階段"} else None
                )
                expected_role = "CONFIRMED_DEPOSIT_TYPE" if attribute == "DEPOSIT_TYPE" else "COMPLETED"
                if (attribute is None or cell["origin"] != "SOURCE_EVIDENCE" or entry.get("observation_id")
                        or any(not _sha(entry.get(key)) for key in (
                            "source_sha256", "anchor_id", "selection_id", "validation_id", "temporal_decision_id",
                        )) or entry.get("temporal_status") != "ELIGIBLE" or not entry.get("locator")
                        or descriptive.get("evidence_type") != "SOURCE_BOUND_TEXT_ATTRIBUTE"
                        or descriptive.get("display_only") is not True or descriptive.get("attribute_kind") != attribute
                        or descriptive.get("attribute_role") != expected_role or not descriptive.get("native_value")
                        or not descriptive.get("publication_date") or not _sha(descriptive.get("policy_sha256"))
                        or attribute == "DEVELOPMENT_STAGE" and not descriptive.get("event_date_token")):
                    raise WebPublicationError("Census attribute lacks its selected source and descriptive role")
            if state == "PRESENT" and cell["origin"] in {"FORMAL_SELECTION", "FORMAL_DIRECT"}:
                if (any(not _sha(entry.get(key)) for key in (
                        "source_sha256", "observation_id", "anchor_id", "temporal_decision_id"))
                        or not entry.get("locator") or entry.get("temporal_status") != "ELIGIBLE"
                        or entry.get("evidence_state") != "FORMAL_DIRECT"):
                    raise WebPublicationError("Formal Census cell lacks eligible source, observation, and locator evidence")
                direct[(project["id"], entry["observation_id"])] = entry
            if state == "PRESENT" and cell["origin"] == "EVIDENCE_REFERENCE":
                references.append(entry)
            if state == "PRESENT" and cell["origin"] == "FORMAL_DERIVED":
                derived.append(entry)
            used.add(lineage_id)
    for project_id, rows in census["selected_composition"].items():
        if project_id not in valid_ids or not isinstance(rows, list):
            raise WebPublicationError("Composition view Project is outside the Census")
        for row in rows:
            variable, lineage_id = row["variable"], row["lineage_id"]
            entry = evidence[lineage_id]
            if (
                variable not in _COMPOSITION or not _scaled_number(row["value"]) or row["unit"] != "ppm"
                or any(key not in {"variable", "value", "unit", "chemical_basis", "material_basis", "denominator_basis",
                                   "lineage_id"} or not _safe_text(value) for key, value in row.items())
                or (entry["project_id"], entry["column_id"]) != (project_id, variable) or lineage_id in used
                or entry["evidence_state"] != "FORMAL_DIRECT"
                or not _sha(entry.get("source_sha256")) or not _sha(entry.get("observation_id"))
            ):
                raise WebPublicationError("Unsafe or unbound public composition selection")
            _allowed_public_evidence(entry)
            used.add(lineage_id)
    _check_evidence_references(references, direct)
    _check_derived_references(derived, direct)
    if set(evidence) != used:
        raise WebPublicationError("Web evidence contains orphaned entries or missing cell lineage")
    return valid_ids


def _check_derived_references(derived, direct):
    if not derived:
        return
    from .derivations import FORMULA_REGISTRY_SHA256, registered_formula

    fields = ("source_sha256", "scope_id", "statement_id", "effective_date", "category", "temporal_decision_id")
    for entry in derived:
        ids = _id_list(entry.get("input_observation_ids"))
        inputs = [direct.get((entry["project_id"], observation)) for observation in ids]
        formula = registered_formula(entry.get("formula_id"))
        if (
            entry["column_id"] != "總TREO蘊藏量_kt" or entry["evidence_state"] != "FORMAL_DERIVED"
            or len(set(ids)) != 2 or len(inputs) != 2 or not all(inputs)
            or any(not _sha(entry.get(key)) for key in (
                "formula_sha256", "formula_registry_sha256", "assessment_id", "derived_value_id",
                "source_sha256", "temporal_decision_id",
            )) or formula is None or entry.get("formula") != formula.expression
            or entry.get("formula_sha256") != formula.sha256
            or entry.get("formula_registry_sha256") != FORMULA_REGISTRY_SHA256 or entry.get("raw_value")
            or tuple(item["column_id"] for item in inputs) != ("礦石蘊藏量_Mt", "TREO品位_pct")
            or any(not entry.get(key) or item.get(key) != entry[key] for item in inputs for key in fields)
            or any(item.get("material_basis") != "WHOLE_ORE" or item.get("chemical_basis") != "TREO" for item in inputs)
        ):
            raise WebPublicationError("Formal derived Census cell lacks matching scoped inputs or approved formula evidence")


def _check_evidence_references(references, direct):
    for entry in references:
        linked = entry.get("input_observation_ids", ())
        context = ("source_sha256", "scope_id", "statement_id", "effective_date")
        if (
            entry.get("data_role") != "EVIDENCE_REFERENCE" or entry["evidence_state"] != "DESCRIPTIVE_ONLY"
            or len(linked) < 1 or len(linked) != len(set(linked)) or not _sha(entry.get("anchor_id"))
            or not _sha(entry.get("temporal_decision_id")) or entry.get("temporal_status") != "ELIGIBLE"
            or not entry.get("locator") or any(not entry.get(key) for key in context)
        ):
            raise WebPublicationError("Present Census evidence reference lacks eligible source or context")
        measurements = [direct.get((entry["project_id"], observation_id)) for observation_id in linked]
        if any(item is None or any(entry[key] != item.get(key) for key in context) for item in measurements):
            raise WebPublicationError("Census evidence reference points outside its accepted statement")
        if entry["column_id"] == _RESOURCE_REFERENCE_HEADING and (
            {item["column_id"] for item in measurements} != _RESOURCE_MEASUREMENT_HEADINGS
        ):
            raise WebPublicationError("TREO resource evidence reference lacks a complete accepted statement")
        if not any(
            all(entry.get(key) == item.get(key) for key in ("anchor_id", "locator", "temporal_decision_id"))
            for item in measurements
        ):
            raise WebPublicationError("Census evidence reference lacks its linked original anchor")


def _check_mcda(mcda, manifest, project_ids):
    criteria = mcda["project_criteria"]
    definitions = mcda["criterion_definitions"]
    codes = set(definitions)
    if (set(criteria) != project_ids or mcda["policy_sha256"] != manifest["decision_policy_sha256"]
            or any(not _safe_text(mcda[key]) for key in ("policy_version", "status", "method", "sensitivity_method"))
            or any(not re.fullmatch(r"[A-Z][0-9]+", code)
                   or rule != _scalar_fields(rule, {"code", "domain", "interpretation", "unit", "definition_version"})
                   for code, rule in definitions.items())):
        raise WebPublicationError("MCDA web view does not bind the cohort and criterion policy")
    for cells in criteria.values():
        if set(cells) != codes:
            raise WebPublicationError("MCDA web Project does not cover all criterion states")
        for row in cells.values():
            scalars = row.keys() - {"scientific_context", "derivation"}
            state = row.get("state")
            if (
                scalars != set(_scalar_fields(row, {"state", "reason", "accepted_value", "unit", "source_token"}))
                or state not in {"COMPARABLE", "UNKNOWN"} or not row.get("reason")
                or (state == "COMPARABLE") != _scaled_number(row.get("accepted_value"))
                or row.get("scientific_context") not in (None, _scalar_fields(row.get("scientific_context") or {}, _CONTEXT_FIELDS))
                or row.get("derivation") != _derivation(row.get("derivation"))
            ):
                raise WebPublicationError("Invalid published criterion or scientific context")
    if len({panel["id"] for panel in mcda["panels"]}) != len(mcda["panels"]):
        raise WebPublicationError("Duplicate MCDA panel")
    for panel in mcda["panels"]:
        _check_panel(panel, project_ids, codes)
        if any(criteria[project][code].get("state") != "COMPARABLE"
               for project in panel["eligible_projects"] for code in panel["criterion_codes"]):
            raise WebPublicationError("MCDA panel includes a Project with missing criteria")


def _check_coverage(coverage, census, entries, mcda):
    keys = {"schema_version", "release_id"}
    content = {key: value for key, value in coverage.items() if key not in keys}
    receipt = {**coverage, "formal_scientific_cells": coverage["formal_direct_cells"] + coverage["formal_derived_cells"], "coverage": {
        "metadata": coverage["metadata_coverage"], "scientific": {"by_column": coverage["scientific_applicability"]},
    }}
    derived = _coverage_view(census["columns"], census["projects"], entries, receipt, {
        "verified_source_count": coverage["present_source_count"],
        "eligible_source_count": coverage["eligible_source_count"],
        "comparable_project_criteria": coverage["comparable_project_criteria"],
    }, mcda)
    counts = (coverage["present_source_count"], coverage["eligible_source_count"])
    if content != derived or any(type(number) is not int or number < 0 for number in counts) or counts[1] > counts[0]:
        raise WebPublicationError("Published coverage differs from its Census and MCDA")


def verify_web_release(
    directory: Path, *, expected_release_id: str | None = None, expected_manifest_sha256: str | None = None,
) -> dict:
    """Check canonical files, privacy allowlists, cell-level joins and release identity."""
    try:
        return _verify_web_release(Path(directory), expected_release_id, expected_manifest_sha256)
    except (KeyError, TypeError, AttributeError, UnicodeError, OverflowError) as error:
        raise WebPublicationError("Invalid public research artifact schema") from error


def _verify_web_release(directory, expected_release_id, expected_manifest_sha256):
    if directory.is_symlink() or not directory.is_dir() or {path.name for path in directory.iterdir()} != {*FILES, "manifest.json"}:
        raise WebPublicationError("Web release has missing, unexpected, or unsafe assets")
    payload = _file(directory, "manifest.json")
    if expected_manifest_sha256 is not None and _digest(payload) != expected_manifest_sha256:
        raise WebPublicationError("Web manifest digest differs from its authorized pointer")
    manifest = _read_json(payload, canonical=True)
    release_id = manifest.get("release_id")
    digest_fields = {
        "profile_sha256", "project_registry_sha256", "census_column_contract_sha256", "census_sha256", "decision_policy_sha256",
        "research_mcda_inputs_sha256", "research_mcda_results_sha256", "research_build_receipt_sha256",
        "research_observation_release_id",
    }
    optional_sha = {"display_policy_sha256", "census_projection_policy_sha256", "authorization_sha256"}
    envelope = {"schema_version", "release_id"}
    if (
        set(manifest) != digest_fields | optional_sha | envelope | {
            "artifacts", "release_status", "cutoff", "timezone", "profile_id", "project_count", "column_count",
            "research_code_commit",
        }
        or manifest["schema_version"] != MANIFEST_SCHEMA or not _sha(release_id)
        or (expected_release_id is not None and release_id != expected_release_id)
        or manifest["release_status"] not in {FORMAL, CANDIDATE}
        or (manifest["release_status"] == FORMAL) != _sha(manifest.get("authorization_sha256"))
        or any(not _sha(manifest[field]) for field in digest_fields)
        or any(manifest[field] is not None and not _sha(manifest[field]) for field in optional_sha)
        or any(type(manifest[field]) is not int or manifest[field] <= 0 for field in ("project_count", "column_count"))
        or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", manifest["profile_id"])
        or not re.fullmatch(r"[a-f0-9]{40}", manifest["research_code_commit"])
        or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]*(?:/[A-Za-z0-9_+-]+)*", manifest["timezone"])
        or date.fromisoformat(manifest["cutoff"]).isoformat() != manifest["cutoff"]
        or set(manifest["artifacts"]) != set(FILES)
    ):
        raise WebPublicationError("Web manifest schema or release identity is invalid")
    docs = {}
    for name in FILES:
        value = _file(directory, name)
        if manifest["artifacts"][name] != {"path": name, "sha256": _digest(value)}:
            raise WebPublicationError(f"Web artifact differs from its manifest: {name}")
        if name in _SCHEMAS:
            document = _read_json(value, canonical=True)
            if (document.get("schema_version"), document.get("release_id")) != (_SCHEMAS[name], release_id):
                raise WebPublicationError(f"Web document mixes release identities: {name}")
            docs[name] = document
        else:
            if _digest(value) != manifest["census_sha256"]:
                raise WebPublicationError("Download CSV differs from the pinned Census")
            csv_payload = value

    census, entries, coverage, mcda, gis = (docs[name] for name in _SCHEMAS)
    known = (
        (census, {"columns", "projects", "selected_composition"}), (entries, {"entries"}),
        (mcda, {"policy_sha256", "policy_version", "status", "method", "sensitivity_method",
                "criterion_definitions", "panels", "project_criteria"}),
    )
    if any(set(doc) != fields | envelope for doc, fields in known):
        raise WebPublicationError("Unexpected public data document fields")
    projects = _check_census(manifest, census, entries["entries"], csv_payload)
    _check_mcda(mcda, manifest, projects)
    _check_coverage(coverage, census, entries["entries"], mcda)
    if gis != _document(_SCHEMAS["gis.geojson"], release_id, **_gis_view(census["columns"], census["projects"], entries["entries"])):
        raise WebPublicationError("Map contains unverified points or differs from the published Census")
    return manifest
