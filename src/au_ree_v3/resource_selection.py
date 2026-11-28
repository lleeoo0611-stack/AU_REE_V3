"""Propose resource selections under the existing Census and MCDA policies."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import json

from .comparability import TOTAL_REO_15, reported_inventory_bounds
from .normalization import reported_interval
from .observations import CATEGORY
from .research_inputs import CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE, RESOURCE_RULE
from .resource_context import BoundStatement, Measurement
from .resource_extraction import ResourceCell, ResourceStatement

_CENSUS_FIELDS = frozenset(("ore_mt", "grade"))
_RESOURCE_CATEGORIES = {CATEGORY, "INDICATED_INFERRED_TOTAL", "MEASURED_INDICATED_TOTAL"}
_NATIVE_FORMAL_USES = {CENSUS_RESOURCE_USE, MCDA_RESOURCE_USE}


def select_reported_resources(statements: list[BoundStatement]) -> tuple[dict[str, tuple[Measurement, ...]], dict[str, str]]:
    groups = defaultdict(list)
    selection = {}
    reasons = {}
    for item in statements:
        if item.statement.primary and item.decision.status == "ELIGIBLE":
            groups[item.project.entity_id].append(item)
    for project_id, choices in groups.items():
        if len({item.scope.scope_id for item in choices}) != 1:
            reasons[project_id] = "CONFLICTING_RESEARCH_SCOPES"
            continue
        if len(choices) > 1 and any(item.statement.effective_date is None for item in choices):
            reasons[project_id] = "ESTIMATE_ORDER_UNRESOLVED"
            continue
        effective = max(item.statement.effective_date or "" for item in choices)
        if any(
            date != effective and effective.startswith(date + "-")
            for date in (item.statement.effective_date for item in choices)
            if date
        ):
            reasons[project_id] = "ESTIMATE_ORDER_PRECISION_UNRESOLVED"
            continue
        current = [item for item in choices if (item.statement.effective_date or "") == effective]
        signatures = {
            tuple(
                (m.raw.variable, Decimal(m.candidate.value), m.candidate.unit,
                 m.candidate.chemical_basis, m.candidate.material_basis, m.validation.outcome)
                for m in item.measurements
            )
            for item in current
        }
        if len(signatures) != 1:
            reasons[project_id] = "CONFLICTING_SAME_DATE_ESTIMATES"
            continue
        chosen = min(current, key=lambda item: (item.source.sha256, item.source.source_key))
        selection[project_id] = tuple(m for m in chosen.measurements if m.validation.outcome == "VALIDATED")
        reasons[project_id] = "APPROVED_REPORTING_SCOPE"
    return selection, reasons


def _statement_issue(statement: ResourceStatement) -> tuple[str, str] | None:
    if not statement.primary or statement.resource_class != "MINERAL_RESOURCE" or statement.reporting_standard != "JORC 2012":
        return "REJECTED", "PRIMARY_JORC_MINERAL_RESOURCE_REQUIRED"
    if statement.category not in _RESOURCE_CATEGORIES:
        return "REJECTED", "APPROVED_PROJECT_RESOURCE_CATEGORY_REQUIRED"
    if statement.cutoff_analyte != "TREO" or statement.cutoff_unit not in {"ppm", "%"} or statement.cutoff is None:
        return "REJECTED", "EXACT_PRIMARY_TREO_CUTOFF_REQUIRED"
    if not 0 < Decimal(statement.cutoff) <= (100 if statement.cutoff_unit == "%" else 1_000_000):
        return "SCIENTIFIC_CONFLICT", "INVALID_PRIMARY_TREO_CUTOFF"
    if tuple(sorted(statement.treo_members)) != tuple(sorted(TOTAL_REO_15)):
        return "EVIDENCE_INSUFFICIENT", "EXACT_TREO_MEMBER_SET_REQUIRED"
    return None


def _native_cell_issue(measurement: Measurement) -> tuple[str, str] | None:
    raw = measurement.raw
    evidence = json.loads(raw.original_payload).get("native_table_binding")
    if (measurement.validation.outcome != "VALIDATED" or not isinstance(evidence, dict)
            or not all(evidence.get(key) for key in ("value_anchor", "header_anchor", "unit_anchor", "caption_anchors"))):
        return "EVIDENCE_INSUFFICIENT", "NATIVE_CELL_EVIDENCE_REQUIRED"
    if raw.material_basis != "WHOLE_ORE" or raw.chemical_basis != "TREO" or (
        raw.variable == "grade" and raw.denominator_basis != "WHOLE_ORE"
    ):
        return "REJECTED", "WHOLE_ORE_TREO_QUANTITY_REQUIRED"
    units = {"ore_mt": {"Mt"}, "grade": {"ppm", "%"}, "contained_treo_kt": {"kt TREO"}}
    if measurement.candidate.unit not in units[raw.variable]:
        return "REJECTED", "CENSUS_RESOURCE_UNIT_NOT_SUPPORTED"
    max_grade = 100 if measurement.candidate.unit == "%" else 1_000_000
    value = Decimal(measurement.candidate.value)
    if value <= 0 or (raw.variable == "grade" and value > max_grade):
        return "SCIENTIFIC_CONFLICT", "CENSUS_RESOURCE_QUANTITY_OUT_OF_RANGE"
    return None


def _native_bundle_issue(measurements: tuple[Measurement, ...], full: bool) -> tuple[str, str] | None:
    needed = _CENSUS_FIELDS | {"contained_treo_kt"} if full else _CENSUS_FIELDS
    if len(measurements) != len(needed) or {cell.raw.variable for cell in measurements} != needed:
        return "REJECTED", "EXACT_REPORTED_RESOURCE_BUNDLE_REQUIRED" if full else "EXACT_ORE_AND_GRADE_PAIR_REQUIRED"
    anchors = {(cell.anchor.page, cell.anchor.table, cell.anchor.row) for cell in measurements}
    if (len(anchors) != 1 or not all(next(iter(anchors)))
            or len({cell.anchor.column for cell in measurements}) != len(needed)):
        return "EVIDENCE_INSUFFICIENT", "EXACT_SAME_ROW_AND_DISTINCT_FIELDS_REQUIRED"
    for cell in measurements:
        if issue := _native_cell_issue(cell):
            return issue
    return None


def _selected_precision(cell: ResourceCell) -> tuple[Decimal, Decimal]:
    raw, low, _ = reported_interval(cell.token)
    return Decimal(cell.value), (raw - low) * Decimal(cell.factor)


def _reported_bundle_verdict(statement: ResourceStatement) -> tuple[str, str]:
    cells = {cell.variable: cell for cell in statement.measurements}
    ore, grade, contained = (cells[name] for name in ("ore_mt", "grade", "contained_treo_kt"))
    ore_value, ore_half = _selected_precision(ore)
    grade_value, grade_half = _selected_precision(grade)
    inventory = Decimal(contained.value)
    low, high = reported_inventory_bounds(ore_value, ore_half, grade_value, grade_half, inventory,
                                           grade.normalized_unit == "%")
    if not low <= inventory <= high:
        return "SCIENTIFIC_CONFLICT", "REPORTED_CONTAINED_OUTSIDE_ROUNDING_INTERVAL"
    return "ADMISSIBLE", "COMPLETE_DIRECT_REPORTED_RESOURCE_BUNDLE"


def native_resource_verdict(item: BoundStatement) -> tuple[str, str]:
    if item.research_use not in _NATIVE_FORMAL_USES:
        return "CANDIDATE_ONLY", "NO_AUTHORIZED_NATIVE_RESOURCE_USE"
    if any(cell.validation.outcome == "SCIENTIFIC_CONFLICT" for cell in item.measurements):
        return "SCIENTIFIC_CONFLICT", "CONFLICTING_NATIVE_MEASUREMENT"
    if item.decision.status != "ELIGIBLE":
        return item.decision.status, item.decision.reason
    if issue := _statement_issue(item.statement):
        return issue
    full = item.research_use == MCDA_RESOURCE_USE
    if issue := _native_bundle_issue(item.measurements, full):
        return issue
    if full:
        return _reported_bundle_verdict(item.statement)
    return "ADMISSIBLE", "EXACT_DIRECT_CENSUS_RESOURCE_OBSERVATION"


@dataclass(frozen=True, slots=True)
class NativeAdmission:
    statement: BoundStatement
    verdict: str
    reason: str

    @property
    def policy(self) -> str:
        return RESOURCE_RULE if self.statement.research_use == MCDA_RESOURCE_USE else CENSUS_RESOURCE_RULE


@dataclass(frozen=True, slots=True)
class SelectionProposal:
    selected: dict[str, Measurement]
    reasons: dict[str, str]
    admissions: list[NativeAdmission]
    scoped_ids: set[str]

    def selection_rule(self, observation_id: str) -> str:
        return CENSUS_RESOURCE_RULE if observation_id in self.scoped_ids else RESOURCE_RULE


def _native_decisions(statements: list[BoundStatement], covered_projects: set[str]) -> list[NativeAdmission]:
    native = [item for item in statements if item.research_use is not None]
    requested = defaultdict(list)
    for item in native:
        if item.research_use in _NATIVE_FORMAL_USES and item.statement.primary:
            requested[item.project.entity_id].append(item)
    decisions = []
    for item in native:
        project_id = item.project.entity_id
        verdict, reason = native_resource_verdict(item)
        if verdict == "ADMISSIBLE":
            if project_id in covered_projects:
                verdict, reason = "REJECTED", "EXISTING_RESEARCH_STATEMENT_PRESERVED"
            elif len(requested[project_id]) != 1:
                verdict, reason = "SCIENTIFIC_CONFLICT", "ALTERNATIVE_CENSUS_RESOURCE_STATEMENTS_REQUIRE_RESOLUTION"
            else:
                verdict = "SELECTED"
        decisions.append(NativeAdmission(item, verdict, reason))
    return decisions


def propose_selections(statements: list[BoundStatement]) -> SelectionProposal:
    legacy = [item for item in statements if item.research_use is None]
    selection, reasons = select_reported_resources(legacy)
    legacy_primary = {item.project.entity_id for item in legacy if item.statement.primary}
    admissions = _native_decisions(statements, legacy_primary | selection.keys())
    scoped_ids = set()
    for decision in admissions:
        if decision.verdict != "SELECTED":
            continue
        item = decision.statement
        project_id = item.project.entity_id
        selection[project_id] = item.measurements
        reasons[project_id] = decision.policy
        if item.research_use == CENSUS_RESOURCE_USE:
            scoped_ids.update(cell.raw.observation_id for cell in item.measurements)
    selected = {item.raw.observation_id: item for items in selection.values() for item in items}
    return SelectionProposal(selected, reasons, admissions, scoped_ids)
