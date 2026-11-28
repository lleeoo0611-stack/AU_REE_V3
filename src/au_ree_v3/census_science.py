"""Read-only resource statements and evidence-bound Census composition candidates."""

from collections import defaultdict
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
from importlib.resources import files
import json

from .census import COLUMNS
from .comparability import (
    MREO_VARIABLE, NDPR_SPECIES, NDPR_VARIABLES, RESOURCE_VARIABLES, TOTAL_REO_15,
    composition_comparison, evaluate_comparability, normalize_input, reported_ndpr_evidence,
    resource_bundle, resource_context, resource_semantic_context, scientific_context, shadow_contained_mass,
)
from .contracts import ResearchRelease, canonical_json
from .fresh_decision import POLICY_SHA256, load_policy
from .normalization import reported_interval
from .observations import OXIDE_VARIABLES
from .research_inputs import CENSUS_RESOURCE_RULE, CENSUS_RESOURCE_USE, ResearchInput, checked_research_inputs


SCHEMA = "census-science-views-v1"
POLICY_VERSION = "census-composition-shadow-v1"
RESOURCE_PRESENCE_COLUMN = "有TREO資源估計"
MAGNET_DISCLOSURE_COLUMN = "四項磁性元素皆公布"
MAGNET_DISCLOSURE_REASON = "FOUR_MAGNET_OXIDES_IN_SCOPED_ORIGINAL"
MAGNET_DISCLOSURE_ROLE = "SCOPED_MAGNET_OXIDE_PUBLICATION"
MASS_FORMULA = "ore_mass_Mt × concentration_ppm × 0.001"
COMPONENT_FACTOR = Decimal("0.001")
MAGNET = tuple(sorted(OXIDE_VARIABLES))
HEAVY = tuple(sorted(("Tb4O7", "Dy2O3", "Ho2O3", "Er2O3", "Tm2O3", "Yb2O3", "Lu2O3", "Y2O3")))
SPECIES = tuple(sorted(TOTAL_REO_15))
_VARIABLES_BY_SPECIES = {species: OXIDE_VARIABLES.get(species, f"{species.lower()}_ppm") for species in SPECIES}
_STATEMENT_VARIABLES = {*RESOURCE_VARIABLES, MREO_VARIABLE, *NDPR_VARIABLES, *_VARIABLES_BY_SPECIES.values()}
_GROUPS = {
    "ndpr_kt": (NDPR_SPECIES, "NdPr蘊藏量_kt_需兩者皆公布"),
    "hree8_kt": (HEAVY, "完整重稀土組合蘊藏量_kt_需8元素皆公布"),
    "magnet_recalculated_kt": (MAGNET, "磁性元素重算值_NdPrTbDy_kt"),
    "complete_oxide_sum_kt": (SPECIES, "已公布元素總和_kt"),
}
_EXPECTED_FORMULAS = {
    "component_mass": MASS_FORMULA,
    "complete_group_mass": MASS_FORMULA.replace("concentration_ppm", "sum(member_ppm)"),
    "reported_group_mass": MASS_FORMULA,
    "complete_sum_ratio": "sum(member_ppm) / declared_total_TREO_ppm",
}
_ADMISSION_GAP = "NO_APPROVED_CENSUS_DERIVATION_SELECTION"
_PRECISION_CONFLICT = "SAME_SCOPE_COMPANION_OXIDE_PRECISION_DISAGREEMENT"
_REVIEWED_EVIDENCE_BYTES = files(__package__).joinpath("census_composition_evidence_v1.json").read_bytes()
_REVIEWED_EVIDENCE = json.loads(_REVIEWED_EVIDENCE_BYTES)
if _REVIEWED_EVIDENCE["schema"] != "reviewed-census-composition-evidence-v1":
    raise ValueError("Census composition precision review requires its versioned contract")
REVIEWED_COMPOSITION_EVIDENCE_SHA256 = sha256(_REVIEWED_EVIDENCE_BYTES).hexdigest()
_PPM_FACTORS = {"ppm": Decimal(1), "kg/t": Decimal(1000)}


def _digest(value):
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _lineage(item: ResearchInput) -> dict:
    return {
        **item.lineage(),
        "raw_token": item.raw.raw_value, "raw_unit": item.raw.raw_unit,
        "source_url": item.source.url,
        "normalized_value": item.candidate.value, "normalized_unit": item.candidate.unit,
        "material_basis": item.candidate.material_basis, "chemical_basis": item.candidate.chemical_basis,
        "denominator_basis": item.candidate.denominator_basis,
        "source_version": item.source.source_version,
        "physical_page": item.anchor.page, "table": item.anchor.table, "row": item.anchor.row,
        "column": item.anchor.column, "locator": item.anchor.locator,
        "effective_date": item.raw.effective_date, "statement_id": item.raw.statement_id,
        "validation_rules_sha256": item.validation.rules_sha,
    }


def _composition_scope_rejection(item: ResearchInput, base: ResearchInput) -> str | None:
    raw, normalized = item.raw, item.candidate
    if (resource_context(item) != resource_context(base)
            or item.source.source_version != base.source.source_version
            or raw.deposit_id != base.raw.deposit_id
            or item.accepted.release_id != base.accepted.release_id):
        return "OXIDE_EDITION_STATEMENT_SCOPE_DATE_OR_CUTOFF_MISMATCH"
    if (raw.observation_role != "HISTORICAL_MEASUREMENT" or not item.validation.comparable
            or raw.material_basis != "WHOLE_ORE" or normalized.material_basis != "WHOLE_ORE"
            or raw.denominator_basis != "WHOLE_ORE" or normalized.denominator_basis != "WHOLE_ORE"):
        return "OXIDE_WHOLE_ORE_QUANTITY_REQUIRED"
    source_context, base_context = resource_semantic_context(item), resource_semantic_context(base)
    if source_context.resource_class != "MINERAL_RESOURCE":
        return "OXIDE_JORC_MINERAL_RESOURCE_REQUIRED"
    if (source_context.includes_stockpiles, source_context.combined_deposit_ids) != (
            base_context.includes_stockpiles, base_context.combined_deposit_ids):
        return "OXIDE_RESOURCE_INCLUSION_SCOPE_MISMATCH"
    return None


def _eligible_component(item: ResearchInput, species: str, base: ResearchInput) -> str | None:
    if (reason := _composition_scope_rejection(item, base)) is not None:
        return reason
    normalized, anchor = item.candidate, item.anchor
    source_context = resource_semantic_context(item)
    if normalized.chemical_basis != species or source_context.chemical_species != (species,):
        return "OXIDE_SPECIES_MISMATCH"
    if (anchor.page is None or not anchor.table or not anchor.row or not anchor.column
            or anchor.column.casefold() != species.casefold()):
        return "OXIDE_NATIVE_TABLE_FIELD_UNBOUND"
    return None


def _amount(item: ResearchInput, target: str):
    """Normalize independently of candidate labels and check the governed quantity."""
    result = normalize_input(item, target)
    if result.status != "NORMALIZED" or not result.verify():
        return None, result.reason
    try:
        current = result if target == item.candidate.unit else normalize_input(item, item.candidate.unit)
        if current.status != "NORMALIZED" or Decimal(current.value) != Decimal(item.candidate.value):
            return None, "REPORTED_TOKEN_AND_GOVERNED_VALUE_DISAGREE"
    except (ValueError, ArithmeticError, TypeError):
        return None, "REPORTED_TOKEN_AND_GOVERNED_VALUE_DISAGREE"
    return result, None


def _numbers(transform):
    return (Decimal(transform.value), *map(Decimal, transform.rounding_interval))


def _product(ore, concentration):
    with localcontext() as arithmetic:
        numbers = (*ore, *concentration)
        arithmetic.prec = sum(len(value.as_tuple().digits) for value in numbers) + 12
        return tuple(a * b * COMPONENT_FACTOR for a, b in zip(ore, concentration, strict=True))


def _total(transforms):
    with localcontext() as arithmetic:
        numbers = [_numbers(item) for item in transforms]
        arithmetic.prec = sum(len(value.as_tuple().digits) for bounds in numbers for value in bounds) + 12
        return tuple(sum(group, Decimal(0)) for group in zip(*numbers, strict=True))


def _ratio(numerator, denominator):
    point, low, high = numerator
    base, base_low, base_high = denominator
    if base_low <= 0:
        return None
    with localcontext() as arithmetic:
        arithmetic.prec = 50
        arithmetic.rounding = ROUND_HALF_EVEN
        quotient = point / base
        arithmetic.rounding = ROUND_FLOOR
        lower = low / base_high
        arithmetic.rounding = ROUND_CEILING
        upper = high / base_low
    return quotient, lower, upper


def _formatted(numbers):
    value, low, high = numbers
    return format(value, "f"), [format(low, "f"), format(high, "f")]


def _candidate(key, heading, species, base, inputs, numbers, formula_type, transforms, *, unit="kt", evidence=None):
    equation = _EXPECTED_FORMULAS[formula_type]
    value, rounding_interval = _formatted(numbers)
    formula = {
        "id": formula_type, "version": POLICY_VERSION, "equation": equation,
        "chemical_species": list(species), "unit": unit,
        "factor": str(COMPONENT_FACTOR) if unit == "kt" else "1",
    }
    row = {
        "key": key, "census_column": heading, "value": value, "rounding_interval": rounding_interval, "unit": unit,
        "status": "SHADOW_VALIDATED", "formal_admission": False, "formal_admission_reason": _ADMISSION_GAP,
        "formula": {**formula, "sha256": _digest(formula)},
        "context": {**scientific_context(base), "treo_members": list(resource_semantic_context(base).treo_members)},
        "inputs": [_lineage(item) for item in inputs],
        "transforms": [item.to_dict() for item in transforms],
    }
    if evidence is not None:
        row["group_evidence"] = evidence
    return {"derivation_id": _digest(row), **row}


def _blocked(key, reason, *, members=(), inputs=()):
    state = "INSUFFICIENT_CONTEXT"
    if reason in {"INCONSISTENT_OXIDE_AND_TREO_ROUNDING_INTERVALS", "REPORTED_TOKEN_AND_GOVERNED_VALUE_DISAGREE",
                  "REPORTED_AND_COMPONENT_GROUP_ROUNDING_INTERVALS_DISAGREE", _PRECISION_CONFLICT}:
        state = "SCIENTIFIC_CONFLICT"
    elif any(token in reason for token in ("MISMATCH", "REQUIRED")) and not reason.startswith("REQUIRED_"):
        state = "NOT_COMPARABLE"
    return {"key": key, "status": state, "reason": reason,
            "required_members": list(members), "inputs": [_lineage(item) for item in inputs]}


def _component_columns(columns):
    return {species: next(heading for heading in columns if heading.startswith(species + "_"))
            for species in SPECIES}


def _overlap(first, second):
    return first[1] <= second[2] and second[1] <= first[2]


def _reviewed_precision_conflict(item, grade, species):
    anchor = item.anchor
    for review in _REVIEWED_EVIDENCE["reviews"]:
        cutoff = review["cutoff"]
        if (item.source.sha256 != review["source_sha256"] or grade.scope.name != review["scope"]
                or grade.raw.effective_date != review["effective_date"] or species != review["species"]
                or resource_context(grade)[-3:] != (Decimal(cutoff[0]), *cutoff[1:])):
            continue
        selected, companion = review["selected"], review["companion"]
        if (anchor.page, anchor.table, anchor.row, anchor.column, item.raw.raw_value, item.raw.raw_unit,
                " ".join((anchor.quote or "").split())) != (
            selected["physical_page"], selected["table"], selected["row"], selected["column"],
            selected["token"], selected["unit"], selected["row_quote"],
        ):
            continue
        bounds = {
            role: tuple(value * _PPM_FACTORS[reading["unit"]] for value in reported_interval(reading["token"]))
            for role, reading in (("selected", selected), ("companion", companion))
        }
        if not _overlap(bounds["selected"], bounds["companion"]):
            return {**review, "review_policy_sha256": REVIEWED_COMPOSITION_EVIDENCE_SHA256,
                    "nominal_rounding_intervals_ppm": {role: _formatted(values)[1] for role, values in bounds.items()}}
    return None


def _magnet_disclosure(by_variable, grade):
    """Verify presence of four native measurements for disclosure metadata."""
    if not set(MAGNET).issubset(resource_semantic_context(grade).treo_members):
        return None
    inputs, precision_holds = [], {}
    for species in MAGNET:
        rows = by_variable.get(OXIDE_VARIABLES[species], ())
        if len(rows) != 1:
            return None
        item = rows[0]
        if _eligible_component(item, species, grade) or _amount(item, "ppm")[0] is None:
            return None
        inputs.append(_lineage(item))
        if review := _reviewed_precision_conflict(item, grade, species):
            precision_holds[species] = review
    return {
        "value": "是", "release_id": grade.accepted.release_id, "display_only": True,
        "evidence_type": MAGNET_DISCLOSURE_ROLE,
        "statement": {**scientific_context(grade), "scope": grade.scope.name},
        "inputs": inputs, "numerical_precision_holds": precision_holds,
        "precision_review": "; ".join(
            f"{species}: " + " / ".join(
                f"{reading['token']} {reading['unit']} (p. {reading['physical_page']}, {reading['table']})"
                for reading in (review["selected"], review["companion"])
            ) for species, review in precision_holds.items()
        ),
    }


def _reported_group(grade, entries, matches, grade_numbers, *, computed):
    if len(entries) != 1:
        return None, "AMBIGUOUS_REPORTED_GROUP_SELECTION", None
    item, = entries
    gap = _composition_scope_rejection(item, grade)
    if gap is not None:
        return None, gap, None
    evidence = matches(item)
    if evidence is None:
        return None, "EXACT_REPORTED_GROUP_DEFINITION_AND_UNIT_REQUIRED", None
    normalized, gap = _amount(item, "ppm")
    if gap is not None:
        return None, gap, None
    numbers = _numbers(normalized)
    if numbers[1] > grade_numbers[2]:
        return None, "INCONSISTENT_OXIDE_AND_TREO_ROUNDING_INTERVALS", None
    if computed is not None and not _overlap(numbers, computed):
        return None, "REPORTED_AND_COMPONENT_GROUP_ROUNDING_INTERVALS_DISAGREE", None
    return normalized, None, evidence


def _derive_components(by_variable, resource, composition, columns, s1_rule):
    ore, grade, _ = resource.inputs
    ore_transform, ore_gap = _amount(ore, "Mt")
    grade_transform, grade_gap = _amount(grade, "ppm")
    if ore_gap or grade_gap:
        return [], [_blocked("oxide_mass", ore_gap or grade_gap, inputs=resource.inputs)]
    ore_numbers, grade_numbers = _numbers(ore_transform), _numbers(grade_transform)
    if ore_numbers[0] <= 0 or grade_numbers[0] <= 0:
        return [], [_blocked("oxide_mass", "POSITIVE_ORE_MASS_AND_TREO_REQUIRED", inputs=resource.inputs)]
    known_members = set(resource_semantic_context(grade).treo_members)
    headings = _component_columns(columns)
    failures, components, precision_conflicts = [], {}, {}
    for species, variable in _VARIABLES_BY_SPECIES.items():
        values = by_variable.get(variable, ())
        if len(values) > 1:
            failures.append(_blocked(variable, "AMBIGUOUS_OXIDE_SELECTION", members=(species,), inputs=values))
        elif values:
            item = values[0]
            gap = _eligible_component(item, species, grade)
            if gap is None and species not in known_members:
                gap = "OXIDE_NOT_IN_DECLARED_TREO_MEMBER_SET"
            transform, gap = (None, gap) if gap else _amount(item, "ppm")
            if gap:
                failures.append(_blocked(variable, gap, members=(species,), inputs=(ore, grade, item)))
                continue
            if (precision := _reviewed_precision_conflict(item, grade, species)) is not None:
                precision_conflicts[species] = item, precision
                conflict = _blocked(variable, _PRECISION_CONFLICT, members=(species,), inputs=(ore, grade, item))
                failures.append({**conflict, "source_precision_reviews": [precision]})
                continue
            components[species] = item, transform
    if components:
        selected_inputs, selected_transforms = zip(*components.values(), strict=True)
        selected_total = _total(selected_transforms)
        # A reported TREO balance must be feasible even when other members are missing.
        if selected_total[1] > grade_numbers[2]:
            conflict = _blocked("oxide_mass", "INCONSISTENT_OXIDE_AND_TREO_ROUNDING_INTERVALS",
                                members=tuple(components), inputs=(ore, grade, *selected_inputs))
            conflict["rounding_intervals_ppm"] = {
                "selected_oxide_sum": _formatted(selected_total)[1],
                "declared_treo": _formatted(grade_numbers)[1],
            }
            return [], [*failures, conflict]
    results = [
        _candidate(f"{species}_kt", headings[species], (species,), grade,
                   (ore, grade, item), _product(ore_numbers, _numbers(transform)),
                   "component_mass", (ore_transform, grade_transform, transform))
        for species, (item, transform) in components.items()
    ]
    group_result = {}
    for key, (members, heading) in _GROUPS.items():
        conflicts = [precision_conflicts[species] for species in members if species in precision_conflicts]
        if conflicts:
            conflict = _blocked(key, _PRECISION_CONFLICT, members=members,
                                inputs=(ore, grade, *(item for item, _ in conflicts)))
            failures.append({**conflict, "source_precision_reviews": [review for _, review in conflicts]})
            continue
        if key == "ndpr_kt" and any(by_variable.get(variable) for variable in NDPR_VARIABLES):
            continue
        if not all(species in components for species in members):
            failures.append(_blocked(key, "REQUIRED_OXIDE_MEMBERS_NOT_SELECTED",
                                     members=tuple(species for species in members if species not in components)))
            continue
        group_inputs, group_transforms = zip(*(components[species] for species in members), strict=True)
        numbers = _total(group_transforms)
        exact_group = key == "complete_oxide_sum_kt"
        if exact_group and known_members != set(members):
            failures.append(_blocked(key, "COMPLETE_SUM_REQUIRES_EXACT_CENSUS_TREO_MEMBERS", members=members,
                                     inputs=(ore, grade, *group_inputs)))
            continue
        if numbers[1] > grade_numbers[2] or (exact_group and numbers[2] < grade_numbers[1]):
            failures.append(_blocked(key, "INCONSISTENT_OXIDE_AND_TREO_ROUNDING_INTERVALS", members=members,
                                     inputs=(ore, grade, *group_inputs)))
            continue
        items = (ore, grade, *group_inputs)
        transforms = (ore_transform, grade_transform, *group_transforms)
        candidate = _candidate(key, heading, members, grade, items, _product(ore_numbers, numbers),
                               "complete_group_mass", transforms)
        results.append(candidate)
        group_result[key] = candidate, numbers
        if exact_group and (ratio := _ratio(numbers, grade_numbers)) is not None:
            results.append(_candidate("complete_sum_ratio", "元素總和/TREO", members, grade,
                                      items, ratio, "complete_sum_ratio", transforms, unit="ratio"))
    ndpr = tuple(item for variable in NDPR_VARIABLES for item in by_variable.get(variable, ()))
    if ndpr:
        components_ndpr = tuple(components[species][1] for species in NDPR_SPECIES if species in components)
        computed_ndpr = _total(components_ndpr) if len(components_ndpr) == len(NDPR_SPECIES) else None
        amount, gap, evidence = _reported_group(grade, ndpr, reported_ndpr_evidence, grade_numbers,
                                               computed=computed_ndpr)
        if gap:
            failures.append(_blocked("ndpr_kt", gap, members=NDPR_SPECIES, inputs=(ore, grade, *ndpr)))
        else:
            results.append(_candidate("ndpr_kt", _GROUPS["ndpr_kt"][1], NDPR_SPECIES, grade,
                                      (ore, grade, *ndpr), _product(ore_numbers, _numbers(amount)),
                                      "reported_group_mass", (ore_transform, grade_transform, amount), evidence=evidence))
    aggregate_inputs = tuple(by_variable.get(MREO_VARIABLE, ()))
    magnet_conflicts = [precision_conflicts[species] for species in MAGNET if species in precision_conflicts]
    if magnet_conflicts:
        conflict = _blocked("mreo_kt", _PRECISION_CONFLICT, members=MAGNET,
                            inputs=(ore, grade, *(item for item, _ in magnet_conflicts)))
        failures.append({**conflict, "source_precision_reviews": [review for _, review in magnet_conflicts]})
    elif aggregate_inputs:
        reported = composition_comparison({MREO_VARIABLE: aggregate_inputs}, resource, s1_rule)
        if not reported.comparable:
            failures.append(_blocked("mreo_kt", reported.reason, members=MAGNET, inputs=reported.inputs))
        else:
            computed = group_result.get("magnet_recalculated_kt")
            amount, gap, evidence = _reported_group(grade, aggregate_inputs, lambda _: reported.derivation, grade_numbers,
                                                   computed=computed[1] if computed else None)
            if gap:
                failures.append(_blocked("mreo_kt", gap, members=MAGNET, inputs=(ore, grade, *aggregate_inputs)))
            else:
                results.append(_candidate("mreo_kt", "磁鐵稀土氧化物蘊藏量_kt", MAGNET, grade,
                                          (ore, grade, *aggregate_inputs), _product(ore_numbers, _numbers(amount)),
                                          "reported_group_mass", (ore_transform, grade_transform, amount), evidence=evidence))
    elif composition.comparable:
        if "magnet_recalculated_kt" in group_result:
            # The evaluated S1 proof distinguishes this group from broad columns labelled rare-earth oxides.
            group, _ = group_result["magnet_recalculated_kt"]
            numbers = (Decimal(group["value"]), *map(Decimal, group["rounding_interval"]))
            items, transforms = zip(*(components[species] for species in MAGNET), strict=True)
            results.append(_candidate("mreo_kt", "磁鐵稀土氧化物蘊藏量_kt", MAGNET, grade,
                                      (ore, grade, *items), numbers, "complete_group_mass",
                                      (ore_transform, grade_transform, *transforms)))
    else:
        failures.append(_blocked("mreo_kt", composition.reason, members=MAGNET, inputs=composition.inputs))
    return results, failures


def census_science_views(store, release: ResearchRelease, columns: tuple[str, ...]) -> dict:
    inputs = checked_research_inputs(store, release, purpose="CENSUS")
    return project_science_views(inputs, release, columns)


def project_science_views(inputs: tuple[ResearchInput, ...], release: ResearchRelease,
                          columns: tuple[str, ...], *, member_disclosure: bool = False) -> dict:
    """Use the immutable selections to describe statements and derive shadow evidence."""
    formal = release.release_mode == "FORMAL" and release.validation_status == "PASS"
    grouped = defaultdict(lambda: defaultdict(list))
    for item in inputs:
        if item.raw.variable in _STATEMENT_VARIABLES:
            grouped[item.raw.project_id][item.raw.variable].append(item)
    policy = load_policy()
    decision_inputs = tuple(item for item in inputs if item.accepted.reason != CENSUS_RESOURCE_RULE)
    comparisons = evaluate_comparability(sorted(grouped), decision_inputs, policy=policy)
    statements, projections, derived, rejected = [], {}, [], []
    scoped_reports = {}
    magnet_disclosures = {}
    for project_id in sorted(grouped):
        values = grouped[project_id]
        for group in sorted({resource_context(item) for rows in values.values() for item in rows}, key=str):
            items = sorted((item for rows in values.values() for item in rows if resource_context(item) == group),
                           key=lambda item: (item.raw.variable, item.raw.observation_id))
            statements.append({"project_id": project_id, "statement": scientific_context(items[0]),
                               "observations": [_lineage(item) for item in items]})
        scoped = {item.raw.variable: item for rows in values.values() for item in rows
                  if item.accepted.reason == CENSUS_RESOURCE_RULE}
        if formal and set(scoped) == {"ore_mt", "grade"}:
            ore, grade = scoped["ore_mt"], scoped["grade"]
            scoped_reports[project_id] = {
                "research_use": CENSUS_RESOURCE_USE, "admission_policy": CENSUS_RESOURCE_RULE,
                "statement": {**scientific_context(grade), "scope": grade.scope.name,
                              "treo_members": resource_semantic_context(grade).treo_members},
                "inputs": [_lineage(ore), _lineage(grade)],
                "contained_shadow": shadow_contained_mass(ore, grade).to_dict(),
            }
        resource = resource_bundle(values)
        if not resource.comparable:
            continue
        _, grade, _ = resource.inputs
        if not formal:
            continue
        projections[project_id] = {"value": "是", "release_id": release.release_id,
                                   "statement": {**scientific_context(grade), "scope": grade.scope.name},
                                   "inputs": [_lineage(item) for item in resource.inputs]}
        if member_disclosure and (disclosure := _magnet_disclosure(values, grade)):
            magnet_disclosures[project_id] = disclosure
        if any(variable not in RESOURCE_VARIABLES for variable in values):
            results, failures = _derive_components(values, resource, comparisons[project_id]["S1"], columns,
                                                   policy["s1_admission"])
            derived.extend({"project_id": project_id, **item} for item in results)
            rejected.extend({"project_id": project_id, **item} for item in failures)
    s1 = [
        {"project_id": project_id, "value": compare.value, "unit": compare.unit,
         "reason": compare.reason, "decision_policy_sha256": POLICY_SHA256,
         "inputs": [_lineage(item) for item in compare.inputs], "derivation": compare.derivation,
         "census_admission": _ADMISSION_GAP}
        for project_id, row in comparisons.items() if formal and (compare := row["S1"]).comparable
    ]
    view = {
        "schema": SCHEMA, "policy_version": POLICY_VERSION, "release_id": release.release_id,
        "research_cutoff": release.cutoff, "release_mode": release.release_mode,
        "reviewed_composition_evidence_sha256": REVIEWED_COMPOSITION_EVIDENCE_SHA256,
        "formula_registry_sha256": _digest({"version": POLICY_VERSION, "formulas": _EXPECTED_FORMULAS,
                                           "groups": _GROUPS, "oxide_factor": str(COMPONENT_FACTOR),
                                           "formal_admission": False}),
        "resource_statements": statements, "verified_resource_presence": projections,
        "derived_candidates": derived, "rejected_derivations": rejected, "approved_mcda_s1_references": s1,
        "formal_selected_inputs": len(inputs) if formal else 0,
        "new_formal_scientific_cells": sum(len(report["inputs"]) for report in scoped_reports.values()),
    }
    if scoped_reports:
        view["scoped_reported_resources"] = scoped_reports
    if member_disclosure:
        view["verified_magnet_oxide_disclosure"] = magnet_disclosures
    return json.loads(canonical_json(view))


def _disclosure_cell(evidence, anchor, reason):
    statement = evidence["statement"]
    trace = anchor["temporal_decision"]
    return {
        "value": evidence["value"], "value_state": "PRESENT", "reason": reason,
        "evidence_state": "DESCRIPTIVE_ONLY", "value_origin": "EVIDENCE_REFERENCE",
        "release_id": evidence["release_id"], "source_key": anchor["source_key"],
        "source_sha256": anchor["source_sha256"], "source_url": anchor["source_url"],
        "scope_id": statement["scope_id"], "scope": statement["scope"], "statement_id": statement["statement_id"],
        "effective_date": statement["effective_date"], "category": statement["category"],
        "physical_page": anchor["physical_page"], "source_table": anchor["table"], "source_row": anchor["row"],
        "source_column": anchor["column"], "anchor_id": anchor["anchor_id"], "locator": anchor["locator"],
        "temporal_policy_version": trace["policy_version"], "temporal_status": trace["status"],
        "temporal_decision_id": trace["decision_id"],
        "temporal_evidence_ids": canonical_json(trace["matching_evidence_ids"]),
        "input_observation_ids": canonical_json([item["observation_id"] for item in evidence["inputs"]]),
        "metadata_evidence": canonical_json(evidence),
    }


def resource_presence_cells(science_views: dict) -> dict:
    """Project affirmative resource evidence with all three selected inputs."""
    return {(project, RESOURCE_PRESENCE_COLUMN): _disclosure_cell(
        evidence, evidence["inputs"][1], "VERIFIED_TREO_RESOURCE_STATEMENT",
    ) for project, evidence in science_views["verified_resource_presence"].items()}


def magnet_disclosure_cells(science_views: dict) -> dict:
    """Project native publication presence under the opt-in Census display policy."""
    return {(project, MAGNET_DISCLOSURE_COLUMN): _disclosure_cell(
        evidence, evidence["inputs"][0], MAGNET_DISCLOSURE_REASON,
    ) for project, evidence in science_views.get("verified_magnet_oxide_disclosure", {}).items()}


def validate_resource_presence(cell, definition, census_receipt, observations, ledger):
    """Independently check a display flag against the release's three formal cells."""
    project = cell["project_id"]
    evidence = json.loads(cell["metadata_evidence"])
    expected = census_receipt.get("science_views", {}).get("verified_resource_presence", {}).get(project)
    if (definition["projection_method"] != "governed_resource_presence" or evidence != expected
            or cell["evidence_state"] != "DESCRIPTIVE_ONLY" or cell["value"] != "是"
            or cell["reason"] != "VERIFIED_TREO_RESOURCE_STATEMENT"
            or census_receipt["release_mode"] != "FORMAL"
            or cell["release_id"] != census_receipt["release_id"] or evidence["release_id"] != cell["release_id"]):
        raise ValueError("Resource disclosure flag is outside its governed Census projection")
    lineage = evidence["inputs"]
    identifiers = json.loads(cell["input_observation_ids"])
    if (len(lineage) != len(RESOURCE_VARIABLES) or len(set(identifiers)) != len(RESOURCE_VARIABLES)
            or identifiers != [item["observation_id"] for item in lineage]):
        raise ValueError("Resource disclosure flag requires three distinct selected observations")
    shared = ("source_sha256", "scope_id", "statement_id", "effective_date")
    cutoff_contexts = set()
    for variable, item in zip(RESOURCE_VARIABLES, lineage, strict=True):
        direct = ledger[project, COLUMNS[variable]]
        raw = observations.get(item["observation_id"])
        if (raw is None or raw["project_id"] != project or raw["variable"] != variable
                or item["project_id"] != project or item["release_id"] != cell["release_id"]
                or any(raw[key] != cell[key] or item[key] != cell[key] for key in shared)
                or direct["value_origin"] != "FORMAL_SELECTION" or direct["temporal_status"] != "ELIGIBLE"
                or any(item[key] != direct[key] for key in ("observation_id", "anchor_id", "selection_id", "validation_id"))
                or item["temporal_decision"]["decision_id"] != direct["temporal_decision_id"]):
            raise ValueError("Resource disclosure flag does not match eligible same-statement observations")
        cutoff_contexts.add(tuple(raw[key] for key in ("category", "cutoff_grade", "cutoff_unit", "cutoff_analyte")))
    if (len(cutoff_contexts) != 1 or cell["anchor_id"] != lineage[1]["anchor_id"]
            or cell["temporal_decision_id"] != lineage[1]["temporal_decision"]["decision_id"]):
        raise ValueError("Resource disclosure flag lacks a shared resource context")


def validate_magnet_disclosure(cell, definition, census_receipt, observations):
    """Reconcile descriptive publication lineage against the governed raw records."""
    project = cell["project_id"]
    science = census_receipt.get("science_views", {})
    evidence = science.get("verified_magnet_oxide_disclosure", {}).get(project)
    if (not evidence or definition["projection_method"] != "approved_member_presence"
            or definition["mcda_use"] or evidence["display_only"] is not True
            or evidence["evidence_type"] != MAGNET_DISCLOSURE_ROLE or evidence["value"] != "是"
            or census_receipt["release_mode"] != "FORMAL" or evidence["release_id"] != census_receipt["release_id"]):
        raise ValueError("Magnet oxide disclosure is outside its descriptive use")
    inputs = evidence["inputs"]
    ids = [item["observation_id"] for item in inputs]
    if len(ids) != len(MAGNET) or len(set(ids)) != len(MAGNET):
        raise ValueError("Magnet oxide disclosure requires four distinct selected observations")
    expected = _disclosure_cell(evidence, inputs[0], MAGNET_DISCLOSURE_REASON)
    if any(cell[key] != str(value) for key, value in expected.items()):
        raise ValueError("Magnet oxide disclosure differs from its projected evidence")
    statement = evidence["statement"]
    basis = science.get("verified_resource_presence", {}).get(project, {}).get("statement")
    if statement != basis or statement["material_basis"] != "WHOLE_ORE" or statement["reporting_standard"] != "JORC 2012":
        raise ValueError("Magnet oxide disclosure lacks the governed resource statement")
    governed = {item["observation_id"]: item for record in science["resource_statements"]
                if record["project_id"] == project for item in record["observations"]}
    context = ("source_sha256", "scope_id", "statement_id", "effective_date", "category",
               "cutoff_grade", "cutoff_unit", "cutoff_analyte", "material_basis")
    for species, item in zip(MAGNET, inputs, strict=True):
        raw = observations.get(item["observation_id"])
        trace = item["temporal_decision"]
        accepted_proofs = ("anchor_id", "selection_id", "validation_id", "candidate_id")
        if (raw is None or raw["project_id"] != project or raw["variable"] != OXIDE_VARIABLES[species]
                or raw["chemical_basis"] != species or item["chemical_basis"] != species
                or item != governed.get(item["observation_id"]) or item["project_id"] != project
                or item["release_id"] != cell["release_id"] or item["denominator_basis"] != "WHOLE_ORE"
                or any(raw[key] != statement[key] for key in context)
                or not all(len(item[key]) == 64 for key in accepted_proofs)
                or (raw["page"], raw["table"], raw["row"], raw["column"], raw["raw_value"], raw["raw_unit"]) != (
                    item["physical_page"], item["table"], item["row"], item["column"], item["raw_token"], item["raw_unit"],
                ) or (raw["value"], raw["unit"]) != (item["normalized_value"], item["normalized_unit"])
                or trace["status"] != "ELIGIBLE" or trace["source_sha256"] != raw["source_sha256"]
                or trace["effective_date"] != raw["effective_date"] or trace["cutoff"] != census_receipt["research_cutoff"]
                or not trace["matching_evidence_ids"]):
            raise ValueError("Magnet oxide disclosure contains an ungoverned constituent or mismatched resource context")
