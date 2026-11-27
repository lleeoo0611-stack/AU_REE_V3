"""Build fresh decision inputs from governed, release-bound observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from decimal import Decimal, InvalidOperation, localcontext
from hashlib import sha256
from importlib.resources import files
import json
from math import fsum, log1p
from pathlib import Path

from .contracts import ContractViolation, ResearchRelease, canonical_json
from .decision import LEAVE_OUT_VERSION, POINT_METHOD_VERSION, leave_one_out, rank_panel
from .registry import load_profile, load_registry
from .research_inputs import ResearchInput, checked_research_inputs
from .store import Store

POLICY_FILENAME = "fresh_decision_policy_v1.json"
POLICY_SHA256 = "b8dd516b6f6a765f3a656630d91315061ff0a27bf2c57bef41c92a2dc80abf19"
_RESOURCE_VARIABLES = ("ore_mt", "grade", "contained_treo_kt")
_SUPPORTED = {"R1", "S1"}


def load_policy() -> dict:
    """Load the reviewed policy projection without loading a V2 result or checkout."""
    data = files(__package__).joinpath(POLICY_FILENAME).read_bytes()
    if sha256(data).hexdigest() != POLICY_SHA256:
        raise ValueError("Fresh decision policy SHA-256 differs from the reviewed version")
    return json.loads(data)


def _release_inputs(store, release_id, cutoff, project_ids):
    if store is None or release_id is None:
        if store is not None or release_id is not None:
            raise ValueError("A decision request requires both a Store and its release ID")
        return (), "FORMAL_RELEASE_NOT_SUPPLIED"
    release = store.get(ResearchRelease, release_id)
    if release is None:
        raise ContractViolation("Fresh decision release is absent from the supplied Store")
    if release.cutoff != cutoff or not set(release.cohort).issubset(project_ids):
        raise ContractViolation("Decision release cutoff or cohort disagrees with the fresh profile")
    if release.release_mode != "FORMAL" or release.validation_status != "PASS":
        return (), "FORMAL_RELEASE_UNAVAILABLE"
    if not store.accepted_for_release(release_id):
        return (), "NO_GOVERNED_ACCEPTED_SELECTIONS"
    return checked_research_inputs(store, release, purpose="MCDA"), None


def _statement_identity(item: ResearchInput) -> tuple:
    return (item.scope.scope_id, item.raw.statement_id, item.raw.effective_date,
            item.source.source_key, item.source.sha256)


def _original_precision(item: ResearchInput) -> Decimal:
    if not item.raw.raw_value or item.candidate.value is None:
        raise ValueError("Reported quantity lacks its source token")
    raw = Decimal(item.raw.raw_value.replace(",", ""))
    if not raw.is_finite() or raw != Decimal(item.candidate.value):
        raise ValueError("Resource quantity differs from its reported token")
    return Decimal((0, (5,), raw.as_tuple().exponent - 1))


def _jorc_context(item: ResearchInput) -> tuple | None:
    payload = json.loads(item.raw.original_payload)
    reporting = str(payload.get("reporting_standard") or "").upper().replace("_", " ")
    category = str(item.raw.category or "").upper().replace("_", " ")
    if reporting != "JORC 2012" or item.source.kind != "pdf":
        return None
    if "RESERVE" in category or "TARGET" in category:
        return None
    resource = "MINERAL RESOURCE" in category or any(
        name in category.split() for name in ("MEASURED", "INDICATED", "INFERRED")
    )
    if not resource or any(payload.get(key) is None for key in ("cutoff_grade", "cutoff_unit", "cutoff_analyte")):
        return None
    return (category, payload["cutoff_grade"], payload["cutoff_unit"], payload["cutoff_analyte"])


def _resource_input(by_variable):
    if not all(by_variable.get(name) for name in _RESOURCE_VARIABLES):
        return None, "INCOMPLETE_SELECTED_RESOURCE_BUNDLE"
    if any(len(by_variable[name]) != 1 for name in _RESOURCE_VARIABLES):
        return None, "AMBIGUOUS_SELECTED_RESOURCE_BUNDLE"
    bundle = tuple(by_variable[name][0] for name in _RESOURCE_VARIABLES)
    ore, grade, contained = bundle
    if len({_statement_identity(item) for item in bundle}) != 1:
        return None, "RESOURCE_STATEMENT_SCOPE_OR_SOURCE_MISMATCH"
    contexts = [_jorc_context(item) for item in bundle]
    if not ore.raw.statement_id or not ore.raw.effective_date or any(context is None for context in contexts):
        return None, "ISSUER_JORC_RESOURCE_AND_CUTOFF_REQUIRED"
    if any(context != contexts[0] for context in contexts[1:]):
        return None, "RESOURCE_CATEGORY_OR_CUTOFF_MISMATCH"
    if any(
        (item.candidate.material_basis, item.candidate.chemical_basis) != ("WHOLE_ORE", "TREO")
        for item in bundle
    ):
        return None, "WHOLE_ORE_TREO_REQUIRED"
    if (ore.candidate.unit != "Mt" or grade.candidate.unit not in {"%", "ppm"}
            or contained.candidate.unit not in {"kt", "kt TREO"}):
        return None, "RESOURCE_QUANTITY_UNIT_MISMATCH"
    try:
        ore_value, grade_value, inventory = (Decimal(item.candidate.value) for item in bundle)
        ore_half, grade_half = (_original_precision(item) for item in (ore, grade))
    except (InvalidOperation, TypeError, ValueError):
        return None, "RESOURCE_REPORTED_PRECISION_UNAVAILABLE"
    percent = grade.candidate.unit == "%"
    if not (ore_value > 0 and 0 < grade_value <= (100 if percent else 1_000_000) and inventory > 0):
        return None, "INVALID_RESOURCE_QUANTITIES"
    factor = Decimal("10" if percent else "0.001")
    with localcontext() as context:
        context.prec = sum(len(value.as_tuple().digits) for value in (
            ore_value, grade_value, inventory, ore_half, grade_half,
        )) + 12
        low = (ore_value - ore_half) * (grade_value - grade_half) * factor
        high = (ore_value + ore_half) * (grade_value + grade_half) * factor
    if not low <= inventory <= high:
        return None, "REPORTED_CONTAINED_OUTSIDE_ROUNDING_INTERVAL"
    return bundle, None


def _composition_input(by_variable, resource, rule):
    if resource is None:
        return None, "SAME_STATEMENT_RESOURCE_REQUIRED"
    options = by_variable.get(rule["variables"][0], ())
    if len(options) != 1:
        reason = "AMBIGUOUS_COMPOSITION_SELECTION" if options else "NO_COMPOSITION_SELECTION"
        return None, reason
    composition, = options
    if _statement_identity(composition) != _statement_identity(resource[0]):
        return None, "COMPOSITION_SCOPE_STATEMENT_OR_EDITION_MISMATCH"
    if (composition.candidate.chemical_basis not in rule["basis"]
            or composition.candidate.unit != rule["unit"]
            or not 0 <= Decimal(composition.candidate.value) <= 100):
        return None, "COMPOSITION_BASIS_UNIT_OR_RANGE_MISMATCH"
    return composition, None


def _utility(value: Decimal, criterion: dict) -> float:
    low, high = criterion["low"], criterion["high"]
    clipped = float(max(Decimal(low), min(Decimal(high), value)))
    if criterion["function"] == "log1p":
        score = (log1p(clipped) - log1p(low)) / (log1p(high) - log1p(low))
    else:
        score = (clipped - low) / (high - low)
    return 1.0 - score if criterion["direction"] == "MIN" else score


def _input_context(item: ResearchInput) -> dict:
    payload = json.loads(item.raw.original_payload)
    return {
        "statement_id": item.raw.statement_id,
        "scope_id": item.raw.scope_id,
        "effective_date": item.raw.effective_date,
        "material_basis": item.candidate.material_basis,
        "chemical_basis": item.candidate.chemical_basis,
        "cutoff_grade": payload.get("cutoff_grade"),
        "cutoff_unit": payload.get("cutoff_unit"),
        "cutoff_analyte": payload.get("cutoff_analyte"),
    }


def _criterion_rows(project_ids, inputs, policy, blocked_reason):
    by_project = defaultdict(lambda: defaultdict(list))
    for item in inputs:
        by_project[item.raw.project_id][item.raw.variable].append(item)
    rows = {}
    for project_id in project_ids:
        by_variable = by_project[project_id]
        resource, resource_gap = _resource_input(by_variable)
        composition, composition_gap = _composition_input(by_variable, resource, policy["s1_admission"])
        values = {
            "R1": (resource[-1], resource, None) if resource else (None, (), resource_gap),
            "S1": (composition, (*resource, composition), None) if composition else (None, (), composition_gap),
        }
        cells = {}
        for code, criterion in policy["criteria"].items():
            item, evidence, reason = values.get(code, (None, (), "CRITERION_ADMISSION_ADAPTER_NOT_MIGRATED"))
            cells[code] = {
                "state": "COMPARABLE" if item else "UNKNOWN",
                "reason": ("RELEASE_BOUND_COMPARABLE_SELECTION" if item
                           else blocked_reason if blocked_reason and code in _SUPPORTED else reason),
                "accepted_value": item.candidate.value if item else None,
                "source_token": item.raw.raw_value if item else None,
                "unit": item.candidate.unit if item else None,
                "utility": _utility(Decimal(item.candidate.value), criterion) if item else None,
                "scientific_context": _input_context(item) if item else None,
                "lineage": [entry.lineage() for entry in evidence],
            }
        rows[project_id] = cells
    return rows


def _scenario_weights(codes, policy):
    domains = Counter(policy["criteria"][code]["domain"] for code in codes)
    weights = {}
    for scenario, budgets in sorted(policy["scenarios"].items()):
        total = fsum(budgets[domain] for domain in sorted(domains))
        weights[scenario] = {}
        for code in codes:
            domain = policy["criteria"][code]["domain"]
            weights[scenario][code] = budgets[domain] / total / domains[domain]
    return weights


def _score_panel(matrix, weights, codes, policy):
    domains = {code: policy["criteria"][code]["domain"] for code in codes}
    threshold = policy["promethee_threshold"]
    ranked, sensitivity = {}, {}
    for scenario, scenario_weights in weights.items():
        ranked[scenario] = {
            method: {pid: asdict(result) for pid, result in points.items()}
            for method, points in rank_panel(matrix, scenario_weights, threshold).items()
        }
        sensitivity[scenario] = [
            {"type": kind, "removed": removed, "projects": {
                pid: asdict(change) for pid, change in projects.items()
            }}
            for (kind, removed), projects in leave_one_out(matrix, scenario_weights, domains, threshold).items()
        ]
    return ranked, sensitivity


def _panels(rows, policy):
    inputs, results = {}, {}
    for name, codes in policy["panels"].items():
        matrix, exclusions = {}, {}
        for project_id, criteria in rows.items():
            missing = {code: criteria[code]["reason"] for code in codes
                       if criteria[code]["state"] != "COMPARABLE"}
            if missing:
                exclusions[project_id] = missing
            else:
                matrix[project_id] = {code: criteria[code]["utility"] for code in codes}
        weights = _scenario_weights(codes, policy)
        eligible = len(matrix) >= policy["minimum_cohort"]
        inputs[name] = {
            "criterion_codes": codes,
            "eligible_projects": sorted(matrix),
            "benefit_matrix": matrix,
            "scenario_weights": weights,
            "project_exclusions": exclusions,
        }
        result = {
            "status": "PASS" if eligible else "INSUFFICIENT_COHORT",
            "eligible_count": len(matrix), "minimum_cohort": policy["minimum_cohort"],
            "point_rankings": {}, "leave_one_out": {},
        }
        if eligible:
            result["point_rankings"], result["leave_one_out"] = _score_panel(matrix, weights, codes, policy)
        results[name] = result
    return inputs, results


def _write_json(path: Path, payload: dict) -> str:
    data = (canonical_json(payload) + "\n").encode("utf-8")
    path.write_bytes(data)
    return sha256(data).hexdigest()


def build_decision(
    profile: Path, out: Path, *, store: Store | None = None, release_id: str | None = None,
) -> dict:
    """Build complete-case matrices and rank only governed, eligible panels."""
    research_profile = load_profile(profile)
    registry = load_registry(research_profile.registry_path)
    if len(registry.projects) != research_profile.project_count:
        raise ValueError("Decision profile Project count differs from the pinned registry")
    project_ids = sorted(project.project_id for project in registry.projects)
    cutoff = research_profile.research_cutoff.isoformat()
    policy = load_policy()
    inputs, blocked = _release_inputs(store, release_id, cutoff, set(project_ids))
    rows = _criterion_rows(project_ids, inputs, policy, blocked)
    panels, results = _panels(rows, policy)

    out.mkdir(parents=True, exist_ok=True)
    shared = {
        "profile_id": research_profile.profile_id,
        "profile_sha256": research_profile.sha256,
        "registry_sha256": registry.sha256,
        "research_cutoff": cutoff,
        "research_timezone": str(research_profile.timezone),
        "release_id": release_id,
        "policy_sha256": POLICY_SHA256,
        "policy_version": policy["policy_version"],
    }
    input_sha = _write_json(out / "mcda_inputs.json", {
        **shared, "schema": "fresh-decision-inputs-v1",
        "policy": policy, "criteria_by_project": rows, "panels": panels,
    })
    results_sha = _write_json(out / "mcda_results.json", {
        **shared, "schema": "fresh-decision-results-v1",
        "mcda_inputs_sha256": input_sha, "point_method_version": POINT_METHOD_VERSION,
        "deterministic_sensitivity_version": LEAVE_OUT_VERSION, "panels": results,
    })
    scored = [name for name, result in results.items() if result["status"] == "PASS"]
    receipt = {
        **shared, "schema": "fresh-decision-receipt-v1",
        "status": "PASS" if scored else "BLOCKED",
        "reason": None if scored else blocked or "NO_PANEL_HAS_SUFFICIENT_COMPARABLE_PROJECTS",
        "accepted_selections": len(inputs),
        "project_count": len(project_ids),
        "comparable_project_criteria": sum(
            cell["state"] == "COMPARABLE" for project in rows.values() for cell in project.values()
        ),
        "panels": {
            name: {key: result[key] for key in ("status", "eligible_count", "minimum_cohort")}
            for name, result in results.items()
        },
        "mcda_inputs_sha256": input_sha,
        "mcda_results_sha256": results_sha,
    }
    _write_json(out / "decision_receipt.json", receipt)
    return receipt
