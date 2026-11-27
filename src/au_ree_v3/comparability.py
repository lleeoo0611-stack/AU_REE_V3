"""Versioned comparability rules for governed Project-level criteria."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import re
from typing import Iterable
import unicodedata

from .contracts import canonical_json
from .observations import CATEGORY, OXIDE_VARIABLES
from .research_inputs import ResearchInput

SEMANTIC_POLICY_VERSION = "criterion-comparability-v1"
RESOURCE_VARIABLES = ("ore_mt", "grade", "contained_treo_kt")
MREO_VARIABLE = "mreo_ppm"
MREO_BASIS = "PrNdDyTb_REO"
_TREO_SPECIES = (
    "La2O3", "CeO2", "Pr6O11", "Nd2O3", "Sm2O3", "Eu2O3", "Gd2O3", "Tb4O7",
    "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3", "Yb2O3", "Lu2O3", "Y2O3",
)
_RESOURCE_UNITS = {
    "ore_mt": ("Mt",),
    "grade": ("%", "ppm"),
    "contained_treo_kt": ("kt TREO", "kt"),
}
_RESOURCE_TOTALS = {CATEGORY, "MEASURED_INDICATED_TOTAL", "INDICATED_INFERRED_TOTAL"}
_RAW_FACTORS = {
    ("t", "Mt"): Decimal("0.000001"),
    ("kt", "Mt"): Decimal("0.001"),
    ("ppm", "%"): Decimal("0.0001"),
    ("%", "ppm"): Decimal("10000"),
    ("million tonnes", "Mt"): Decimal(1),
}


@dataclass(frozen=True)
class Comparison:
    reason: str
    value: str | None = None
    unit: str | None = None
    source_token: str | None = None
    inputs: tuple[ResearchInput, ...] = ()
    context: dict | None = None
    derivation: dict | None = None

    @property
    def comparable(self) -> bool:
        return self.value is not None


def resource_observation_compatible(item: ResearchInput) -> bool:
    """Identify a comparable reported resource field without requiring a full bundle."""
    raw, candidate = item.raw, item.candidate
    units = _RESOURCE_UNITS.get(raw.variable, ())
    return (
        raw.category in _RESOURCE_TOTALS
        and raw.observation_role == "HISTORICAL_MEASUREMENT"
        and item.validation.comparable
        and candidate.value is not None
        and candidate.unit in units
        and candidate.material_basis == "WHOLE_ORE"
        and candidate.chemical_basis == "TREO"
        and (raw.variable != "grade" or candidate.denominator_basis == "WHOLE_ORE")
    )


def census_quantity(item: ResearchInput) -> tuple[str, str]:
    """Project a compatible selection in the pinned Census column unit."""
    canonical = _RESOURCE_UNITS[item.raw.variable][0]
    value = item.candidate.value
    if item.raw.variable == "grade" and item.candidate.unit == "ppm":
        ppm = Decimal(value).as_tuple()
        value = format(Decimal((ppm.sign, ppm.digits, ppm.exponent - 4)), "f")
    return value, canonical


def _statement_identity(item: ResearchInput) -> tuple:
    return (item.scope.scope_id, item.raw.statement_id, item.raw.effective_date,
            item.source.source_key, item.source.sha256)


def _cutoff(item: ResearchInput) -> tuple:
    payload = json.loads(item.raw.original_payload)
    grade = payload.get("cutoff_grade")
    try:
        grade = Decimal(str(grade)) if grade is not None else None
        if grade is not None and (not grade.is_finite() or grade < 0):
            grade = None
    except InvalidOperation:
        grade = None
    unit, analyte = payload.get("cutoff_unit"), payload.get("cutoff_analyte")
    return grade, unit if isinstance(unit, str) else None, analyte if isinstance(analyte, str) else None


def resource_context(item: ResearchInput) -> tuple:
    """Provide a comparable edition/scope key without rewriting original context."""
    return (*_statement_identity(item), item.raw.category, *_cutoff(item))


def scientific_context(item: ResearchInput) -> dict:
    raw = item.raw
    payload = json.loads(raw.original_payload)
    return {
        "quantity": raw.variable,
        "statement_id": raw.statement_id,
        "scope_id": raw.scope_id,
        "effective_date": raw.effective_date,
        "category": raw.category,
        "reporting_standard": payload.get("reporting_standard"),
        "material_basis": item.candidate.material_basis,
        "chemical_basis": item.candidate.chemical_basis,
        "denominator_basis": item.candidate.denominator_basis,
        "cutoff_grade": payload.get("cutoff_grade"),
        "cutoff_unit": payload.get("cutoff_unit"),
        "cutoff_analyte": payload.get("cutoff_analyte"),
        "reported_chemical_label": payload.get("reported_chemical_label"),
        "process_endpoint": payload.get("process_endpoint"),
        "process_scale": payload.get("process_scale"),
        "source_sha256": item.source.sha256,
    }


def _reported(item: ResearchInput) -> tuple[Decimal, Decimal]:
    """Resolve the exact normalized quantity and the last-reported-digit half-width."""
    raw_token = item.raw.raw_value
    if not raw_token or item.candidate.value is None:
        raise ValueError("A reported token and normalized quantity are required")
    raw, normalized = Decimal(raw_token.replace(",", "")), Decimal(item.candidate.value)
    raw_unit, unit = item.raw.raw_unit, item.candidate.unit
    factor = Decimal(1) if raw_unit == unit else _RAW_FACTORS.get((raw_unit, unit))
    if factor is None or not raw.is_finite() or not normalized.is_finite():
        raise ValueError("Unsupported reported quantity conversion")
    with localcontext() as context:
        context.prec = len(raw.as_tuple().digits) + len(factor.as_tuple().digits) + 12
        if raw * factor != normalized:
            raise ValueError("Normalized quantity differs from the reported token")
        half = Decimal((0, (5,), raw.as_tuple().exponent - 1)) * factor
    return normalized, half


def _jorc_resource(item: ResearchInput) -> bool:
    payload = json.loads(item.raw.original_payload)
    reporting = str(payload.get("reporting_standard") or "").upper().replace("_", " ")
    grade, unit, analyte = _cutoff(item)
    lookup = (unit, analyte)
    # A scope filter is distinct from the chemistry of the reported quantity.
    # The two non-TREO filters are interpreted only for explicitly disclosed
    # whole-ore TREO measurements, never for a phase concentration.
    approved_cutoff = lookup in {("%", "TREO"), ("ppm", "TREO")} or (
        lookup == ("%", "THM") and payload.get("reported_chemical_label") == "In-Situ TREO + Y2O3"
    ) or (
        lookup == ("ppm", "TREO-CeO2") and payload.get("reported_chemical_label") == "TREO including Y2O3"
    )
    return (
        reporting == "JORC 2012"
        and item.source.kind == "pdf"
        and bool(item.raw.statement_id and item.raw.effective_date)
        and grade is not None and approved_cutoff
    )


def resource_bundle(by_variable: dict[str, list[ResearchInput]]) -> Comparison:
    """Admit reported inventory only as part of a complete same-statement bundle."""
    if not all(by_variable.get(name) for name in RESOURCE_VARIABLES):
        return Comparison("INCOMPLETE_SELECTED_RESOURCE_BUNDLE")
    if any(len(by_variable[name]) != 1 for name in RESOURCE_VARIABLES):
        return Comparison("AMBIGUOUS_SELECTED_RESOURCE_BUNDLE")
    bundle = tuple(by_variable[name][0] for name in RESOURCE_VARIABLES)
    ore, grade, contained = bundle
    if len({_statement_identity(item) for item in bundle}) != 1:
        return Comparison("RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH", inputs=bundle)
    if not all(map(_jorc_resource, bundle)):
        return Comparison("ISSUER_JORC_RESOURCE_AND_CUTOFF_REQUIRED", inputs=bundle)
    if len({resource_context(item) for item in bundle}) != 1:
        return Comparison("RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH", inputs=bundle)
    if not all(map(resource_observation_compatible, bundle)):
        return Comparison("RESOURCE_QUANTITY_BASIS_OR_UNIT_MISMATCH", inputs=bundle)
    try:
        ore_value, ore_half = _reported(ore)
        grade_value, grade_half = _reported(grade)
        inventory = Decimal(contained.candidate.value)
    except (InvalidOperation, TypeError, ValueError):
        return Comparison("RESOURCE_REPORTED_PRECISION_UNAVAILABLE", inputs=bundle)
    percent = grade.candidate.unit == "%"
    if not (ore_value > 0 and 0 < grade_value <= (100 if percent else 1_000_000) and inventory > 0):
        return Comparison("INVALID_RESOURCE_QUANTITIES", inputs=bundle)
    factor = Decimal("10" if percent else "0.001")
    with localcontext() as context:
        context.prec = sum(len(value.as_tuple().digits) for value in (
            ore_value, grade_value, inventory, ore_half, grade_half,
        )) + 12
        low = (ore_value - ore_half) * (grade_value - grade_half) * factor
        high = (ore_value + ore_half) * (grade_value + grade_half) * factor
    if not low <= inventory <= high:
        return Comparison("REPORTED_CONTAINED_OUTSIDE_ROUNDING_INTERVAL", inputs=bundle)
    return Comparison(
        "RELEASE_BOUND_COMPARABLE_SELECTION", contained.candidate.value, contained.candidate.unit,
        contained.raw.raw_value, bundle, scientific_context(contained),
    )


def _oxide_compatible(item: ResearchInput, species: str) -> bool:
    return (
        item.raw.variable == OXIDE_VARIABLES[species]
        and item.raw.observation_role == "HISTORICAL_MEASUREMENT"
        and item.validation.comparable
        and item.candidate.unit == "ppm"
        and item.candidate.chemical_basis == species
        and item.candidate.material_basis == "WHOLE_ORE"
        and item.candidate.denominator_basis == "WHOLE_ORE"
    )


def _reported_aggregate_compatible(item: ResearchInput) -> bool:
    payload = json.loads(item.raw.original_payload)
    definition = payload.get("oxide_definition")
    if not isinstance(definition, dict) or definition.get("page") != item.anchor.page:
        return False
    quote = unicodedata.normalize("NFKC", str(definition.get("quote", "")))
    match = re.search(r"\bMREO\s*=\s*Magnet Rare Earth Oxides?\s*\(([^)]+)\)", quote, flags=re.I)
    if match is None:
        return False
    declared = sorted(part.strip().upper() for part in match[1].split(","))
    species = payload.get("oxide_species")
    return (
        isinstance(species, list)
        and all(isinstance(part, str) for part in species)
        and sorted(species) == sorted(OXIDE_VARIABLES)
        and declared == sorted(name.upper() for name in OXIDE_VARIABLES)
        and item.raw.observation_role == "HISTORICAL_MEASUREMENT"
        and item.validation.comparable
        and item.candidate.unit == "ppm"
        and item.candidate.chemical_basis == MREO_BASIS
        and item.candidate.material_basis == "WHOLE_ORE"
        and item.candidate.denominator_basis == "WHOLE_ORE"
    )


def _verified_aggregate_units(
    item: ResearchInput, ore: ResearchInput, grade: ResearchInput, denominator_ppm: Decimal,
) -> dict | None:
    if item.raw.raw_value and re.search(
        rf"\bMREO\s*[:=]?\s*{re.escape(item.raw.raw_value)}\s*ppm\b",
        item.anchor.quote or "", flags=re.I,
    ):
        return {"physical_page": item.anchor.page, "quote": item.anchor.quote, "unit": "ppm"}

    review = json.loads(item.raw.original_payload).get("reviewed_companion_image")
    if not isinstance(review, dict) or (
        review.get("version") != "fixed-original-combined-oxide-table-review-v1"
        or review.get("source_sha256") != item.source.sha256
        or review.get("scope") != item.scope.name
        or review.get("effective_date") != item.raw.effective_date
        or review.get("group_unit") != "ppm" or review.get("grade_unit") != "ppm"
        or not re.fullmatch(r"[0-9a-f]{64}", str(review.get("image_sha256") or ""))
        or type(review.get("physical_page")) is not int or review["physical_page"] < 1
        or not review.get("locator")
    ):
        return None
    try:
        cutoff_value, cutoff_unit, cutoff_analyte = review["cutoff"]
        if (Decimal(cutoff_value), cutoff_unit, cutoff_analyte) != _cutoff(grade):
            return None
        if Decimal(review["grade_token_ppm"]) != denominator_ppm:
            return None
        ore_value, ore_half = _reported(ore)
        if abs(Decimal(review["ore_token_mt"]) - ore_value) > ore_half:
            return None
        aggregate, half = _reported(item)
        groups = tuple(Decimal(review[key]) for key in ("nd_pr_token_ppm", "dy_tb_token_ppm"))
        if any(not value.is_finite() or value < 0 for value in groups):
            return None
        group_half = sum(Decimal((0, (5,), value.as_tuple().exponent - 1)) for value in groups)
        if abs(sum(groups) - aggregate) > half + group_half:
            return None
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return None
    return review


def _composition_numerator(by_variable):
    components = {species: by_variable.get(variable, ()) for species, variable in OXIDE_VARIABLES.items()}
    aggregates = by_variable.get(MREO_VARIABLE, ())
    if any(len(entries) > 1 for entries in (*components.values(), aggregates)):
        return {}, None, "AMBIGUOUS_COMPOSITION_SELECTION"
    if all(components.values()):
        parts = {species: entries[0] for species, entries in components.items()}
        if not all(_oxide_compatible(item, species) for species, item in parts.items()):
            return parts, None, "COMPOSITION_BASIS_UNIT_OR_RANGE_MISMATCH"
        return parts, "COMPONENTS", None
    if aggregates:
        aggregate, = aggregates
        parts = {"MREO": aggregate}
        if not _reported_aggregate_compatible(aggregate):
            return parts, None, "MREO_DEFINITION_OR_WHOLE_ORE_BASIS_UNBOUND"
        return parts, "REPORTED_MREO", None
    reason = ("COMPOSITION_FORMULA_EVIDENCE_MISSING" if by_variable.get("magnet_reo_share_pct")
              else "NO_COMPOSITION_SELECTION")
    return {}, None, reason


def _treo_denominator(grade: ResearchInput) -> dict | None:
    """Require the disclosed 15-oxide denominator for this comparison group."""
    context = json.loads(grade.raw.original_payload).get("statement_context") or ()
    definitions = []
    for reference in context:
        if not isinstance(reference, dict) or not isinstance(reference.get("quote"), str):
            continue
        quote = unicodedata.normalize("NFKC", reference["quote"].strip().rstrip("."))
        match = re.fullmatch(
            r"TREO\s*=\s*Total Rare Earth Oxides\s*(?:[–—-]\s*([^()]+)|\(([^()]+)\))",
            quote, flags=re.I,
        )
        if match:
            definitions.append((reference, sorted(part.strip().upper() for part in (match[1] or match[2]).split(","))))
    expected = sorted(species.upper() for species in _TREO_SPECIES)
    if not definitions or any(species != expected for _, species in definitions):
        return None
    return {
        "chemical_species": sorted(_TREO_SPECIES),
        "definition_sha256": sha256(canonical_json(expected).encode("utf-8")).hexdigest(),
        "references": [reference for reference, _ in definitions],
    }


def _composition(by_variable, resource: Comparison, rule: dict) -> Comparison:
    if not resource.comparable:
        return Comparison("SAME_STATEMENT_RESOURCE_REQUIRED")
    if (rule["unit"], rule["basis"], rule.get("resource_scope")) != ("%", ["PrNdDyTb_REO_DIV_TREO"], True):
        return Comparison("COMPOSITION_POLICY_UNSUPPORTED")
    parts, numerator_kind, gap = _composition_numerator(by_variable)
    ore, grade, _ = resource.inputs
    input_rows = (*resource.inputs, *parts.values())
    if gap:
        return Comparison(gap, inputs=input_rows if parts else ())
    if any(resource_context(item) != resource_context(ore) for item in parts.values()):
        return Comparison("COMPOSITION_SCOPE_STATEMENT_OR_EDITION_MISMATCH", inputs=input_rows)
    treo_definition = _treo_denominator(grade)
    if treo_definition is None:
        return Comparison("COMPOSITION_TREO_CHEMICAL_DEFINITION_UNBOUND", inputs=input_rows)
    try:
        quantities = {species: _reported(item) for species, item in parts.items()}
        denominator, denominator_half = _reported(grade)
    except (InvalidOperation, TypeError, ValueError):
        return Comparison("COMPOSITION_REPORTED_PRECISION_UNAVAILABLE", inputs=input_rows)
    reported_aggregate = numerator_kind == "REPORTED_MREO"
    unit_evidence = None
    with localcontext() as arithmetic:
        arithmetic.prec = 34
        if grade.candidate.unit == "%":
            denominator *= 10000
            denominator_half *= 10000
        if reported_aggregate:
            unit_evidence = _verified_aggregate_units(parts["MREO"], ore, grade, denominator)
            if unit_evidence is None:
                return Comparison("MREO_UNIT_OR_COMPANION_CONTEXT_UNBOUND", inputs=input_rows)
        numerator = sum(value for value, _ in quantities.values())
        if any(value < 0 for value, _ in quantities.values()) or not 0 <= numerator <= denominator:
            return Comparison("COMPOSITION_BASIS_UNIT_OR_RANGE_MISMATCH", inputs=input_rows)
        # Score the central value inferred from reported tokens, while keeping
        # the measurement precision as a separate interval.
        if denominator <= denominator_half:
            return Comparison("COMPOSITION_TREO_DENOMINATOR_UNDETERMINED", inputs=input_rows)
        percent = numerator / denominator * 100
        min_numerator = sum(max(Decimal(0), value - half) for value, half in quantities.values())
        max_numerator = sum(value + half for value, half in quantities.values())
        lower = min_numerator / (denominator + denominator_half) * 100
        upper = min(Decimal(100), max_numerator / (denominator - denominator_half) * 100)
    context = scientific_context(grade)
    context.update(quantity="magnet_reo_share_pct", chemical_basis=rule["basis"][0],
                   denominator_basis="TREO_WITHIN_WHOLE_ORE",
                   treo_denominator=treo_definition)
    derivation = {
        "formula": ("reported_MREO_ppm / TREO_ppm * 100" if reported_aggregate else
                    "(Pr6O11_ppm + Nd2O3_ppm + Dy2O3_ppm + Tb4O7_ppm) / TREO_ppm * 100"),
        "method": ("same-statement-reported-four-oxide-share-v1" if reported_aggregate else
                   "same-statement-four-oxide-share-v1"),
        "input_observation_ids": [parts[species].raw.observation_id for species in parts] + [grade.raw.observation_id],
        "numerator_ppm": str(numerator), "denominator_ppm": str(denominator),
        "rounding_interval_pct": [str(lower), str(upper)],
        "component_tokens": {species: item.raw.raw_value for species, item in parts.items()},
        "treo_token": grade.raw.raw_value, "treo_unit": grade.raw.raw_unit,
    }
    if reported_aggregate:
        derivation["reported_definition"] = json.loads(parts["MREO"].raw.original_payload)["oxide_definition"]
        derivation["reported_unit_evidence"] = unit_evidence
    return Comparison("SAME_STATEMENT_FOUR_OXIDE_DERIVATION", str(percent), "%", None,
                      input_rows, context, derivation)


def evaluate_comparability(
    project_ids: Iterable[str], governed_inputs: Iterable[ResearchInput], *, policy: dict,
    blocked_reason: str | None = None,
) -> dict[str, dict[str, Comparison]]:
    """Apply named scientific comparisons to accepted inputs without changing them."""
    by_project = defaultdict(lambda: defaultdict(list))
    for item in governed_inputs:
        by_project[item.raw.project_id][item.raw.variable].append(item)
    results = {}
    for project_id in project_ids:
        by_variable = by_project[project_id]
        inventory = resource_bundle(by_variable)
        comparisons = {"R1": inventory, "S1": _composition(by_variable, inventory, policy["s1_admission"])}
        if blocked_reason:
            comparisons = {code: Comparison(blocked_reason) for code in comparisons}
        results[project_id] = {
            code: comparisons.get(code, Comparison("CRITERION_ADMISSION_ADAPTER_NOT_MIGRATED"))
            for code in policy["criteria"]
        }
    return results
