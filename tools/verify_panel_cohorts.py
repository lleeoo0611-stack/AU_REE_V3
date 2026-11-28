"""Independently audit matrix utilities, rankings, sensitivity, and all Census delta keys."""

import argparse
from collections import Counter
from contextlib import closing
import csv
from decimal import Decimal, localcontext
from hashlib import sha256
import json
from pathlib import Path


TOLERANCE = Decimal("5e-14")
_PANEL = "issuer_resource_composition"


def _decimal(value):
    return Decimal(str(value))


def _ranks(values):
    return {project: Decimal(1 + sum(item > score for item in values.values()))
            + Decimal(sum(item == score for item in values.values()) - 1) / 2
            for project, score in values.items()}


def _normalized(weights):
    total = sum(weights.values())
    return {criterion: weight / total for criterion, weight in weights.items()}


def _flows(matrix, weights, threshold):
    def preference(first, second):
        return sum(weight * min(1, max(0, (first[code] - second[code]) / threshold))
                   for code, weight in weights.items())

    return {key: sum(preference(row, other) - preference(other, row)
                     for project, other in matrix.items() if key != project) / (len(matrix) - 1)
            for key, row in matrix.items()}


def decimal_scenario(matrix, weights, domains, threshold):
    """Compute scores independently in Decimal arithmetic."""
    with localcontext() as context:
        context.prec = 50
        benefit = {project: {code: _decimal(value) for code, value in row.items()} for project, row in matrix.items()}
        supplied = {code: _decimal(value) for code, value in weights.items()}
        normalized = _normalized(supplied)
        threshold = _decimal(threshold)
        flows = _flows(benefit, normalized, threshold)
        saw, topsis = {}, {}
        for project, values in benefit.items():
            point = [weight * values[code] for code, weight in normalized.items()]
            minus = sum(value ** 2 for value in point).sqrt()
            plus = sum((weight - value) ** 2 for weight, value in zip(normalized.values(), point, strict=True)).sqrt()
            topsis[project] = minus / (minus + plus)
            saw[project] = sum(point)
        results = {}
        for method, values in (("saw", saw), ("topsis", topsis), ("promethee", flows)):
            ranks = _ranks(values)
            results[method] = {project: {"score": value, "rank": ranks[project]} for project, value in values.items()}
        sensitivity = {}
        removed = {("criterion", code): {code} for code in weights}
        for domain in set(domains.values()):
            removed["domain", domain] = {code for code in weights if domains[code] == domain}
        original = _ranks(flows)
        for key, excluded in removed.items():
            remaining = {code: weight for code, weight in supplied.items() if code not in excluded}
            if not sum(remaining.values()):
                continue
            reduced = _ranks(_flows(benefit, _normalized(remaining), threshold))
            sensitivity[key] = {project: {"rank": rank, "rank_change": rank - original[project]}
                                for project, rank in reduced.items()}
        return results, sensitivity


def _utility(value, rule):
    point = min(_decimal(rule["high"]), max(_decimal(rule["low"]), _decimal(value)))
    low, high = _decimal(rule["low"]), _decimal(rule["high"])
    with localcontext() as context:
        context.prec = 50
        if rule["function"] == "log1p":
            benefit = ((1 + point).ln() - (1 + low).ln()) / ((1 + high).ln() - (1 + low).ln())
        elif rule["function"] == "linear":
            benefit = (point - low) / (high - low)
        else:
            raise ValueError(f"Unreviewed utility function {rule['function']}")
        return 1 - benefit if rule["direction"] == "MIN" else benefit


def _match(actual, expected, path=()):
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or actual.keys() != expected.keys():
            raise ValueError(f"Independent result keys differ at {path}")
        for key, value in expected.items():
            _match(actual[key], value, (*path, key))
    elif isinstance(expected, Decimal):
        if type(actual) not in (float, int):
            raise ValueError(f"Independent numerical input is not a finite number at {path}")
        result = _decimal(actual)
        if not result.is_finite() or abs(expected - result) > TOLERANCE:
            raise ValueError(f"Independent numerical result differs at {path}: {result} != {expected}")
    elif actual != expected:
        raise ValueError(f"Independent result differs at {path}")


def _weights(codes, policy):
    domains = Counter(policy["criteria"][code]["domain"] for code in codes)
    result = {}
    for scenario, budgets in policy["scenarios"].items():
        total = sum(_decimal(budgets[domain]) for domain in domains)
        result[scenario] = {code: (_decimal(budgets[domain]) / total) / domains[domain]
                            for code in codes for domain in (policy["criteria"][code]["domain"],)}
    return result


def _panel_scores(matrix, weights, codes, policy, rankings, sensitivity):
    expected_rankings, expected_sensitivity = {}, {}
    if len(matrix) >= policy["minimum_cohort"]:
        domains = {code: policy["criteria"][code]["domain"] for code in codes}
        for scenario, budgets in weights.items():
            expected_rankings[scenario], expected_sensitivity[scenario] = decimal_scenario(
                matrix, budgets, domains, policy["promethee_threshold"],
            )
    scores = {}
    for scenario, rows in sensitivity.items():
        index = {(row["type"], row["removed"]): row["projects"] for row in rows}
        if len(index) != len(rows):
            raise ValueError("Duplicate leave-one-out scenario evidence")
        scores[scenario] = index
    _match(rankings, expected_rankings)
    _match(scores, expected_sensitivity)
    return {
        "score_and_rank_points": sum(len(points) for methods in rankings.values() for points in methods.values()),
        "sensitivity_points": sum(len(projects) for scenario in scores.values() for projects in scenario.values()),
    }


def _panel(inputs, result, name, policy):
    panel = inputs["panels"][name]
    codes = policy["panels"][name]
    criteria = inputs["criteria_by_project"]
    eligible = {project: {code: _utility(row[code]["accepted_value"], policy["criteria"][code]) for code in codes}
                for project, row in criteria.items() if all(row[code]["state"] == "COMPARABLE" for code in codes)}
    comparable = {project: {code: row[code]["utility"] for code in codes} for project, row in criteria.items()
                  if project in eligible}
    _match(comparable, eligible)
    _match(panel["benefit_matrix"], eligible)
    _match(panel["scenario_weights"], _weights(codes, policy))
    status = "PASS" if len(eligible) >= policy["minimum_cohort"] else "INSUFFICIENT_COHORT"
    if panel["eligible_projects"] != sorted(eligible) or result["status"] != status or result["eligible_count"] != len(eligible):
        raise ValueError(f"Independent complete-case panel differs: {name}")
    return _panel_scores(panel["benefit_matrix"], panel["scenario_weights"], codes, policy,
                         result["point_rankings"], result["leave_one_out"])


def verify_panels(old, new, old_results, new_results, panels, policy):
    audit = {}
    if panels.keys() != policy["panels"].keys():
        raise ValueError("Comparison does not cover the exact panel registry")
    for name, codes in policy["panels"].items():
        before, after = (build["panels"][name] for build in (old, new))
        prior, current = (result["panels"][name] for result in (old_results, new_results))
        baseline = _panel(old, prior, name, policy)
        expanded = _panel(new, current, name, policy)
        fixed = panels[name]["fixed_cohort"]
        old_ids, current_ids = set(before["benefit_matrix"]), set(after["benefit_matrix"])
        missing = sorted(old_ids - current_ids)
        exact_restriction = {project: after["benefit_matrix"][project] for project in sorted(old_ids & current_ids)}
        if (fixed["baseline_projects"] != sorted(old_ids) or fixed["missing_current_projects"] != missing
                or fixed["before_benefit_matrix"] != before["benefit_matrix"] or fixed["current_benefit_matrix"] != exact_restriction
                or panels[name]["after_benefit_matrix"] != after["benefit_matrix"]
                or panels[name]["scenario_weights"] != after["scenario_weights"]
                or panels[name]["after_rankings"] != current["point_rankings"]
                or panels[name]["after_sensitivity"] != current["leave_one_out"]):
            raise ValueError("Mixed fixed and expanded panel evidence")
        scored = None
        if prior["status"] != "PASS" or missing:
            expected = "INSUFFICIENT_COHORT" if prior["status"] != "PASS" else "BLOCKED"
            if fixed["status"] != expected or "current_point_rankings" in fixed:
                raise ValueError("Unavailable fixed cohort carries rankings")
        else:
            if (fixed["status"] != "PASS" or fixed["before_point_rankings"] != prior["point_rankings"]
                    or fixed["before_sensitivity"] != prior["leave_one_out"]):
                raise ValueError("The fixed cohort lost its archived rankings")
            scored = _panel_scores(exact_restriction, after["scenario_weights"], codes, policy,
                                   fixed["current_point_rankings"], fixed["current_sensitivity"])
            _verify_effects(fixed, current)
        audit[name] = {"baseline": baseline, "current_fixed": scored, "current_full": expanded,
                       "fixed_status": fixed["status"], "before_projects": sorted(old_ids),
                       "after_projects": sorted(current_ids)}
    return audit


def _point_axes(rankings):
    return {(scenario, method, project): result
            for scenario, methods in rankings.items() for method, projects in methods.items()
            for project, result in projects.items()}


def _sensitivity_axes(sensitivity):
    return {(scenario, leave["type"], leave["removed"], project): result
            for scenario, variants in sensitivity.items() for leave in variants
            for project, result in leave["projects"].items()}


def _verify_effects(fixed, full):
    def expected(before, after):
        return {key: (old, after[key]) for key, old in before.items() if old != after[key]}

    def reported(rows, fields):
        result = {tuple(row[name] for name in fields): (row["before"], row["after"]) for row in rows}
        if len(rows) != len(result):
            raise ValueError("Duplicate panel impact key")
        return result

    baseline_utility = {(p, code): value for p, values in fixed["before_benefit_matrix"].items() for code, value in values.items()}
    current_utility = {(p, code): value for p, values in fixed["current_benefit_matrix"].items() for code, value in values.items()}
    changed = fixed["same_cohort_input_effect"]
    if expected(baseline_utility, current_utility) != reported(changed["utility_changes"], ("project_id", "criterion_id")):
        raise ValueError("Same-cohort utility change differs")
    for index, names, key, archived, restricted, expanded in (
        (_point_axes, ("scenario", "method", "project_id"), "ranking_changes",
         fixed["before_point_rankings"], fixed["current_point_rankings"], full["point_rankings"]),
        (_sensitivity_axes, ("scenario", "type", "removed", "project_id"), "sensitivity_changes",
         fixed["before_sensitivity"], fixed["current_sensitivity"], full["leave_one_out"]),
    ):
        if (expected(index(archived), index(restricted)) != reported(changed[key], names)
                or expected(index(restricted), index(expanded)) != reported(fixed["cohort_membership_effect"][key], names)):
            raise ValueError(f"Fixed or expanded cohort effect differs: {key}")


def _criterion_keys(inputs, policy):
    compared = set()
    count = 0
    for project, criteria in inputs["criteria_by_project"].items():
        if criteria.keys() != policy["criteria"].keys():
            raise ValueError("Criterion verification lost a Project axis")
        for code, row in criteria.items():
            count += 1
            if row["state"] == "COMPARABLE":
                _match(row["utility"], _utility(row["accepted_value"], policy["criteria"][code]), (project, code))
                compared.add((project, code))
            elif row["accepted_value"] is not None or row["utility"] is not None:
                raise ValueError("Unavailable criterion was assigned a value")
    return compared, count


def _census_audit(before, after, delta):
    from verify_display_research_equivalence import read_census

    columns, projects, left = read_census(before)
    new_columns, new_projects, right = read_census(after)
    if (columns, projects) != (new_columns, new_projects) or left.keys() != right.keys():
        raise ValueError("Core Census grid or row order differs")
    categories, seen = Counter(), set()
    axes = {"value_or_unit": {"value", "unit", "raw_value", "raw_unit"},
            "status": {"value_state", "value_origin", "evidence_state"},
            "native_evidence": {"source_sha256", "anchor_id", "observation_id", "scope_id", "temporal_decision_id", "locator"},
            "release_lineage": {"release_id", "selection_id", "validation_id", "lineage_id"}}
    with (delta / "census_delta.csv").open(encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            key = row["project_id"], row["column"]
            if key in seen or key not in left:
                raise ValueError("Census delta contains a duplicate or unknown key")
            seen.add(key)
            initial, current = left[key], right[key]
            fields = {field for field in initial.keys() | current.keys() if initial.get(field) != current.get(field)}
            if (json.loads(row["before_cell"]) != initial or json.loads(row["after_cell"]) != current
                    or json.loads(row["changed_fields"]) != sorted(fields)):
                raise ValueError(f"Census delta omitted changed evidence or state at {key}")
            categories.update(tag for tag, paths in axes.items() if fields & paths)
            categories["any_cell_change"] += bool(fields)
    if seen != left.keys():
        raise ValueError("Census delta does not enumerate all original keys")
    return {"keys": len(seen), "changed_axes": categories,
            "before_evidence_states": Counter(row["evidence_state"] for row in left.values()),
            "after_evidence_states": Counter(row["evidence_state"] for row in right.values())}


def _yield_audit(before, after, builds, sources, cas):
    from verify_scientific_yield import (
        DERIVED_IDS, PROTECTED_CONTEXT, db, derived_records, governed, jsonl, read, require, sha_file,
        verify_added_raw, verify_formula, without,
    )

    observations, accepted, derived = [], [], []
    for root, build in zip((before, after), builds, strict=True):
        table = jsonl(root / "observations/observations.jsonl", "observation_id")
        with closing(db(root / "observations/research.sqlite")) as database:
            selected = governed(database, read(root / "observations/extraction.json")["rules_sha256"])
            derived.append(derived_records(database))
        check = build["observation_counts"]
        require((len(table), sum(row["outcome"] == "VALIDATED" for row in table.values()), len(selected))
                == (check["candidate_count"], check["validated_count"], check["selected_count"]),
                "Independent observation and accepted counts differ from the build")
        selected_ids = {record["raw"]["observation_id"] for record in selected.values()}
        require(selected_ids == {key for key, row in table.items() if row["selected_for_census"]},
                "SQLite acceptance lacks exact extraction identity")
        observations.append(table)
        accepted.append(selected)
    left, right = ({key for key, row in dataset.items() if row["outcome"] == "VALIDATED"} for dataset in observations)
    old, new = accepted
    added = set(new) - old.keys()
    revised = {key for key in old.keys() & new.keys()
               if without(old[key], PROTECTED_CONTEXT | {"rules_sha"}) != without(new[key], PROTECTED_CONTEXT | {"rules_sha"})}
    proof_status = "PASS" if not added and not revised else "NOT_RUN"
    manifest = None
    if sources is not None:
        require(sha_file(sources) == builds[1]["input_artifacts"]["sources"], "Candidate source proof uses another manifest")
        manifest = read(sources)
        proof_status = "PASS"
    selected_changes = []
    for key in sorted(added | revised):
        item = new[key]
        raw = item["raw"]
        proof = verify_added_raw(raw, item["anchor"], item["decision"], manifest, cas) if manifest is not None else None
        selected_changes.append({
            "selection_key": key, "change": "ADDED" if key in added else "REVISED",
            **{field: raw[field] for field in ("project_id", "observation_id", "scope_id", "statement_id",
                                             "variable", "raw_value", "raw_unit", "source_sha")},
            "selection_id": item["accepted"]["acceptance_id"], "exact_original": proof,
        })
    prior, current = derived
    derived_new = [verify_formula(value, new.values()) for key, value in current.items() if key not in prior]
    derived_revised = [list(key) for key in sorted(prior.keys() & current.keys())
                       if without(prior[key], PROTECTED_CONTEXT | DERIVED_IDS) != without(current[key], PROTECTED_CONTEXT | DERIVED_IDS)]
    return {
        "sqlite_integrity_and_foreign_keys": "PASS",
        "distinct_validated_original_observation_ids_added": sorted(right - left),
        "distinct_validated_original_observation_ids_withdrawn": sorted(left - right),
        "formal_selection_keys_before_after": [len(old), len(new)],
        "formal_selection_changes": selected_changes,
        "formal_selection_keys_withdrawn": sorted(old.keys() - new.keys()),
        "changed_selections_independent_original_readback": proof_status,
        "derived_records_before_after": [len(prior), len(current)], "new_formula_proofs": derived_new,
        "derived_keys_revised": derived_revised, "derived_keys_withdrawn": [list(key) for key in sorted(prior.keys() - current.keys())],
    }


def audit(before, after, delta, policy_path, *, sources=None, cas=None):
    from verify_display_research_equivalence import read, sha_file

    builds = [read(root / "build_receipt.json") for root in (before, after)]
    for root, receipt in zip((before, after), builds, strict=True):
        declared = receipt["scientific_artifacts"]
        if len(declared) != 8 or any(sha_file(root / filename) != digest for filename, digest in declared.items()):
            raise ValueError("Scientific artifacts differ from their input build receipt")
    policy = read(policy_path)
    hashes = [read(root / "decision/mcda_inputs.json") for root in (before, after)]
    old, new = hashes
    if any(inputs["policy_sha256"] != sha_file(policy_path) or inputs["policy"] != policy for inputs in hashes):
        raise ValueError("MCDA inputs disagree with the exact pinned decision policy")
    before_result, after_result = (read(root / "decision/mcda_results.json") for root in (before, after))
    comparison = read(delta / "comparison_receipt.json")
    if any(sha_file(delta / name) != digest for name, digest in comparison["outputs_sha256"].items()):
        raise ValueError("Comparison artifact fingerprints disagree")
    lineage = read(delta / "release_and_replay.json")
    baseline_id, candidate_id = (sha_file(root / "build_receipt.json") for root in (before, after))
    if [lineage[role]["build_receipt_sha256"] for role in ("before", "after")] != [baseline_id, candidate_id]:
        raise ValueError("Delta report belongs to different exact builds")
    cohort_report = read(delta / "cohort_and_mcda.json")["panels"]
    checked = verify_panels(old, new, before_result, after_result, cohort_report, policy)
    (previous, old_count), (current, new_count) = (_criterion_keys(inputs, policy) for inputs in hashes)
    added, removed = current - previous, previous - current
    complete = len(checked[_PANEL]["after_projects"])
    net = sum(code in ("R1", "S1") for _, code in added) - sum(code in ("R1", "S1") for _, code in removed)

    def hypothesis(passed):
        if baseline_id == candidate_id:
            return {"status": "NOT_RUN", "reason": "BASELINE_SELF_COMPARISON"}
        return {"status": "PASS", "reason": None} if passed else {"status": "FAIL", "reason": "HYPOTHESIS_NOT_SUPPORTED"}

    return {
        "schema": "fixed-and-expanded-panel-oracle-v1", "status": "PASS",
        "baseline_build_sha256": baseline_id, "candidate_build_sha256": candidate_id,
        "manifests": [receipt["input_artifacts"]["sources"] for receipt in builds],
        "scientific_yield": _yield_audit(before, after, builds, sources, cas),
        "policy_sha256": sha_file(policy_path), "absolute_score_tolerance": str(TOLERANCE),
        "census": _census_audit(before, after, delta), "panels": checked,
        "criterion_grid_keys_before_after": [old_count, new_count],
        "comparable_keys_before_after": [len(previous), len(current)],
        "added_comparable_keys": sorted(added), "withdrawn_comparable_keys": sorted(removed),
        "hypotheses": {
            "new_independent_r1_or_s1": {"net": net, "target": 1, **hypothesis(net >= 1)},
            "complete_r1_and_s1_panel": {"before": len(checked[_PANEL]["before_projects"]), "after": complete, "target": 3,
                                        **hypothesis(complete >= 3)},
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("baseline", "candidate", "delta", "policy", "out"):
        parser.add_argument(f"--{label}", type=Path, required=True)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--cas", type=Path)
    args = parser.parse_args()
    if bool(args.sources) != bool(args.cas):
        parser.error("--sources and --cas must be supplied together to reread changed selections")
    result = audit(args.baseline, args.candidate, args.delta, args.policy, sources=args.sources, cas=args.cas)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "out": str(args.out)}))
