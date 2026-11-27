"""Build a fully accounted Census from a fresh, temporally governed release."""

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from .census import COLUMNS
from .comparability import census_quantity, resource_context, resource_observation_compatible
from .contracts import ContractViolation, ResearchRelease, canonical_json
from .registry import PROJECT_ID_COLUMN, load_census_columns, load_profile, load_registry
from .research_inputs import ResearchInput, checked_research_inputs
from .store import Store

SCHEMA = "fresh-census-v1"
IDENTITY_COLUMN = PROJECT_ID_COLUMN
STATUS_FIELDS = (
    "project_id", "column", "value", "value_state", "reason", "value_origin", "source_search_status",
    "registry_sha256", "release_id", "selection_id", "validation_id", "candidate_id", "observation_id",
    "raw_record_id", "source_key", "source_sha256", "source_url", "anchor_id", "locator", "physical_page",
    "source_table", "source_row", "source_column", "scope_id", "scope", "statement_id", "effective_date",
    "category", "raw_value", "raw_unit", "unit", "material_basis", "chemical_basis", "denominator_basis",
    "rules_sha256", "temporal_policy_version", "temporal_status", "temporal_decision_id", "temporal_evidence_ids",
    "comparable", "context_diagnostics",
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


def _accepted_cell(entry: ResearchInput) -> dict:
    raw, candidate, source = entry.raw, entry.candidate, entry.source
    anchor = entry.anchor
    lineage, trace = entry.lineage(), entry.temporal_trace
    value, unit = census_quantity(raw.variable, candidate.value, candidate.unit)
    lineage.pop("temporal_decision")
    lineage.pop("project_id")
    return {
        **lineage, "value": value, "value_state": "PRESENT",
        "reason": "GOVERNED_SELECTION" if unit == candidate.unit else "GOVERNED_SELECTION_EXACT_UNIT_CONVERSION",
        "value_origin": "FORMAL_SELECTION",
        "source_url": source.url, "locator": anchor.locator, "physical_page": anchor.page,
        "source_table": anchor.table, "source_row": anchor.row, "source_column": anchor.column,
        "scope": entry.scope.name, "statement_id": raw.statement_id, "effective_date": raw.effective_date,
        "category": raw.category, "raw_value": raw.raw_value, "raw_unit": raw.raw_unit,
        "unit": unit, "material_basis": candidate.material_basis,
        "chemical_basis": candidate.chemical_basis, "denominator_basis": candidate.denominator_basis,
        "rules_sha256": entry.validation.rules_sha, "temporal_policy_version": trace["policy_version"],
        "temporal_status": trace["status"], "temporal_decision_id": trace["decision_id"],
        "temporal_evidence_ids": canonical_json(trace["matching_evidence_ids"]), "comparable": "YES",
    }


def _selection_rows(store: Store | None, release_id: str | None, axes: CensusAxes) -> tuple[str | None, dict, dict]:
    if (store is None) != (release_id is None):
        raise ContractViolation("Census store and release ID must be supplied together")
    if store is None:
        return None, {}, {}
    release = store.get(ResearchRelease, release_id)
    if release is None or release.cutoff != axes.cutoff or not set(release.cohort).issubset(axes.project_ids):
        raise ContractViolation("Census release must have the profile cutoff and registered projects")

    selected = defaultdict(list)
    decisions = defaultdict(list)
    for entry in checked_research_inputs(store, release, purpose="CENSUS"):
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
                projected[key] = _accepted_cell(alternatives[0])

    return release.release_mode, projected, decisions


def _field_status(axes, project, column, searches, selected, decisions):
    source_status = searches.get(project, "STATUS_NOT_PROVIDED")
    row = {field: "" for field in STATUS_FIELDS}
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


def _rows(axes, searches, selected, decisions):
    census = []
    status = []
    for project in axes.project_ids:
        cells = [
            _field_status(axes, project, column, searches, selected, decisions) for column in axes.columns
        ]
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


def _check_outputs(out, axes, expected_status):
    data = _read_csv(out / "census.csv", axes.columns)
    cells = _read_csv(out / "cell_status.csv", STATUS_FIELDS)
    if tuple(row[IDENTITY_COLUMN] for row in data) != axes.project_ids:
        raise ValueError("Census project IDs or order do not match the registry")
    if len(cells) != len(expected_status) or len(cells) != PROJECTS * CENSUS_COLUMNS:
        raise ValueError("Census cell ledger size differs from the declared grid")

    actual_values = {(row[IDENTITY_COLUMN], col): row[col] for row in data for col in axes.columns}
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
    return cells


def _prepared(profile, store, release_id, project_statuses):
    axes = CensusAxes.load(Path(profile))
    searches = project_statuses or {}
    if not set(searches).issubset(axes.project_ids) or any(
        not isinstance(status, str) or not status.strip() for status in searches.values()
    ):
        raise ContractViolation("Census search statuses require registered projects and nonempty source states")
    release_mode, selected, decisions = _selection_rows(store, release_id, axes)
    return axes, release_mode, *_rows(axes, searches, selected, decisions)


def _receipt(out, axes, release_id, release_mode, cells):
    origins = Counter(row["value_origin"] for row in cells if row["value_state"] == "PRESENT")
    values_by_column = Counter(row["column"] for row in cells if row["value_origin"] == "FORMAL_SELECTION")
    states = Counter(row["value_state"] for row in cells)
    unknown = Counter(row["reason"] for row in cells if row["value_state"] == "UNKNOWN")
    source_statuses = Counter(row["source_search_status"] for row in cells if row["column"] == IDENTITY_COLUMN)
    return {
        "schema_version": SCHEMA, "gate": "F3", "status": "PASS",
        "profile_id": axes.profile_id, "research_cutoff": axes.cutoff, "timezone": axes.timezone,
        "profile_sha256": axes.profile_sha,
        "registry_sha256": axes.registry_sha, "column_contract_sha256": axes.columns_sha,
        "release_id": release_id, "release_mode": release_mode,
        "projects": len(axes.project_ids), "columns": len(axes.columns), "cells": len(cells),
        "cell_states": {label: states[label] for label in ("PRESENT", "UNKNOWN", "NOT_APPLICABLE")},
        "identity_cells": origins["IDENTITY"],
        "formal_scientific_cells": origins["FORMAL_SELECTION"],
        "formal_projects": len({row["project_id"] for row in cells if row["value_origin"] == "FORMAL_SELECTION"}),
        "formal_scientific_cells_by_column": dict(values_by_column),
        "unknown_reasons": dict(unknown), "project_source_search_statuses": dict(source_statuses),
        "outputs": {name: _sha(out / name) for name in ("census.csv", "cell_status.csv")},
    }


def build_census(profile: Path, out: Path, *, store: Store | None = None,
                 release_id: str | None = None, project_statuses: dict[str, str] | None = None) -> dict:
    """Build the grid and verify its CSV values and state ledger before publishing a receipt."""
    axes, release_mode, census, expected_status = _prepared(profile, store, release_id, project_statuses)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".census-", dir=out) as staging:
        staged = Path(staging)
        (staged / "census.csv").write_bytes(_csv_bytes(axes.columns, census))
        (staged / "cell_status.csv").write_bytes(_csv_bytes(STATUS_FIELDS, expected_status))
        cells = _check_outputs(staged, axes, expected_status)
        receipt = _receipt(staged, axes, release_id, release_mode, cells)
        (staged / "census_receipt.json").write_text(canonical_json(receipt) + "\n", encoding="utf-8")
        for filename in ("census.csv", "cell_status.csv", "census_receipt.json"):
            (staged / filename).replace(out / filename)
    return receipt


def verify_census(profile: Path, out: Path, *, store: Store | None = None,
                  release_id: str | None = None, project_statuses: dict[str, str] | None = None) -> dict:
    """Re-resolve governed inputs and verify a previously emitted Census receipt."""
    axes, release_mode, _, expected_status = _prepared(profile, store, release_id, project_statuses)
    out = Path(out)
    cells = _check_outputs(out, axes, expected_status)
    receipt = _receipt(out, axes, release_id, release_mode, cells)
    found = json.loads((out / "census_receipt.json").read_text(encoding="utf-8"))
    if found != receipt:
        raise ValueError("Census receipt fingerprint or profile does not match")
    return receipt
