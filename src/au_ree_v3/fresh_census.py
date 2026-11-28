"""Build a fully accounted Census from a fresh, temporally governed release."""

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .census import COLUMNS
from .census_metadata import CensusMetadata, SHA256, coverage_report, metadata_projection, public_source_url
from .census_science import project_science_views, resource_presence_cells
from .comparability import census_quantity, resource_context, resource_observation_compatible
from .contracts import ContractViolation, ResearchRelease, canonical_json
from .registry import PROJECT_ID_COLUMN, load_census_columns, load_profile, load_registry
from .research_inputs import CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, ResearchInput, checked_research_inputs
from .store import Store

SCHEMA = "fresh-census-v1"
PROJECTION_SCHEMA = "fresh-census-v2"
IDENTITY_COLUMN = PROJECT_ID_COLUMN
_CENSUS_ONLY_REASON = "GOVERNED_CENSUS_RESOURCE_OBSERVATION"
FORMAL_CENSUS_ORIGINS = frozenset(("FORMAL_SELECTION", "FORMAL_DERIVED"))
STATUS_FIELDS_V1 = (
    "project_id", "column", "value", "value_state", "reason", "value_origin", "source_search_status",
    "registry_sha256", "release_id", "selection_id", "validation_id", "candidate_id", "observation_id",
    "raw_record_id", "source_key", "source_sha256", "source_url", "anchor_id", "locator", "physical_page",
    "source_table", "source_row", "source_column", "scope_id", "scope", "statement_id", "effective_date",
    "category", "raw_value", "raw_unit", "unit", "material_basis", "chemical_basis", "denominator_basis",
    "rules_sha256", "temporal_policy_version", "temporal_status", "temporal_decision_id", "temporal_evidence_ids",
    "comparable", "context_diagnostics",
)
STATUS_FIELDS = (*STATUS_FIELDS_V1,
    "column_id", "data_role", "evidence_state", "projection_policy_version", "projection_policy_sha256",
    "metadata_evidence", "precision", "lineage_id", "formula_id", "formula_sha256", "input_observation_ids",
    "geometry_role",
)
PROJECTS = 93
CENSUS_COLUMNS = 51


@dataclass(frozen=True)
class CensusAxes:
    profile_id: str
    cutoff: str
    timezone: str
    project_ids: tuple[str, ...]
    columns: tuple[str, ...]
    profile_sha: str
    registry_sha: str
    columns_sha: str

    @classmethod
    def load(cls, path: Path):
        profile = load_profile(path)
        registry = load_registry(profile.registry_path)
        projects = tuple(project.project_id for project in registry.projects)
        columns = tuple(load_census_columns(profile.census_columns_path))
        if len(projects) != PROJECTS or profile.project_count != PROJECTS:
            raise ContractViolation("fresh Census requires 93 unique Registry IDs")
        if len(columns) != CENSUS_COLUMNS or profile.census_column_count != CENSUS_COLUMNS:
            raise ContractViolation("fresh Census requires the 51 unique Census headings")
        if columns[0] != IDENTITY_COLUMN or not set(COLUMNS.values()).issubset(columns):
            raise ContractViolation("Census axes lack the pinned identity and resource headings")
        return cls(
            profile.profile_id, profile.research_cutoff.isoformat(), profile.timezone.key, projects, columns, profile.sha256,
            registry.sha256, _sha(profile.census_columns_path),
        )


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _accepted_cell(entry: ResearchInput, *, enriched=False) -> dict:
    raw, candidate, source = entry.raw, entry.candidate, entry.source
    anchor = entry.anchor
    lineage, trace = entry.lineage(), entry.temporal_trace
    value, unit = census_quantity(raw.variable, candidate.value, candidate.unit)
    lineage.pop("temporal_decision")
    lineage.pop("project_id")
    reason = "GOVERNED_SELECTION" if unit == candidate.unit else "GOVERNED_SELECTION_EXACT_UNIT_CONVERSION"
    census_only = entry.accepted.reason == CENSUS_RESOURCE_RULE
    cell = {
        **lineage, "value": value, "value_state": "PRESENT",
        "reason": _CENSUS_ONLY_REASON if census_only else reason,
        "value_origin": "FORMAL_SELECTION",
        "source_url": source.url, "locator": anchor.locator, "physical_page": anchor.page,
        "source_table": anchor.table, "source_row": anchor.row, "source_column": anchor.column,
        "scope": entry.scope.name, "statement_id": raw.statement_id, "effective_date": raw.effective_date,
        "category": raw.category, "raw_value": raw.raw_value, "raw_unit": raw.raw_unit,
        "unit": unit, "material_basis": candidate.material_basis,
        "chemical_basis": candidate.chemical_basis, "denominator_basis": candidate.denominator_basis,
        "rules_sha256": entry.validation.rules_sha, "temporal_policy_version": trace["policy_version"],
        "temporal_status": trace["status"], "temporal_decision_id": trace["decision_id"],
        "temporal_evidence_ids": canonical_json(trace["matching_evidence_ids"]), "comparable": "NO" if census_only else "YES",
    }
    if census_only and enriched:
        bound = json.loads(raw.original_payload)["native_table_binding"]
        precision = {"mode": "EXACT_SOURCE_TOKEN", "token": raw.raw_value, "unit": raw.raw_unit,
                     "rounding_interval": bound["raw_rounding_interval"]}
        if bound["reported_significant_figures"] is not None:
            precision["significant_figures"] = bound["reported_significant_figures"]
        cell["precision"] = canonical_json(precision)
    return cell


def _selection_rows(store: Store | None, release_id: str | None, axes: CensusAxes, *, science=False):
    if (store is None) != (release_id is None):
        raise ContractViolation("Census store and release ID must be supplied together")
    if store is None:
        return None, {}, {}, None
    release = store.get(ResearchRelease, release_id)
    if release is None or release.cutoff != axes.cutoff or not set(release.cohort).issubset(axes.project_ids):
        raise ContractViolation("Census release must have the profile cutoff and registered projects")

    selected = defaultdict(list)
    decisions = defaultdict(list)
    release_inputs = checked_research_inputs(store, release, purpose="CENSUS")
    for entry in release_inputs:
        raw, validation, trace = entry.raw, entry.validation, entry.temporal_trace
        if raw.variable not in COLUMNS:
            continue
        selected[raw.project_id].append(entry)
        decisions[raw.project_id, COLUMNS[raw.variable]].append({
            "validation_id": validation.validation_id, "observation_id": raw.observation_id,
            "source_sha256": raw.source_sha, "scope_id": raw.scope_id,
            "outcome": str(validation.outcome), "reason": validation.reason,
            "temporal_status": trace["status"], "temporal_decision_id": trace["decision_id"],
        })

    projected = {}
    for project_id, entries in selected.items():
        by_column = {COLUMNS[entry.raw.variable]: [] for entry in entries}
        contexts = set()
        for entry in entries:
            if resource_observation_compatible(entry):
                contexts.add(resource_context(entry))
                by_column[COLUMNS[entry.raw.variable]].append(entry)
        for column, alternatives in by_column.items():
            key = project_id, column
            if release.release_mode != "FORMAL":
                projected[key] = {"reason": "SELECTION_NOT_FORMAL"}
            elif len(contexts) > 1 or len(alternatives) > 1:
                projected[key] = {"reason": "CONFLICTING_SELECTED_CONTEXT"}
            elif not alternatives:
                projected[key] = {"reason": "NOT_COMPARABLE_FOR_CENSUS_COLUMN"}
            else:
                projected[key] = _accepted_cell(alternatives[0], enriched=science)

    scientific_views = project_science_views(release_inputs, release, axes.columns) if science else None
    if scientific_views is not None:
        projected.update(resource_presence_cells(scientific_views))
        from .census_derivations import governed_derived_projection

        derived_cells, assessed = governed_derived_projection(store, release, release_inputs)
        if derived_cells:
            if projected.keys() & derived_cells.keys():
                raise ContractViolation("A Census derivation overlaps an existing scientific selection")
            projected.update(derived_cells)
            scientific_views["formal_derivations"] = assessed
    return release.release_mode, projected, decisions, scientific_views


def _field_status(axes, project, column, searches, selected, decisions):
    source_status = searches.get(project, "STATUS_NOT_PROVIDED")
    row = {field: "" for field in STATUS_FIELDS_V1}
    row.update(project_id=project, column=column, source_search_status=source_status, comparable="NO")
    if column == IDENTITY_COLUMN:
        row.update(
            value=project, value_state="PRESENT", reason="REGISTRY_IDENTITY",
            value_origin="IDENTITY", registry_sha256=axes.registry_sha, temporal_status="NOT_REQUIRED_FOR_IDENTITY",
        )
        return row

    key = project, column
    diagnostics = decisions.get(key, ())
    if column not in COLUMNS.values():
        reason = "FRESH_COLUMN_INPUT_NOT_IMPLEMENTED"
    else:
        reason = "NO_ACCEPTED_OBSERVATION"
    row.update(value_state="UNKNOWN", reason=reason, temporal_status="NOT_ESTABLISHED")
    if diagnostics:
        row["context_diagnostics"] = canonical_json(diagnostics)
        trace_statuses = {item["temporal_status"] for item in diagnostics}
        if len(trace_statuses) == 1:
            row["temporal_status"] = trace_statuses.pop()
    row.update(selected.get(key, {}))
    return row


_SOURCE_REASONS = {
    "NOT_SEARCHED": "NOT_SEARCHED", "NO_SOURCE_FOUND": "NOT_FOUND_AFTER_SEARCH",
    "SOURCE_OBJECT_UNAVAILABLE": "SOURCE_UNAVAILABLE", "SOURCE_LOOKUP_BLOCKED": "SOURCE_UNAVAILABLE",
    "SOURCE_LOCATOR_REACHABLE": "SOURCE_LOCATOR_WITHOUT_VERIFIED_DOCUMENT",
}


def _unknown_reason(row, definition, metadata, has_resource):
    column_id = definition["column_id"]
    policy_method = definition["projection_method"]
    if row["reason"] not in {"FRESH_COLUMN_INPUT_NOT_IMPLEMENTED", "NO_ACCEPTED_OBSERVATION"}:
        return row["reason"]
    if column_id == "asx_code":
        return "LISTING_CODE_NOT_RECORDED_IN_SNAPSHOT"
    if column_id == "unlisted_company":
        return "CURRENT_LISTING_STATUS_NOT_ESTABLISHED"
    if policy_method == "verified_site_point":
        return "NO_VERIFIED_MINE_OR_DEPOSIT_POINT"
    if policy_method == "verified_source_manifest":
        if column_id == "source_name" and (row["project_id"], "有來源文件") in metadata.cells:
            return "SOURCE_ATTRIBUTION_NOT_RECORDED"
        return metadata.source_reasons.get(row["project_id"]) or _SOURCE_REASONS.get(
            row["source_search_status"], "NO_VERIFIED_PROJECT_SOURCE",
        )
    source_reason = _SOURCE_REASONS.get(row["source_search_status"])
    if source_reason:
        return source_reason
    if definition["display_group"] in {"resource", "oxide", "aggregate"} and not has_resource:
        return "NO_SELECTED_MRE"
    return "NO_APPROVED_SELECTION"


def _enrich_cell(row, definition, metadata, has_resource):
    row.update({field: "" for field in STATUS_FIELDS if field not in row})
    row.update(
        column_id=definition["column_id"], data_role=definition["data_role"],
        projection_policy_version=metadata.policy.version, projection_policy_sha256=metadata.policy.sha256,
    )
    supplied = metadata.cells.get((row["project_id"], row["column"]))
    if supplied is not None and metadata.can_project(definition) and row["value_origin"] in {"", "IDENTITY"}:
        row.update(supplied)
    if row["value_origin"] == "FORMAL_SELECTION":
        row["evidence_state"] = "FORMAL_DIRECT"
        if row["reason"] == _CENSUS_ONLY_REASON:
            row["metadata_evidence"] = canonical_json({"research_use": CENSUS_RESOURCE_USE,
                                                        "admission_policy": CENSUS_RESOURCE_RULE})
        row["precision"] = row["precision"] or canonical_json({
            "mode": "EXACT_SOURCE_TOKEN", "token": row["raw_value"], "unit": row["raw_unit"],
        })
    if row["value_state"] == "PRESENT":
        row["lineage_id"] = projected_lineage_id(row)
    elif row["value_state"] == "UNKNOWN":
        row["reason"] = _unknown_reason(row, definition, metadata, has_resource)
        row["evidence_state"] = {
            "SOURCE_UNAVAILABLE": "SOURCE_UNAVAILABLE", "NOT_SEARCHED": "NOT_SEARCHED",
            "TEMPORAL_INELIGIBLE": "TEMPORAL_INELIGIBLE", "CONFLICTING_SELECTED_CONTEXT": "SCIENTIFIC_CONFLICT",
            "SCIENTIFIC_CONFLICT": "SCIENTIFIC_CONFLICT",
        }.get(row["reason"], "EVIDENCE_INSUFFICIENT")
    return row


def _rows(axes, searches, selected, decisions, metadata):
    census = []
    status = []
    definitions = metadata.policy.by_name if metadata is not None else {}
    resource_projects = {
        project for (project, column), row in selected.items()
        if column in COLUMNS.values() and row.get("value_origin") in FORMAL_CENSUS_ORIGINS
    }
    for project in axes.project_ids:
        cells = [
            _field_status(axes, project, column, searches, selected, decisions) for column in axes.columns
        ]
        if metadata is not None:
            cells = [_enrich_cell(row, definitions[row["column"]], metadata, project in resource_projects) for row in cells]
        census.append({row["column"]: row["value"] for row in cells})
        status.extend(cells)
    return census, status


def _csv_bytes(fields, rows):
    buffer = StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def _read_csv(path, headings):
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != headings:
            raise ValueError(f"Census headings do not match contract in {path.name}")
        rows = list(reader)
    if any(None in row or None in row.values() for row in rows):
        raise ValueError(f"Census row width differs from the contract in {path.name}")
    return rows


def projected_lineage_id(cell: dict) -> str:
    fields = {key: "" if value is None else str(value) for key, value in cell.items() if key != "lineage_id"}
    return sha256(canonical_json(fields).encode("utf-8")).hexdigest()


def validate_projected_cell(cell, definition):
    state, value = cell["value_state"], cell["value"]
    if state not in {"PRESENT", "UNKNOWN", "NOT_APPLICABLE"} or not cell["evidence_state"]:
        raise ValueError("Census projection requires both cell and evidence states")
    if state == "NOT_APPLICABLE":
        evidence = json.loads(cell["metadata_evidence"] or "{}")
        if evidence.get("applicability") != "AFFIRMATIVELY_INAPPLICABLE":
            raise ValueError("Census applicability requires affirmative evidence")
    if state != "PRESENT":
        return
    if cell["lineage_id"] != projected_lineage_id(cell) or cell["column_id"] != definition["column_id"]:
        raise ValueError("Census projection lacks column-level lineage")
    kind = definition["data_type"]
    if kind in {"decimal", "integer"}:
        try:
            number = Decimal(value)
            if not number.is_finite() or (kind == "integer" and number != number.to_integral_value()):
                raise ValueError("Census cell requires a finite number of its declared type")
        except InvalidOperation as error:
            raise ValueError("Census cell requires its declared numeric type") from error
    elif kind == "boolean_text" and value not in {"是", "否"}:
        raise ValueError("Census flag requires explicit yes or no")
    elif kind == "https_url" and public_source_url(value) is None:
        raise ValueError("Census URL is not a public HTTPS reference")
    if definition["source_edition_required"] and not SHA256.fullmatch(cell["source_sha256"]):
        raise ValueError("Census projection lacks an exact source edition")
    if cell["evidence_state"] == "VALIDATED_DESCRIPTIVE":
        from .project_attributes import validate_attribute_cell

        if not metadata_projection(definition, include_attributes=True) or definition["data_role"] != "VERIFIED_METADATA":
            raise ValueError("Census attribute is outside its metadata projection")
        validate_attribute_cell(cell)
    if cell["evidence_state"] in {"FORMAL_DIRECT", "FORMAL_DERIVED"}:
        if not cell["temporal_decision_id"] or cell["temporal_status"] != "ELIGIBLE":
            raise ValueError("formal Census cell requires a temporal decision")
        if cell["evidence_state"] == "FORMAL_DIRECT" and not all(
            cell[key] for key in ("selection_id", "observation_id", "anchor_id")
        ):
            raise ValueError("direct Census cell requires formal selection lineage")
        if cell["evidence_state"] == "FORMAL_DERIVED":
            from .census_derivations import FORMULA_INPUT_COLUMNS

            inputs = json.loads(cell["input_observation_ids"] or "[]")
            required = FORMULA_INPUT_COLUMNS.get(cell["column"])
            if (
                cell["value_origin"] != "FORMAL_DERIVED" or cell["comparable"] != "NO"
                or not cell["formula_id"] or not SHA256.fullmatch(cell["formula_sha256"])
                or required is None or not isinstance(inputs, list) or len(inputs) != len(required)
                or any(not isinstance(item, str) or not SHA256.fullmatch(item) for item in inputs)
                or len(set(inputs)) != len(required) or not cell["precision"]
            ):
                raise ValueError("derived Census cell requires an approved formula and distinct governed inputs")
    elif not cell["metadata_evidence"]:
        raise ValueError("display Census cell requires its bounded metadata evidence")


def _check_outputs(out, axes, expected_status, metadata):
    data = _read_csv(out / "census.csv", axes.columns)
    cells = _read_csv(out / "cell_status.csv", STATUS_FIELDS if metadata is not None else STATUS_FIELDS_V1)
    if tuple(row[IDENTITY_COLUMN] for row in data) != axes.project_ids:
        raise ValueError("Census project IDs or order do not match the registry")
    if len(cells) != len(expected_status) or len(cells) != PROJECTS * CENSUS_COLUMNS:
        raise ValueError("Census cell ledger size differs from the declared grid")

    actual_values = {(row[IDENTITY_COLUMN], col): row[col] for row in data for col in axes.columns}
    definitions = metadata.policy.by_name if metadata is not None else {}
    for cell, expected in zip(cells, expected_status, strict=True):
        identity = cell["project_id"], cell["column"]
        if any(cell[key] != ("" if value is None else str(value)) for key, value in expected.items()):
            raise ValueError(f"Census ledger lacks matching evidence or state for {identity}")
        if actual_values[identity] != cell["value"]:
            raise ValueError(f"Census value and ledger value disagree for {identity}")
        if cell["value_state"] == "PRESENT":
            if cell["value"] == "":
                raise ValueError(f"Present cell has no value for {identity}")
        elif cell["value"] != "" or not cell["reason"]:
            raise ValueError(f"Census missing value lacks a reason or has a value for {identity}")
        if metadata is not None:
            validate_projected_cell(cell, definitions[cell["column"]])
    return cells


def _prepared(profile, store, release_id, project_statuses, metadata):
    axes = CensusAxes.load(Path(profile))
    searches = project_statuses or {}
    if not set(searches).issubset(axes.project_ids) or any(
        not isinstance(status, str) or not status.strip() for status in searches.values()
    ):
        raise ContractViolation("Census search statuses require registered projects and nonempty source states")
    if metadata is not None and (
        (metadata.profile_sha256, metadata.registry_sha256) != (axes.profile_sha, axes.registry_sha)
        or tuple(metadata.policy.by_name) != axes.columns
        or any(project not in axes.project_ids or column not in axes.columns for project, column in metadata.cells)
    ):
        raise ContractViolation("Census display metadata does not belong to the pinned axes")
    release_mode, selected, decisions, science_views = _selection_rows(
        store, release_id, axes, science=metadata is not None,
    )
    return axes, release_mode, *_rows(axes, searches, selected, decisions, metadata), science_views


def _receipt(out, axes, release_id, release_mode, cells, metadata, science_views):
    origins = Counter(row["value_origin"] for row in cells if row["value_state"] == "PRESENT")
    values_by_column = Counter(row["column"] for row in cells if row["value_origin"] in FORMAL_CENSUS_ORIGINS)
    states = Counter(row["value_state"] for row in cells)
    unknown = Counter(row["reason"] for row in cells if row["value_state"] == "UNKNOWN")
    source_statuses = Counter(row["source_search_status"] for row in cells if row["column"] == IDENTITY_COLUMN)
    receipt = {
        "schema_version": SCHEMA, "gate": "F3", "status": "PASS",
        "profile_id": axes.profile_id, "research_cutoff": axes.cutoff, "timezone": axes.timezone,
        "profile_sha256": axes.profile_sha,
        "registry_sha256": axes.registry_sha, "column_contract_sha256": axes.columns_sha,
        "release_id": release_id, "release_mode": release_mode,
        "projects": len(axes.project_ids), "columns": len(axes.columns), "cells": len(cells),
        "cell_states": {label: states[label] for label in ("PRESENT", "UNKNOWN", "NOT_APPLICABLE")},
        "identity_cells": origins["IDENTITY"],
        "formal_scientific_cells": sum(origins[origin] for origin in FORMAL_CENSUS_ORIGINS),
        "formal_projects": len({row["project_id"] for row in cells if row["value_origin"] in FORMAL_CENSUS_ORIGINS}),
        "formal_scientific_cells_by_column": dict(values_by_column),
        "unknown_reasons": dict(unknown), "project_source_search_statuses": dict(source_statuses),
        "outputs": {name: _sha(out / name) for name in ("census.csv", "cell_status.csv")},
    }
    if metadata is not None:
        receipt.update(
            schema_version=PROJECTION_SCHEMA, projection_policy_version=metadata.policy.version,
            projection_policy_sha256=metadata.policy.sha256, coverage=coverage_report(
                cells, metadata.policy, include_attributes=metadata.attribute_receipt is not None,
            ),
        )
        if metadata.attribute_receipt is not None:
            receipt["attribute_assessments"] = metadata.attribute_receipt
    if science_views is not None:
        receipt["science_views"] = science_views
    if origins["FORMAL_DERIVED"]:
        receipt["formal_derived_cells"] = origins["FORMAL_DERIVED"]
    return receipt


def build_census(profile: Path, out: Path, *, store: Store | None = None,
                 release_id: str | None = None, project_statuses: dict[str, str] | None = None,
                 metadata: CensusMetadata | None = None) -> dict:
    """Build the grid and verify its CSV values and state ledger before publishing a receipt."""
    axes, release_mode, census, expected_status, science_views = _prepared(
        profile, store, release_id, project_statuses, metadata,
    )
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".census-", dir=out) as staging:
        staged = Path(staging)
        (staged / "census.csv").write_bytes(_csv_bytes(axes.columns, census))
        fields = STATUS_FIELDS if metadata is not None else STATUS_FIELDS_V1
        (staged / "cell_status.csv").write_bytes(_csv_bytes(fields, expected_status))
        cells = _check_outputs(staged, axes, expected_status, metadata)
        receipt = _receipt(staged, axes, release_id, release_mode, cells, metadata, science_views)
        (staged / "census_receipt.json").write_text(canonical_json(receipt) + "\n", encoding="utf-8")
        for filename in ("census.csv", "cell_status.csv", "census_receipt.json"):
            (staged / filename).replace(out / filename)
    return receipt


def verify_census(profile: Path, out: Path, *, store: Store | None = None,
                  release_id: str | None = None, project_statuses: dict[str, str] | None = None,
                  metadata: CensusMetadata | None = None) -> dict:
    """Re-resolve governed inputs and verify a previously emitted Census receipt."""
    axes, release_mode, _, expected_status, science_views = _prepared(
        profile, store, release_id, project_statuses, metadata,
    )
    out = Path(out)
    cells = _check_outputs(out, axes, expected_status, metadata)
    receipt = _receipt(out, axes, release_id, release_mode, cells, metadata, science_views)
    found = json.loads((out / "census_receipt.json").read_text(encoding="utf-8"))
    if found != receipt:
        raise ValueError("Census receipt fingerprint or profile does not match")
    return receipt
