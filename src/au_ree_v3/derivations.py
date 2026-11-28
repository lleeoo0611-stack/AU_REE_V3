"""Registered, Census-only calculations over immutable release selections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
import json
import re
from typing import TYPE_CHECKING

from .comparability import TOTAL_REO_15, normalize_input
from .contracts import ContractViolation, DerivedValue, RawObservation, ResearchRelease, canonical_json
from .normalization import exact_unit_exponent, reported_interval
from .observations import digest
from .research_inputs import CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, ResearchInput, checked_research_inputs

if TYPE_CHECKING:
    from .store import Store

POLICY_VERSION = "approved-census-contained-treo-derivation-v1"
ASSESSMENT_VERSION = "census-derivation-assessment-v1"
PURPOSE = "CENSUS_FORMAL_DERIVATION"
RELEASE_REGISTRY_PIN = "census_derivation_registry"
VARIABLE = "contained_treo_kt"
_OUTPUT_UNIT = "kt TREO"
_VARIABLES = ("ore_mt", "grade")
_CATEGORIES = ("INDICATED_INFERRED_TOTAL", "MEASURED_INDICATED_INFERRED_TOTAL")
_RAW_UNITS = {"ore_mt": {"t", "kt", "Mt", "Bt"}, "grade": {"ppm", "%", "kg/t"}}
_DRY = re.compile(r"\b(?:tonnages?|resource estimates?)[^.]{0,80}\bdry (?:tonnage |weight )?basis\b", re.I)
_AMBIGUOUS_BASIS = re.compile(r"\b(?:whether|wet|not)\b", re.I)
_SIGFIGS = re.compile(r"\b(?:grades and tonnages|tonnages and grades)\s+are\s+rounded\s+to\s+(\d+)\s+significant figures\b", re.I)


@dataclass(frozen=True)
class Formula:
    formula_id: str
    expression: str
    factor: str
    grade_unit: str
    output_unit: str = _OUTPUT_UNIT
    purpose: str = PURPOSE

    @property
    def sha256(self) -> str:
        return digest(asdict(self))


_FORMULAS = (
    Formula("contained-treo-from-mt-ppm-v1", "ore_mt × grade_ppm × 0.001", "0.001", "ppm"),
    Formula("contained-treo-from-mt-pct-v1", "ore_mt × grade_pct × 10", "10", "%"),
)
FORMULA_REGISTRY_SHA256 = digest({
    "policy_version": POLICY_VERSION, "input_rule": CENSUS_RESOURCE_RULE,
    "formulas": [asdict(formula) for formula in _FORMULAS], "output_variable": VARIABLE,
    "material_and_grade_denominator": "DRY_WHOLE_ORE", "categories": _CATEGORIES,
    "treo_members": sorted(TOTAL_REO_15), "precision_policy": "evidenced-significant-figures-or-last-digit-v1",
})


def registered_formula(formula_id: str) -> Formula | None:
    return next((formula for formula in _FORMULAS if formula.formula_id == formula_id), None)


@dataclass(frozen=True)
class DerivationAssessment:
    assessment_id: str
    project_id: str
    release_id: str
    status: str
    reason: str
    scope_id: str | None = None
    deposit_id: str | None = None
    statement_id: str | None = None
    source_sha256: str | None = None
    source_key: str | None = None
    source_url: str | None = None
    effective_date: str | None = None
    publication_date: str | None = None
    research_cutoff: str | None = None
    resource_category: str | None = None
    cutoff: tuple[str, str, str] = ()
    treo_members: tuple[str, ...] = ()
    formula_id: str | None = None
    formula_sha256: str | None = None
    factor: str | None = None
    value: str | None = None
    rounding_interval: tuple[str, str] | None = None
    inputs: tuple[dict, ...] = ()
    schema_version: str = ASSESSMENT_VERSION
    purpose: str = PURPOSE
    policy_version: str = POLICY_VERSION
    formula_registry_sha256: str = FORMULA_REGISTRY_SHA256
    output_variable: str = VARIABLE
    output_unit: str = _OUTPUT_UNIT

    def to_dict(self) -> dict:
        return json.loads(canonical_json(asdict(self)))


def _receipt(project: str, release: ResearchRelease, status: str, reason: str, **payload) -> DerivationAssessment:
    draft = DerivationAssessment("", project, release.release_id, status, reason, research_cutoff=release.cutoff, **payload)
    contents = draft.to_dict()
    del contents["assessment_id"]
    return replace(draft, assessment_id=digest(contents))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _measure(token: str, unit: str, target: str, significant: int | None) -> tuple[Decimal, Decimal, Decimal]:
    values = reported_interval(token, significant_figures=significant)
    move = exact_unit_exponent(unit, target)
    with localcontext() as arithmetic:
        arithmetic.prec = max(len(value.as_tuple().digits) for value in values)
        return tuple(value.scaleb(move) for value in values)


def _calculate(ore, grade, factor) -> tuple[str, tuple[str, str]]:
    factor = Decimal(factor)
    with localcontext() as arithmetic:
        arithmetic.prec = max(len(x.as_tuple().digits) for x in ore) + max(len(x.as_tuple().digits) for x in grade) + 2
        value, low, high = (_decimal_text(a * b * factor) for a, b in zip(ore, grade))
    return value, (low, high)


def _raw_input(item: ResearchInput, payload: dict, target: str) -> tuple[dict, tuple[Decimal, Decimal, Decimal]]:
    raw, candidate, anchor = item.raw, item.candidate, item.anchor
    native = payload["native_table_binding"]
    position = native["value_anchor"]
    if (position.get("text") != raw.raw_value or position.get("physical_page") != anchor.page
            or not all(native.get(key) for key in ("header_anchor", "unit_anchor", "caption_anchors"))
            or native.get("contextual_holds") or not all((anchor.locator, anchor.table, anchor.row, anchor.column))
            or not all(type(position.get(key)) is int and position[key] > 0
                       for key in ("line", "start_column", "end_column"))
            or position["start_column"] > position["end_column"]):
        raise ValueError("EXACT_NATIVE_ROW_EVIDENCE_REQUIRED")
    if raw.raw_unit not in _RAW_UNITS[raw.variable]:
        raise ValueError("NATIVE_UNIT_OUTSIDE_APPROVED_FORMULA_INPUTS")
    significant = native.get("reported_significant_figures")
    context = [entry["quote"] for entry in payload["statement_context"]]
    disconnected = {entry["quote"] for entry in native.get("context_links_not_established", ())}
    quotes = [quote for quote in context if quote not in disconnected]
    if (not any(_DRY.search(quote) and not _AMBIGUOUS_BASIS.search(quote) for quote in quotes)
            or any(re.search(r"\bwet (?:tonnage |weight )?basis\b", quote, re.I) for quote in quotes)):
        raise ValueError("EXACT_DRY_WHOLE_ORE_EVIDENCE_REQUIRED")
    stated_precision = {int(match[1]) for quote in quotes for match in _SIGFIGS.finditer(quote)}
    if stated_precision != (set() if significant is None else {significant}):
        raise ValueError("PUBLISHER_SIGNIFICANT_FIGURES_EVIDENCE_MISMATCH")
    raw_values = _measure(raw.raw_value, raw.raw_unit, raw.raw_unit, significant)
    if tuple(map(Decimal, native["raw_rounding_interval"])) != raw_values[1:]:
        raise ValueError("STORED_NATIVE_ROUNDING_INTERVAL_MISMATCH")
    values = _measure(raw.raw_value, raw.raw_unit, target, significant)
    transformed = normalize_input(item, target)
    if (transformed.status != "NORMALIZED" or not transformed.verify() or candidate.unit != target
            or Decimal(candidate.value) != values[0] or Decimal(transformed.value) != values[0]
            or tuple(map(Decimal, transformed.rounding_interval)) != values[1:]):
        raise ValueError("NORMALIZED_VALUE_OR_PRECISION_MISMATCH")
    fields = {
        "variable": raw.variable, "observation_id": raw.observation_id, "raw_record_id": raw.raw_record_id,
        "selection_id": item.accepted.acceptance_id, "validation_id": item.validation.validation_id,
        "candidate_id": candidate.candidate_id, "anchor_id": anchor.anchor_id,
        "temporal_decision_id": item.temporal_trace["decision_id"], "raw_token": raw.raw_value, "raw_unit": raw.raw_unit,
        "raw_rounding_interval": tuple(map(_decimal_text, raw_values[1:])), "reported_significant_figures": significant,
        "normalized_value": _decimal_text(values[0]), "normalized_unit": target,
        "normalized_rounding_interval": tuple(map(_decimal_text, values[1:])),
        "physical_page": anchor.page, "table": anchor.table, "row": anchor.row, "column": anchor.column,
        "native_value_anchor": position,
        "original_payload_sha256": digest(payload),
    }
    return fields, values


def _scope_signature(item: ResearchInput) -> tuple:
    raw, anchor = item.raw, item.anchor
    return (raw.project_id, raw.scope_id, raw.deposit_id, item.scope.deposit_id, raw.statement_id, raw.source_key, raw.source_sha,
            raw.effective_date, raw.reported_publication_date, raw.category, anchor.page, anchor.table, anchor.row,
            item.validation.cutoff, item.temporal_trace.get("decision_id"))


def _scientific_scope(payload: dict) -> tuple:
    return (payload.get("reporting_standard"), payload.get("resource_class"), payload.get("resource_category"),
            payload.get("cutoff_grade"), payload.get("cutoff_unit"), payload.get("cutoff_analyte"),
            tuple(payload.get("treo_members", ())), payload.get("includes_stockpiles"),
            tuple(payload.get("combined_deposit_ids", ())))


def _selected_for_census(item: ResearchInput, release: ResearchRelease) -> bool:
    accepted, validation, candidate, raw, source, scope, anchor, trace = (
        item.accepted, item.validation, item.candidate, item.raw, item.source, item.scope, item.anchor, item.temporal_trace,
    )
    return (accepted.release_id == release.release_id and accepted.reason == CENSUS_RESOURCE_RULE
            and raw.project_id in release.cohort and accepted.validation_id == validation.validation_id
            and validation.outcome == "VALIDATED" and validation.rules_sha == release.rules_sha
            and validation.candidate_id == candidate.candidate_id and candidate.observation_id == raw.observation_id
            and candidate.raw_record_id in {None, raw.raw_record_id} and raw.status == "REPORTED"
            and raw.observation_role == "HISTORICAL_MEASUREMENT"
            and (raw.scope_id, raw.project_id) == (scope.scope_id, scope.project_id)
            and raw.deposit_id in {None, scope.deposit_id} and raw.anchor_id == anchor.anchor_id
            and (raw.source_key, raw.source_sha) == (source.source_key, source.sha256) == (anchor.source_key, anchor.source_sha)
            and trace.get("status") == "ELIGIBLE" and trace.get("source_sha256") == raw.source_sha
            and trace.get("decision_id") == validation.temporal_decision_id
            and trace.get("decision_id") in release.temporal_decision_ids)


def assess_census_pair(ore: ResearchInput, grade: ResearchInput, release: ResearchRelease) -> DerivationAssessment:
    """Assess the science, with Store authority required separately for admission."""
    pair = (ore, grade)
    raw = ore.raw
    attach = {"scope_id": raw.scope_id, "deposit_id": ore.scope.deposit_id, "statement_id": raw.statement_id,
              "source_sha256": raw.source_sha, "source_key": raw.source_key, "source_url": ore.source.url,
              "effective_date": raw.effective_date, "publication_date": raw.reported_publication_date,
              "resource_category": raw.category}

    def reject(reason: str, status: str = "EVIDENCE_INSUFFICIENT") -> DerivationAssessment:
        return _receipt(raw.project_id, release, status, reason, **attach)

    if release.release_mode != "FORMAL" or release.validation_status != "PASS" or not release.temporal_policy_version:
        return reject("FORMAL_TEMPORALLY_GOVERNED_RELEASE_REQUIRED")
    if tuple(item.raw.variable for item in pair) != _VARIABLES or ore.raw.raw_record_id == grade.raw.raw_record_id:
        return reject("DISTINCT_ORDERED_ORE_AND_GRADE_REQUIRED")
    if any(not _selected_for_census(item, release) for item in pair):
        return reject("EXACT_RELEASE_CENSUS_SELECTIONS_REQUIRED")
    if (_scope_signature(ore) != _scope_signature(grade) or not all((raw.statement_id, raw.effective_date))
            or ore.anchor.column == grade.anchor.column or ore.validation.cutoff != release.cutoff):
        return reject("SOURCE_STATEMENT_SCOPE_DATE_OR_NATIVE_ROW_MISMATCH", "SCIENTIFIC_CONFLICT")
    payloads = tuple(json.loads(item.raw.original_payload) for item in pair)
    try:
        contexts = tuple(_scientific_scope(payload) for payload in payloads)
        standard, resource_class, category, cutoff, unit, analyte, members, *_ = contexts[0]
        valid_cutoff = (analyte == "TREO" and unit in {"ppm", "%"} and
                        0 < Decimal(cutoff) <= (100 if unit == "%" else 1_000_000))
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return reject("UNREADABLE_SCIENTIFIC_CONTEXT")
    if contexts[0] != contexts[1]:
        return reject("RESOURCE_CATEGORY_CUTOFF_OR_CHEMISTRY_MISMATCH", "SCIENTIFIC_CONFLICT")
    if (standard != "JORC 2012" or resource_class != "MINERAL_RESOURCE" or category not in _CATEGORIES
            or category != raw.category or not valid_cutoff or tuple(sorted(members)) != tuple(sorted(TOTAL_REO_15))):
        return reject("JORC_SCOPE_EXACT_TREO_CUTOFF_AND_MEMBER_SET_REQUIRED")
    if any(item.raw.material_basis != "WHOLE_ORE" or item.raw.chemical_basis != "TREO"
           or item.candidate.material_basis != "WHOLE_ORE" or item.candidate.chemical_basis != "TREO"
           or payload.get("research_use") != CENSUS_RESOURCE_USE for item, payload in zip(pair, payloads)):
        return reject("WHOLE_ORE_CANONICAL_TREO_SELECTION_REQUIRED")
    if grade.raw.denominator_basis != "WHOLE_ORE" or grade.candidate.denominator_basis != "WHOLE_ORE":
        return reject("GRADE_DENOMINATOR_MUST_BE_WHOLE_ORE")
    if any(item.denominator_basis not in {None, "ABSOLUTE_RESOURCE_MASS"} for item in (ore.raw, ore.candidate)):
        return reject("ORE_DENOMINATOR_MUST_BE_ABSOLUTE_RESOURCE_MASS")
    formula = next((entry for entry in _FORMULAS if entry.grade_unit == grade.candidate.unit), None)
    if formula is None:
        return reject("UNREGISTERED_GRADE_UNIT")
    try:
        ore_input, ore_values = _raw_input(ore, payloads[0], "Mt")
        grade_input, grade_values = _raw_input(grade, payloads[1], formula.grade_unit)
    except (KeyError, TypeError, InvalidOperation):
        return reject("UNREADABLE_NATIVE_PRECISION_OR_LINEAGE")
    except ValueError as error:
        return reject(str(error), "SCIENTIFIC_CONFLICT")
    ore_anchor, grade_anchor = ore_input["native_value_anchor"], grade_input["native_value_anchor"]
    if (ore_anchor["line"] != grade_anchor["line"]
            or max(ore_anchor["start_column"], grade_anchor["start_column"])
            <= min(ore_anchor["end_column"], grade_anchor["end_column"])):
        return reject("SAME_PHYSICAL_ROW_AND_DISTINCT_VALUE_SPANS_REQUIRED", "SCIENTIFIC_CONFLICT")
    if ore_values[1] <= 0 or grade_values[1] <= 0 or grade_values[2] > (100 if formula.grade_unit == "%" else 1_000_000):
        return reject("INVALID_PHYSICAL_MASS_OR_GRADE_INTERVAL", "SCIENTIFIC_CONFLICT")
    value, interval = _calculate(ore_values, grade_values, formula.factor)
    return _receipt(raw.project_id, release, "APPROVED", "SAME_STATEMENT_APPROVED_CENSUS_TREO_DERIVATION",
                    **attach, cutoff=(cutoff, unit, analyte), treo_members=members,
                    formula_id=formula.formula_id, formula_sha256=formula.sha256, factor=formula.factor,
                    value=value, rounding_interval=interval, inputs=(ore_input, grade_input))


def verify_derivation_assessment(payload: dict) -> bool:
    """Verify the registered formula, digest, unit conversions and interval without SQLite."""
    try:
        receipt = DerivationAssessment(**payload)
        signed = receipt.to_dict()
        fingerprint = signed.pop("assessment_id")
        formula = registered_formula(receipt.formula_id)
        if (formula is None or fingerprint != digest(signed) or receipt.status != "APPROVED"
                or (receipt.schema_version, receipt.policy_version, receipt.purpose, receipt.formula_registry_sha256)
                != (ASSESSMENT_VERSION, POLICY_VERSION, PURPOSE, FORMULA_REGISTRY_SHA256)
                or (receipt.formula_sha256, receipt.factor, receipt.output_variable, receipt.output_unit)
                != (formula.sha256, formula.factor, VARIABLE, _OUTPUT_UNIT)):
            return False
        if tuple(item["variable"] for item in receipt.inputs) != _VARIABLES:
            return False
        measurements = []
        for entry, unit in zip(receipt.inputs, ("Mt", formula.grade_unit)):
            arguments = (entry["raw_token"], entry["raw_unit"], entry["reported_significant_figures"])
            raw_token, raw_unit, significant = arguments
            if raw_unit not in _RAW_UNITS[entry["variable"]]:
                return False
            raw = _measure(raw_token, raw_unit, raw_unit, significant)
            normalized = _measure(raw_token, raw_unit, unit, significant)
            if (list(map(_decimal_text, raw[1:])) != list(entry["raw_rounding_interval"])
                    or list(map(_decimal_text, normalized[1:])) != list(entry["normalized_rounding_interval"])
                    or _decimal_text(normalized[0]) != entry["normalized_value"] or unit != entry["normalized_unit"]):
                return False
            measurements.append(normalized)
        value, interval = _calculate(*measurements, formula.factor)
        return value == receipt.value and interval == tuple(receipt.rounding_interval)
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return False


def assess_census_treo(store: Store, release: ResearchRelease) -> tuple[DerivationAssessment, ...]:
    """Assess in-scope rows from the authoritative accepted observation chain."""
    selected = checked_research_inputs(store, release, purpose="CENSUS")
    grouped: dict[str, dict[str, list[ResearchInput]]] = defaultdict(lambda: defaultdict(list))
    for item in selected:
        if item.raw.variable == VARIABLE or item.accepted.reason == CENSUS_RESOURCE_RULE:
            grouped[item.raw.project_id][item.raw.variable].append(item)
    reported_scopes = {(raw.project_id, raw.scope_id) for raw in store.records(RawObservation)
                       if raw.variable == VARIABLE and raw.status == "REPORTED" and raw.raw_value is not None}
    results = []
    for project, by_variable in sorted(grouped.items()):
        if not any(by_variable.get(key) for key in _VARIABLES):
            continue
        pair = [by_variable.get(variable, ()) for variable in _VARIABLES]
        if any(len(items) != 1 for items in pair):
            results.append(_receipt(project, release, "EVIDENCE_INSUFFICIENT", "EXACT_ORE_AND_GRADE_PAIR_REQUIRED"))
            continue
        ore, grade = (items[0] for items in pair)
        if by_variable.get(VARIABLE) or (project, ore.raw.scope_id) in reported_scopes:
            results.append(_receipt(project, release, "EVIDENCE_INSUFFICIENT", "REPORTED_CONTAINED_TREO_TAKES_PRECEDENCE",
                                    scope_id=ore.raw.scope_id, statement_id=ore.raw.statement_id))
            continue
        results.append(assess_census_pair(ore, grade, release))
    return tuple(results)


def _value(assessment: DerivationAssessment) -> DerivedValue:
    formula = registered_formula(assessment.formula_id)
    return DerivedValue(derived_id=assessment.assessment_id, release_id=assessment.release_id,
                        project_id=assessment.project_id, scope_id=assessment.scope_id, variable=VARIABLE,
                        value=assessment.value, unit=_OUTPUT_UNIT, formula=formula.expression, transform_version=POLICY_VERSION,
                        factor=formula.factor, denominator_basis="ABSOLUTE_RESOURCE_MASS", material_basis="WHOLE_ORE",
                        chemical_basis="TREO", input_observation_ids=tuple(entry["raw_record_id"] for entry in assessment.inputs),
                        assessment=assessment.to_dict())


def _require_registry_pin(release: ResearchRelease) -> None:
    if (release.result_fingerprints or {}).get(RELEASE_REGISTRY_PIN) != FORMULA_REGISTRY_SHA256:
        raise ContractViolation("formal derivation requires the exact Formula Registry pin in its immutable release")


def persist_census_treo(store: Store, release: ResearchRelease) -> tuple[DerivationAssessment, ...]:
    """Persist approved calculations using the caller's existing Store transaction."""
    _require_registry_pin(release)
    assessments = assess_census_treo(store, release)
    for result in assessments:
        if result.status == "APPROVED":
            store.add(_value(result))
    return assessments


def validate_census_derivation(store: Store, release: ResearchRelease, value: DerivedValue) -> None:
    """Recompute the exact authorized payload for a Store write."""
    _require_registry_pin(release)
    expected = next((result for result in assess_census_treo(store, release)
                     if result.status == "APPROVED" and result.project_id == value.project_id), None)
    if expected is None or _value(expected).to_dict() != value.to_dict():
        raise ContractViolation("derived value lacks the exact approved Census assessment and input selection lineage")


def checked_census_derivations(store: Store, release: ResearchRelease) -> tuple[DerivedValue, ...]:
    """Read only Store-authorized rows with recomputed formula and exact input links."""
    records = store.derivations_for_release(release.release_id)
    formal = tuple(value for value in records if value.assessment is not None)
    if not formal:
        return ()
    _require_registry_pin(release)
    expected = {entry.assessment_id: _value(entry) for entry in assess_census_treo(store, release) if entry.status == "APPROVED"}
    for value in formal:
        approved = expected.get(value.derived_id)
        if approved is None or approved.to_dict() != value.to_dict() or (
            store.derivation_input_keys(value.derived_id) != value.input_observation_ids
        ):
            raise ContractViolation("stored derivation diverges from its approved Census assessment or exact inputs")
    return formal
