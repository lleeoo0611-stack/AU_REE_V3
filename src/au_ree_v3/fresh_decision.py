"""Build fresh decision inputs from governed, release-bound observations."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from hashlib import sha256
from importlib.resources import files
import json
from math import fsum, log1p
from pathlib import Path

from .comparability import SEMANTIC_POLICY_VERSION, evaluate_comparability
from .contracts import ContractViolation, ResearchRelease, canonical_json
from .decision import LEAVE_OUT_VERSION, POINT_METHOD_VERSION, leave_one_out, rank_panel
from .registry import load_profile, load_registry
from .research_inputs import checked_research_inputs
from .store import Store

POLICY_FILENAME = "fresh_decision_policy_v1.json"
POLICY_SHA256 = "b8dd516b6f6a765f3a656630d91315061ff0a27bf2c57bef41c92a2dc80abf19"


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


def _utility(value: Decimal, criterion: dict) -> float:
    low, high = criterion["low"], criterion["high"]
    clipped = float(max(Decimal(low), min(Decimal(high), value)))
    if criterion["function"] == "log1p":
        score = (log1p(clipped) - log1p(low)) / (log1p(high) - log1p(low))
    else:
        score = (clipped - low) / (high - low)
    return 1.0 - score if criterion["direction"] == "MIN" else score


def _criterion_rows(project_ids, inputs, policy, blocked_reason):
    comparisons = evaluate_comparability(
        project_ids, inputs, policy=policy, blocked_reason=blocked_reason,
    )
    return {
        project_id: {
            code: {
                "state": "COMPARABLE" if item.comparable else "UNKNOWN",
                "reason": item.reason,
                "accepted_value": item.value,
                "source_token": item.source_token,
                "unit": item.unit,
                "utility": _utility(Decimal(item.value), policy["criteria"][code]) if item.comparable else None,
                "scientific_context": item.context,
                "lineage": [entry.lineage() for entry in item.inputs],
                "derivation": item.derivation,
            }
            for code, item in criteria.items()
        }
        for project_id, criteria in comparisons.items()
    }


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
        "semantic_policy_version": SEMANTIC_POLICY_VERSION,
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
