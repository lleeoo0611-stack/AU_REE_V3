"""Versioned comparability rules for governed Project-level criteria."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
import json
import re
from typing import TYPE_CHECKING, Iterable
import unicodedata

from .contracts import canonical_json
from .observations import CATEGORY, OXIDE_VARIABLES
from .research_inputs import ResearchInput

if TYPE_CHECKING:
    from .normalization import NormalizationResult
    from .semantics import SemanticContext

SEMANTIC_POLICY_VERSION = "criterion-comparability-v1"
RESOURCE_VARIABLES = ("ore_mt", "grade", "contained_treo_kt")
MREO_VARIABLE = "mreo_ppm"
MREO_BASIS = "PrNdDyTb_REO"
NDPR_VARIABLES = ("ndpr_ppm", "reported_resource_ndpr_ppm")
NDPR_SPECIES = ("Nd2O3", "Pr6O11")
TOTAL_REO_15 = (
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


def census_quantity(variable: str, value: str, unit: str) -> tuple[str, str]:
    """Project a compatible selection in the pinned Census column unit."""
    compatible = _RESOURCE_UNITS[variable]
    if unit not in compatible:
        raise ValueError("Observation unit cannot be projected to the Census column")
    if variable == "grade" and unit == "ppm":
        ppm = Decimal(value).as_tuple()
        value = format(Decimal((ppm.sign, ppm.digits, ppm.exponent - 4)), "f")
    return value, compatible[0]


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


def _resource_scope_rejection(bundle: tuple[ResearchInput, ...]) -> str | None:
    """Apply the same source/scope checks to direct and shadow resource use."""
    if len({_statement_identity(item) for item in bundle}) != 1:
        return "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH"
    if not all(map(_jorc_resource, bundle)):
        return "ISSUER_JORC_RESOURCE_AND_CUTOFF_REQUIRED"
    if len({resource_context(item) for item in bundle}) != 1:
        return "RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH"
    if not all(map(resource_observation_compatible, bundle)):
        return "RESOURCE_QUANTITY_BASIS_OR_UNIT_MISMATCH"
    return None


def resource_bundle(by_variable: dict[str, list[ResearchInput]]) -> Comparison:
    """Admit reported inventory only as part of a complete same-statement bundle."""
    if not all(by_variable.get(name) for name in RESOURCE_VARIABLES):
        return Comparison("INCOMPLETE_SELECTED_RESOURCE_BUNDLE")
    if any(len(by_variable[name]) != 1 for name in RESOURCE_VARIABLES):
        return Comparison("AMBIGUOUS_SELECTED_RESOURCE_BUNDLE")
    bundle = tuple(by_variable[name][0] for name in RESOURCE_VARIABLES)
    ore, grade, contained = bundle
    if (reason := _resource_scope_rejection(bundle)) is not None:
        return Comparison(reason, inputs=bundle)
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


_REO_FORM = re.compile(
    r"(?:In[- ]situ\s+)?TREO(?:\s*(?P<operation>[+−-])\s*(?P<oxide>Y2O3|Sc2O3|CeO2))?", re.I
)


def _treo_definitions(grade: ResearchInput, *, extended: bool = False) -> list[tuple[dict, list[str]]]:
    payload = json.loads(grade.raw.original_payload)
    context = payload.get("statement_context") or ()
    qualifier = None
    if extended:
        reported_label = payload.get("reported_chemical_label")
        if isinstance(reported_label, str):
            reported_label = unicodedata.normalize("NFKC", reported_label.strip())
            declaration = _REO_FORM.fullmatch(reported_label)
            if declaration and declaration["oxide"]:
                qualifier = (declaration["operation"].replace("−", "-"), declaration["oxide"].upper())
            elif reported_label.casefold() == "treo including y2o3":
                qualifier = ("includes", "Y2O3")
            elif declaration is None:
                return []
    definitions = []
    for reference in context:
        if not isinstance(reference, dict) or not isinstance(reference.get("quote"), str):
            continue
        quote = unicodedata.normalize("NFKC", reference["quote"].strip().rstrip("."))
        if extended:
            expanded = re.fullmatch(
                r"\*?TREO\s*=\s*Total Rare Earth Oxides\s*\(([^()]+)\)"
                r"\s*\+\s*Yttrium\s*\((Y2O3)\)", quote, flags=re.I,
            )
            if expanded:
                if qualifier not in {None, ("+", "Y2O3"), ("includes", "Y2O3")}:
                    continue
                quote = f"TREO = Total Rare Earth Oxides ({expanded[1]}, {expanded[2]})"
            else:
                declaration, equals, definition = quote.lstrip("*").partition("=")
                label = _REO_FORM.fullmatch(declaration.strip())
                if not equals or label is None:
                    continue
                explicit = (label["operation"].replace("−", "-"), label["oxide"].upper()) if label["oxide"] else None
                requested = None if qualifier and qualifier[0] == "includes" else qualifier
                if explicit != requested:
                    continue
                quote = f"TREO ={definition}"
        match = re.fullmatch(
            r"TREO\s*=\s*Total Rare Earth Oxides\s*(?:[–—-]\s*([^()]+)|\(([^()]+)\))",
            quote, flags=re.I,
        )
        if match:
            members = sorted(part.strip().upper() for part in (match[1] or match[2]).split(","))
            if qualifier is not None and ((qualifier[1] in members) != (qualifier[0] != "-")):
                return []
            definitions.append((reference, members))
    return definitions


def _treo_denominator(grade: ResearchInput) -> dict | None:
    """Require the disclosed 15-oxide denominator for this comparison group."""
    definitions = _treo_definitions(grade)
    expected = sorted(species.upper() for species in TOTAL_REO_15)
    if not definitions or any(species != expected for _, species in definitions):
        return None
    return {
        "chemical_species": sorted(TOTAL_REO_15),
        "definition_sha256": sha256(canonical_json(expected).encode("utf-8")).hexdigest(),
        "references": [reference for reference, _ in definitions],
    }


def composition_comparison(by_variable, resource: Comparison, rule: dict) -> Comparison:
    """Evaluate the existing exact same-statement S1 composition rule."""
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
        comparisons = {"R1": inventory, "S1": composition_comparison(by_variable, inventory, policy["s1_admission"])}
        if blocked_reason:
            comparisons = {code: Comparison(blocked_reason) for code in comparisons}
        results[project_id] = {
            code: comparisons.get(code, Comparison("CRITERION_ADMISSION_ADAPTER_NOT_MIGRATED"))
            for code in policy["criteria"]
        }
    return results


def _stated_treo_members(definitions: list[tuple[dict, list[str]]]) -> tuple[str, ...]:
    """Recognize only the exact, explicitly enumerated TREO members."""
    from .normalization import KNOWN_OXIDE_SPECIES

    names = {species.upper(): species for species in KNOWN_OXIDE_SPECIES}
    declarations = set()
    for _, members in definitions:
        if any(member not in names for member in members) or len(members) != len(set(members)):
            return ()
        declarations.add(tuple(sorted(names[member] for member in members)))
    return next(iter(declarations)) if len(declarations) == 1 else ()


def reported_ndpr_evidence(item: ResearchInput) -> dict | None:
    """Recognize a defined native whole-ore combined grade without splitting it."""
    raw, candidate = item.raw, item.candidate
    if (raw.variable not in NDPR_VARIABLES or raw.raw_unit != "ppm" or candidate.unit != "ppm"
            or raw.material_basis != "WHOLE_ORE" or candidate.material_basis != "WHOLE_ORE"
            or raw.denominator_basis != "WHOLE_ORE" or candidate.denominator_basis != "WHOLE_ORE"
            or raw.chemical_basis != "NdPr_OXIDES" or candidate.chemical_basis != raw.chemical_basis
            or not item.anchor.page or not item.anchor.table or not item.anchor.row
            or (item.anchor.column or "").casefold() != "ndpr"):
        return None
    definition = json.loads(raw.original_payload).get("oxide_definition")
    if not isinstance(definition, dict) or definition.get("page") != item.anchor.page:
        return None
    text = unicodedata.normalize("NFKC", str(definition.get("quote", ""))).strip().rstrip(".")
    match = re.fullmatch(r"NdPr\s*=\s*(?:NdPr Oxides?\s*)?\(?\s*(Nd2O3|Pr6O11)\s*\+\s*(Nd2O3|Pr6O11)\s*\)?", text, re.I)
    if match is None or set(part.casefold() for part in match.groups()) != {part.casefold() for part in NDPR_SPECIES}:
        return None
    unit_evidence = json.loads(raw.original_payload).get("unit_evidence")
    if not isinstance(unit_evidence, dict) or unit_evidence.get("page") != item.anchor.page:
        return None
    if not re.fullmatch(r"NdPr\s*\(ppm\)", str(unit_evidence.get("quote", "")).strip(), re.I):
        return None
    return {"definition": definition, "unit_evidence": unit_evidence}


def _resource_semantics_and_evidence(item: ResearchInput) -> tuple["SemanticContext", tuple[dict, ...]]:
    from .normalization import KNOWN_OXIDE_SPECIES
    from .semantics import SemanticContext

    raw = item.raw
    payload = json.loads(raw.original_payload)
    chemical_basis = raw.chemical_basis
    definitions = _treo_definitions(item, extended=True) if chemical_basis == "TREO" else []
    references = [dict(reference) for reference, _ in definitions]
    species = ()
    if chemical_basis in KNOWN_OXIDE_SPECIES or chemical_basis in {"Nd", "Pr", "Dy", "Tb"}:
        species = (chemical_basis,)
    elif raw.variable == MREO_VARIABLE and _reported_aggregate_compatible(item):
        species = tuple(OXIDE_VARIABLES)
        references.append(payload["oxide_definition"])
    elif raw.variable in NDPR_VARIABLES and (evidence := reported_ndpr_evidence(item)) is not None:
        species = NDPR_SPECIES
        references.extend(evidence.values())
    kinds = {"ore_mt": "ORE_MASS", "grade": "GRADE", "contained_treo_kt": "CONTAINED_OXIDE_MASS"}
    categories = tuple(part for part in (raw.category or "").split("_")
                       if part in {"MEASURED", "INDICATED", "INFERRED"})
    material, denominator = raw.material_basis, raw.denominator_basis
    included_stockpiles = payload.get("includes_stockpiles")
    combined_deposits = payload.get("combined_deposit_ids")
    # Absolute resource mass is bound to its reported whole-ore scope.
    # It does not share the concentration denominator of grade.
    if denominator is None and material == "WHOLE_ORE" and raw.variable in {"ore_mt", "contained_treo_kt"}:
        denominator = "ABSOLUTE_RESOURCE_MASS"
    return SemanticContext(
        quantity_kind=kinds.get(raw.variable, "OXIDE_COMPOSITION"),
        project_id=raw.project_id, scope_id=raw.scope_id, deposit_id=raw.deposit_id,
        statement_id=raw.statement_id,
        resource_class=("MINERAL_RESOURCE" if raw.category in _RESOURCE_TOTALS else
                        "ORE_RESERVE" if "RESERVE" in (raw.category or "") else
                        "EXPLORATION_TARGET" if "TARGET" in (raw.category or "") else None),
        jorc_categories=categories, cutoff_grade=payload.get("cutoff_grade"),
        cutoff_unit=payload.get("cutoff_unit"), cutoff_analyte=payload.get("cutoff_analyte"),
        effective_date=raw.effective_date,
        includes_stockpiles=included_stockpiles if type(included_stockpiles) is bool else None,
        combined_deposit_ids=tuple(combined_deposits) if isinstance(combined_deposits, list) else (),
        material=material, denominator_basis=denominator, unit=raw.raw_unit,
        chemical_species=species, treo_members=_stated_treo_members(definitions),
        temporal_status=item.temporal_trace.get("status"), research_cutoff=item.validation.cutoff,
        temporal_decision_id=item.temporal_trace.get("decision_id"),
        observation_role=raw.observation_role,
        value_state="PRESENT" if raw.raw_value is not None else "NOT_DISCLOSED",
    ), tuple(references)


def resource_semantic_context(item: ResearchInput) -> "SemanticContext":
    """Project governed raw resource context without inferring absent chemistry."""
    context, _ = _resource_semantics_and_evidence(item)
    return context


def normalize_input(
    item: ResearchInput, target_unit: str, *, target_context: "SemanticContext | None" = None,
) -> "NormalizationResult":
    """Return exact Decimal normalization and its bound provenance for an input."""
    from .normalization import normalize_measurement

    context, definitions = _resource_semantics_and_evidence(item)
    return normalize_measurement(
        item.raw.raw_value, item.raw.raw_unit, target_unit, source_context=context, context_evidence=definitions,
        target_context=target_context, observation_id=item.raw.raw_record_id,
        source_sha256=item.source.sha256, evidence_anchor_id=item.anchor.anchor_id,
    )


@dataclass(frozen=True)
class ContainedMassShadow:
    """A proposed derived research quantity, kept outside the formal R1 path."""

    shadow_id: str
    status: str
    reason: str
    input_observation_ids: tuple[str, ...]
    evidence_anchor_ids: tuple[str, ...]
    project_id: str
    source_sha256: str
    scope_id: str
    value: str | None = None
    unit: str = "kt TREO"
    context: dict | None = None
    derivation: dict | None = None
    schema_version: str = "contained-mass-shadow-v1"
    layer: str = "SHADOW"

    def to_dict(self) -> dict:
        return asdict(self)


def _shadow_receipt(
    *, project_id: str, source_sha256: str, scope_id: str, input_observation_ids: tuple[str, ...],
    evidence_anchor_ids: tuple[str, ...], input_fingerprints: tuple[str, ...], status: str, reason: str,
    value: str | None = None, context: dict | None = None, derivation: dict | None = None,
) -> ContainedMassShadow:
    payload = {
        "schema_version": "contained-mass-shadow-v1", "layer": "SHADOW", "project_id": project_id,
        "source_sha256": source_sha256, "scope_id": scope_id, "input_observation_ids": input_observation_ids,
        "evidence_anchor_ids": evidence_anchor_ids, "unit": "kt TREO", "value": value,
        "status": status, "reason": reason, "context": context, "derivation": derivation,
    }
    fingerprint = {**payload, "input_fingerprints": input_fingerprints}
    return ContainedMassShadow(sha256(canonical_json(fingerprint).encode()).hexdigest(), **payload)


def contained_mass_shadow(
    ore: "NormalizationResult", grade: "NormalizationResult", *, reported: "NormalizationResult | None" = None,
) -> ContainedMassShadow:
    """Test precisely normalized ore×grade under an explicitly evidenced MRE."""
    from .semantics import SemanticContext, assess_semantic_use

    transforms = (ore, grade) if reported is None else (ore, grade, reported)
    requested_grade = grade.target_context
    common = {
        "project_id": requested_grade.get("project_id") or "", "scope_id": requested_grade.get("scope_id") or "",
        "source_sha256": grade.source_sha256,
        "input_observation_ids": tuple(item.observation_id for item in transforms),
        "evidence_anchor_ids": tuple(dict.fromkeys(item.evidence_anchor_id for item in transforms)),
        "input_fingerprints": tuple(item.transform_id for item in transforms),
    }

    def result(status, reason, value=None, context=None, derivation=None):
        return _shadow_receipt(**common, status=status, reason=reason, value=value, context=context, derivation=derivation)

    if not all(item.verify() for item in transforms):
        return result("SCIENTIFIC_CONFLICT", "INPUT_TRANSFORM_RECEIPT_DIGEST_MISMATCH")
    contexts = tuple(SemanticContext(**item.target_context) for item in transforms)
    grade_context = contexts[1]
    derivation = {"input_transforms": [item.to_dict() for item in transforms]}
    if (refused := next((item for item in transforms if item.status != "NORMALIZED"), None)) is not None:
        return result(refused.status, refused.reason, derivation=derivation)
    required = (("ORE_MASS", "Mt"), ("GRADE", "%"), ("CONTAINED_OXIDE_MASS", "kt TREO"))
    if tuple((context.quantity_kind, item.target_unit) for context, item in zip(contexts, transforms)) != required[:len(transforms)]:
        return result("NOT_COMPARABLE", "ORE_GRADE_AND_OPTIONAL_REPORTED_MASS_REQUIRED", derivation=derivation)
    if len(set(common["input_observation_ids"])) != len(transforms):
        return result("NOT_COMPARABLE", "DISTINCT_MEASUREMENT_OBSERVATIONS_REQUIRED", derivation=derivation)
    scopes = {(item.project_id, item.scope_id, item.deposit_id, item.statement_id, item.effective_date,
               item.includes_stockpiles, item.combined_deposit_ids, item.research_cutoff) for item in contexts}
    if not grade_context.research_cutoff:
        return result("INSUFFICIENT_CONTEXT", "RESEARCH_CUTOFF_NOT_BOUND", derivation=derivation)
    if len({item.source_sha256 for item in transforms}) != 1 or len(scopes) != 1:
        return result("NOT_COMPARABLE", "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH", derivation=derivation)
    if any(item.resource_class not in {None, "MINERAL_RESOURCE"} for item in contexts):
        return result("NOT_COMPARABLE", "JORC_MINERAL_RESOURCE_REQUIRED", derivation=derivation)
    if any(item.resource_class is None for item in contexts):
        return result("INSUFFICIENT_CONTEXT", "RESOURCE_CLASS_UNKNOWN", derivation=derivation)
    categories_and_cutoffs = {
        (item.jorc_categories, item.cutoff_grade, item.cutoff_unit, item.cutoff_analyte) for item in contexts
    }
    if len(categories_and_cutoffs) != 1:
        return result("NOT_COMPARABLE", "RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH", derivation=derivation)
    if any(item.material != "WHOLE_ORE" for item in contexts) or grade_context.denominator_basis != "WHOLE_ORE":
        return result("NOT_COMPARABLE", "RESOURCE_WHOLE_ORE_MATERIAL_AND_DENOMINATOR_REQUIRED", derivation=derivation)
    if any(item.observation_role != "HISTORICAL_MEASUREMENT" for item in contexts):
        return result("NOT_COMPARABLE", "HISTORICAL_MEASUREMENT_REQUIRED", derivation=derivation)
    members = grade_context.treo_members
    explicit = {item.treo_members for item in contexts if item.treo_members}
    if not members:
        return result("INSUFFICIENT_CONTEXT", "EXACT_TREO_MEMBER_DEFINITION_REQUIRED", derivation=derivation)
    if len(explicit) != 1:
        return result("SCIENTIFIC_CONFLICT", "TREO_MEMBER_DEFINITIONS_DISAGREE", derivation=derivation)
    if any(item.chemical_species not in {(), ("TREO",), members} for item in contexts):
        return result("NOT_COMPARABLE", "RESOURCE_TREO_QUANTITY_CHEMISTRY_MISMATCH", derivation=derivation)
    mass_denominators = {None, "ABSOLUTE_RESOURCE_MASS"}
    if contexts[0].denominator_basis not in mass_denominators or (
        reported and contexts[2].denominator_basis not in mass_denominators
    ):
        return result("NOT_COMPARABLE", "RESOURCE_QUANTITY_DENOMINATOR_MISMATCH", derivation=derivation)

    ore_amount, grade_amount = (Decimal(item.value) for item in transforms[:2])
    ore_low, ore_high = map(Decimal, ore.rounding_interval)
    grade_low, grade_high = map(Decimal, grade.rounding_interval)
    if ore_amount <= 0 or grade_amount <= 0:
        return result("NOT_COMPARABLE", "POSITIVE_RESOURCE_MASS_AND_GRADE_REQUIRED", derivation=derivation)
    with localcontext() as arithmetic:
        arithmetic.prec = sum(len(value.as_tuple().digits) for value in (ore_low, ore_high, grade_low, grade_high)) + 12
        point = ore_amount * grade_amount * 10
        lower, upper = ore_low * grade_low * 10, ore_high * grade_high * 10
    derivation.update({
        "method": "same-statement-ore-grade-contained-mass-v1",
        "formula_version": "same-statement-ore-grade-contained-mass-v1",
        "input_observation_ids": common["input_observation_ids"],
        "evidence_anchor_ids": common["evidence_anchor_ids"],
        "formula": "ore_mass_Mt × grade_pct × 10",
        "factor": "10",
        "rounding_interval_kt": [format(lower, "f"), format(upper, "f")],
        "result_role": "DERIVED",
        "formal_admission": False,
    })
    if reported:
        reported_low, reported_high = map(Decimal, reported.rounding_interval)
        derivation["reported_agreement"] = (
            "ROUNDING_INTERVALS_OVERLAP" if lower <= reported_high and reported_low <= upper else "CONFLICT"
        )
        if derivation["reported_agreement"] == "CONFLICT":
            return result("SCIENTIFIC_CONFLICT", "REPORTED_CONTAINED_MASS_CONTRADICTS_DERIVATION", derivation=derivation)
    output_context = replace(
        grade_context, quantity_kind="CONTAINED_OXIDE_MASS",
        denominator_basis="ABSOLUTE_RESOURCE_MASS", unit="kt TREO",
    )
    assessment = assess_semantic_use(
        output_context, purpose="R1", observation_ids=common["input_observation_ids"],
        source_sha256=common["source_sha256"], evidence_anchor_ids=common["evidence_anchor_ids"],
        transforms=(derivation,),
    )
    context = assessment.to_dict()
    if assessment.status not in {"COMPARABLE_DIRECT", "COMPARABLE_DERIVED"}:
        return result(assessment.status, "DERIVED_RESOURCE_USE_CONTRACT_NOT_MET", context=context, derivation=derivation)
    return result("COMPARABLE_DERIVED", "SAME_STATEMENT_ORE_GRADE_ROUNDING_CHECKED",
                  format(point, "f"), context, derivation)


def shadow_contained_mass(
    ore: ResearchInput, grade: ResearchInput, *, reported: ResearchInput | None = None,
) -> ContainedMassShadow:
    """Run a read-only derived R1 check using same-release research inputs."""
    bundle = (ore, grade) if reported is None else (ore, grade, reported)

    def rejection(reason):
        fingerprints = tuple(
            sha256(canonical_json({"raw": item.raw.to_dict(), "validation": item.validation.to_dict(),
                                   "candidate": item.candidate.to_dict()}).encode()).hexdigest()
            for item in bundle
        )
        return _shadow_receipt(
            project_id=ore.raw.project_id, scope_id=ore.scope.scope_id, source_sha256=ore.source.sha256,
            input_observation_ids=tuple(item.raw.raw_record_id for item in bundle),
            evidence_anchor_ids=tuple(item.anchor.anchor_id for item in bundle),
            input_fingerprints=fingerprints, status="NOT_COMPARABLE", reason=reason,
        )

    if tuple(item.raw.variable for item in bundle) != RESOURCE_VARIABLES[:len(bundle)]:
        return rejection("ORE_GRADE_AND_OPTIONAL_REPORTED_MASS_REQUIRED")
    if (len({item.accepted.release_id for item in bundle}) != 1
            or len({item.validation.cutoff for item in bundle}) != 1
            or len({item.raw.project_id for item in bundle}) != 1
            or any(item.anchor.anchor_id != item.raw.anchor_id or item.anchor.source_sha != item.source.sha256
                   or item.raw.source_key != item.source.source_key or item.raw.source_sha != item.source.sha256
                   or item.temporal_trace.get("decision_id") != item.validation.temporal_decision_id
                   for item in bundle)):
        return rejection("RELEASE_PROJECT_OR_EVIDENCE_CHAIN_MISMATCH")
    if (reason := _resource_scope_rejection(bundle)) is not None:
        return rejection(reason)
    transforms = tuple(normalize_input(item, unit) for item, unit in zip(bundle, ("Mt", "%", "kt TREO")))
    return contained_mass_shadow(*transforms[:2], reported=transforms[2] if reported else None)
