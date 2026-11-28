"""Verify frozen observations, source-derived additions and MCDA deltas independently."""

import argparse
from collections import Counter
from contextlib import closing
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
from unicodedata import normalize

from verify_display_research_equivalence import (
    FIXED_SOURCE, archive_closure, field_evidence, read, read_census, require, sha_file,
    stable_hash, typed,
)

PROTECTED_CONTEXT = {"profile_sha256", "release_id", "selection_id", "acceptance_id", "validation_id"}
DERIVED_IDS = {"assessment_id", "derived_id", "derived_value_id"}


def without(value, keys):
    if isinstance(value, dict):
        return {key: without(item, keys) for key, item in value.items() if key not in keys}
    if isinstance(value, list):
        return [without(item, keys) for item in value]
    return value


def db(path):
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    require(connection.execute("PRAGMA user_version").fetchone()[0] == 3, "Prior scientific schema was revised")
    require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity check failed")
    require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign key check failed")
    return connection


def jsonl(path, key):
    rows = {}
    for line in path.read_text(encoding="utf8").splitlines():
        item = json.loads(line)
        identity = item[key]
        require(identity not in rows, f"Duplicate original key {identity} in {path}")
        rows[identity] = item
    return rows


def canon_source_word(text):
    return " ".join(normalize("NFKC", text).split())


def verify_added_raw(raw, anchor, decision, manifest, cas):
    from au_ree_v3.documents import DocumentCache

    capture = [row for row in manifest["editions"] if row["sha256"] == raw["source_sha"]
               and raw["project_id"] in row["project_ids"] and row["kind"] == "pdf"
               and row["temporal_decision"]["status"] == "ELIGIBLE"]
    require(capture and decision["status"] == "ELIGIBLE", "Added selection lacks its own eligible capture and temporal proof")
    capture_sha256 = {edition["receipt_sha256"] for edition in capture}
    for pin in capture_sha256:
        receipt = cas / "objects" / pin
        require(sha_file(receipt) == pin, "Capture proof bytes changed")
    digest = raw["source_sha"]
    original = cas / "objects" / digest
    require(sha_file(original) == digest == anchor["source_sha"], "Added observation source SHA mismatch")
    require(anchor["page"] and anchor["quote"], "Added observation lacks a physical page and quote")
    document = DocumentCache().pdf(original.read_bytes(), digest, last_page=anchor["page"])
    exact = canon_source_word(document.pages[anchor["page"] - 1])
    quote = canon_source_word(anchor["quote"])
    token = canon_source_word(raw["raw_value"])
    require(quote in exact and token in quote, f"Added observation text or value not located on its exact original page {digest}#{anchor['page']}")
    return {"page": anchor["page"], "source_sha256": digest, "quote": anchor["quote"], "locator": anchor["locator"],
            "capture_sha256": sorted(capture_sha256), "temporal_decision_id": decision["decision_id"]}


def governed(connection, rule_lock=None):
    statement = """SELECT a.acceptance_id, a.selection_key, a.payload, v.payload, o.payload, n.payload, d.payload, e.payload
                   FROM accepted_values AS a
                   JOIN validations AS v USING (validation_id)
                   JOIN candidates AS n USING (candidate_id)
                   JOIN raw_observations AS o USING (raw_record_id)
                   JOIN anchors AS e ON e.anchor_id = o.anchor_id
                   JOIN temporal_decisions AS d USING (validation_id)"""
    result = {}
    for identity, key, *columns in connection.execute(statement):
        accepted, validation, raw, normalized, temporal, anchor = map(json.loads, columns)
        record = {"accepted": accepted, "validation": validation, "raw": raw, "normalized": normalized,
                  "decision": temporal, "anchor": anchor}
        require(key not in result and accepted["acceptance_id"] == identity and accepted["validation_id"] == validation["validation_id"],
                "Repeated or invalid accepted selection key")
        if rule_lock is not None:
            require(validation["rules_sha"] == rule_lock, "Accepted value validation differs from its build rule pin")
        result[key] = record
    require(len(result) == connection.execute("SELECT COUNT(*) FROM accepted_values").fetchone()[0],
            "Accepted values have unresolved scientific provenance")
    return result


def compare_store(before, after, manifest, cas):
    before_path, after_path = (root / "observations/research.sqlite" for root in (before, after))
    with closing(db(before_path)) as old_db, closing(db(after_path)) as new_db:
        schema = "SELECT name, sql FROM sqlite_schema ORDER BY name"
        require(old_db.execute(schema).fetchall() == new_db.execute(schema).fetchall(), "Stage 5 drifted SQLite scientific schema")
        locks = [read(build / "observations/extraction.json")["rules_sha256"] for build in (before, after)]
        old, new = governed(old_db, locks[0]), governed(new_db, locks[1])
        require(old.keys() <= new.keys(), "Stage 5 removed an existing formal accepted key")
        for key, prior in old.items():
            compare_fields = PROTECTED_CONTEXT | {"rules_sha"}
            require(without(prior, compare_fields) == without(new[key], compare_fields),
                    f"Prior governed selection changed scientific content: {key}")
        added = {}
        for key in sorted(new.keys() - old.keys()):
            item = new[key]
            raw, validation = item["raw"], item["validation"]
            require(validation["outcome"] == "VALIDATED" and item["decision"]["status"] == "ELIGIBLE",
                    "Only validated and temporally eligible values can become new selections")
            proof = verify_added_raw(raw, item["anchor"], item["decision"], manifest, cas)
            added[key] = {"project_id": raw["project_id"], "observation_id": raw["observation_id"],
                          "statement_id": raw["statement_id"], "scope_id": raw["scope_id"], "variable": raw["variable"],
                          "raw_value": raw["raw_value"], "raw_unit": raw["raw_unit"], "material_basis": raw["material_basis"],
                          "chemical_basis": raw["chemical_basis"], "denominator_basis": raw["denominator_basis"],
                          "selection_reason": item["accepted"]["reason"], "selected_value": item["normalized"]["value"],
                          "selected_unit": item["normalized"]["unit"], "exact_original": proof,
                          "acceptance_id": item["accepted"]["acceptance_id"]}
        approved = {item["accepted"]["acceptance_id"]: item for item in new.values()}
        old_dates, new_dates = map(derived_records, (old_db, new_db))
        require(old_dates.keys() <= new_dates.keys(), "Stage 5 removed an approved old formula selection")
        for key, prior in old_dates.items():
            require(without(prior, PROTECTED_CONTEXT | DERIVED_IDS) == without(new_dates[key], PROTECTED_CONTEXT | DERIVED_IDS),
                    f"Existing derived science or formula changed for {key}")
        approved_derived = {}
        for key, value in new_dates.items():
            if key not in old_dates:
                proof = verify_formula(value, new.values())
                approved_derived[value["derived_id"]] = proof
        return added, approved, [len(old), len(new)], approved_derived, locks


def derived_records(connection):
    result = {}
    for identity, project, scope, payload in connection.execute("SELECT derived_id, project_id, scope_id, payload FROM derived_values"):
        record = json.loads(payload)
        links = [row[0] for row in connection.execute(
            "SELECT raw_record_id FROM derived_inputs WHERE derived_id=? ORDER BY position", (identity,),
        )]
        require(links == record["input_observation_ids"], "Stored derivation input links differ from payload")
        key = project, scope, record["variable"]
        require(key not in result, f"Repeated derived selection for {key}")
        result[key] = record
    return result


_MEASUREMENT_SCALES = {
    "Mt": {"t": -6, "kt": -3, "Mt": 0, "Bt": 3},
    "ppm": {"ppm": 0, "%": 4, "kg/t": 3},
    "%": {"ppm": -4, "%": 0, "kg/t": -1},
}


def _reported_measurement(entry, interval, target):
    raw, value = entry["raw"], entry["normalized"]
    token = Decimal(raw["raw_value"].replace(",", ""))
    significant = interval.get("reported_significant_figures")
    exponent = token.as_tuple().exponent if significant is None else token.adjusted() - significant + 1
    move = _MEASUREMENT_SCALES[target][raw["raw_unit"]]
    half_step = Decimal("0.5").scaleb(exponent)
    measured = token.scaleb(move)
    bounds = (max(token - half_step, Decimal(0)).scaleb(move), (token + half_step).scaleb(move))
    require((interval["raw_token"], interval["raw_unit"]) == (raw["raw_value"], raw["raw_unit"])
            and interval["normalized_unit"] == value["unit"] == target
            and Decimal(value["value"]) == Decimal(interval["normalized_value"]) == measured
            and tuple(map(Decimal, interval["normalized_rounding_interval"])) == bounds,
            "Independent source token, units or printed rounding bounds differ")
    return measured, bounds


def verify_formula(value, inputs):
    assessment = value["assessment"]
    formula_id = assessment["formula_id"]
    recipes = {
        "contained-treo-from-mt-ppm-v1": ("grade", "ppm", Decimal("0.001")),
        "contained-treo-from-mt-pct-v1": ("grade", "%", Decimal(10)),
        "contained-reported-mreo-mt-ppm-v1": ("mreo_ppm", "ppm", Decimal("0.001")),
    }
    require(formula_id in recipes and assessment["status"] == "APPROVED", "New formula falls outside the independent verifier")
    by_raw = {(row["normalized"]["raw_record_id"] or row["raw"]["native_raw_id"] or row["raw"]["observation_id"]): row
              for row in inputs}
    eligible = [by_raw[key] for key in value["input_observation_ids"]]
    for entry in eligible:
        raw = entry["raw"]
        require((raw["statement_id"], raw["scope_id"], raw["project_id"], raw["source_sha"])
                == (assessment["statement_id"], assessment["scope_id"], value["project_id"], assessment["source_sha256"]),
                "New derived value mixes statement, scope, Project or source")
    variables = {row["raw"]["variable"]: row for row in eligible}
    precision = {row["variable"]: row for row in assessment["inputs"]}
    concentration, unit, factor = recipes[formula_id]
    ore, ore_bounds = _reported_measurement(variables["ore_mt"], precision["ore_mt"], "Mt")
    grade, grade_bounds = _reported_measurement(variables[concentration], precision[concentration], unit)
    amount = ore * grade * factor
    lower, upper = map(Decimal, assessment["rounding_interval"])
    require(amount == Decimal(value["value"])
            and (lower, upper) == tuple(tonnage * percentage * factor for tonnage, percentage in zip(ore_bounds, grade_bounds, strict=True)),
            f"Independent scientific formula value or interval differs for {formula_id}")
    return {"value": str(amount), "formula_id": formula_id, "input_observation_ids": value["input_observation_ids"],
            "rounding_interval": [str(lower), str(upper)]}


def compare_census(before, after, added, approved, derived, locks):
    left_columns, left_projects, left = read_census(before)
    columns, projects, right = read_census(after)
    require((left_columns, left_projects) == (columns, projects) and left.keys() == right.keys(),
            "Protected Census 93×51 keys or order changed")
    preserved = 0
    change_rows = []
    identities = {item["acceptance_id"]: item for item in added.values()}
    stable_top = {"release_id", "lineage_id", "selection_id", "metadata_evidence", "context_diagnostics"}
    for key, prior in left.items():
        actual = right[key]
        if prior["value_state"] == "PRESENT":
            preserved += 1
            skipped = stable_top
            if prior["value_origin"] == "FORMAL_SELECTION":
                item = approved[actual["selection_id"]]
                require((actual["observation_id"], actual["validation_id"], actual["rules_sha256"])
                        == (item["raw"]["observation_id"], item["validation"]["validation_id"], item["validation"]["rules_sha"]),
                        f"Revalidated Census row is not linked to the exact Store selection {key}")
                skipped = stable_top | {"validation_id", "rules_sha256"}
            elif prior["value_origin"] == "FORMAL_DERIVED":
                if prior["rules_sha256"] != actual["rules_sha256"]:
                    require([prior["rules_sha256"], actual["rules_sha256"]] == locks, f"Unexpected formula release rule pin change {key}")
                    skipped = stable_top | {"rules_sha256"}
            previous_context, current_context = (json.loads(row["context_diagnostics"] or "[]") for row in (prior, actual))
            require(without(previous_context, {"validation_id"}) == without(current_context, {"validation_id"}),
                    f"Stable scientific context diagnostics changed for {key}")
            changes = [field for field in prior if prior[field] != actual[field] and field not in skipped]
            require(not changes, f"Protected value/evidence changed {key}: {changes}")
            ignored = PROTECTED_CONTEXT | DERIVED_IDS
            old_evidence, new_evidence = map(field_evidence, (prior, actual))
            if prior["value_origin"] == "EVIDENCE_REFERENCE":
                for evidence, lock in zip((old_evidence, new_evidence), locks, strict=True):
                    require(all(item["validation_rules_sha256"] == lock for item in evidence["inputs"]),
                            f"Evidence reference no longer pins its exact validation rules {key}")
                ignored |= {"validation_rules_sha256"}
            require(without(old_evidence, ignored) == without(new_evidence, ignored),
                    f"Protected evidence changed for {key}")
            continue
        if prior["value_state"] == actual["value_state"]:
            continue
        require((prior["value_state"], actual["value_state"]) == ("UNKNOWN", "PRESENT"), f"Unsupported Census change {key}")
        kind = actual["value_origin"]
        if kind == "FORMAL_SELECTION":
            require(actual["selection_id"] in identities, f"Census cell lacks new validated selection {key}")
            item = identities[actual["selection_id"]]
            require((actual["observation_id"], actual["source_sha256"], actual["scope_id"])
                    == (item["observation_id"], item["exact_original"]["source_sha256"], item["scope_id"]),
                    f"Census cell mismatches original selected observation {key}")
        elif kind == "FORMAL_DERIVED":
            identity = field_evidence(actual)["derived_value_id"]
            require(actual["formula_sha256"] and actual["input_observation_ids"] and identity in derived,
                    f"Unpinned Stage 5 derivation {key}")
            require(Decimal(actual["value"]) == Decimal(derived[identity]["value"]), f"Census derivation differs for {key}")
        else:
            require(typed(actual), f"Census gained unsupported non-scientific content {key}")
        change_rows.append({"project_id": key[0], "column": key[1], "new_value": actual["value"], "kind": kind,
                            "source_sha256": actual["source_sha256"], "observation_id": actual["observation_id"],
                            "scope_id": actual["scope_id"], "temporal_decision_id": actual["temporal_decision_id"],
                            "precision": actual["precision"], "raw_value": actual["raw_value"], "raw_unit": actual["raw_unit"]})
    require(preserved == 1138, "Stage 4 original present count not reproduced")
    return {"protected_existing_values": preserved, "changes": change_rows,
            "change_counts": dict(Counter(row["kind"] for row in change_rows)), "denominator": 4743}


def compare_mcda(before, after, approved):
    for build in (before, after):
        inputs, results = (build / "decision" / name for name in ("mcda_inputs.json", "mcda_results.json"))
        decision = read(build / "decision/decision_receipt.json")
        require(read(results)["mcda_inputs_sha256"] == decision["mcda_inputs_sha256"] == sha_file(inputs)
                and decision["mcda_results_sha256"] == sha_file(results), "MCDA receipt does not bind actual inputs and output")
    old, new = (read(build / "decision/mcda_inputs.json") for build in (before, after))
    policy = ("policy_sha256", "policy", "policy_version", "semantic_policy_version", "profile_id", "registry_sha256",
              "research_cutoff", "research_timezone")
    require(all(old[key] == new[key] for key in policy), "MCDA method, policy or research scope changed")
    def criteria(data):
        return {(project, code): row for project, rows in data["criteria_by_project"].items()
                for code, row in rows.items() if row["state"] == "COMPARABLE"}
    left, right = map(criteria, (old, new))
    require(Counter(code for _, code in left) == {"R1": 4, "S1": 2} and left.keys() <= right.keys(),
            "Prior comparable keys were not all preserved")
    for key, prior in left.items():
        require(without(prior, PROTECTED_CONTEXT) == without(right[key], PROTECTED_CONTEXT), f"Comparable prior criterion changed: {key}")
    added = []
    for project, code in sorted(right.keys() - left.keys()):
        record = right[project, code]
        require(record["lineage"] and all(
            approved[link["selection_id"]]["raw"]["observation_id"] == link["observation_id"]
            and approved[link["selection_id"]]["validation"]["validation_id"] == link["validation_id"]
            for link in record["lineage"]),
                "New comparable key does not use eligible Store readings")
        added.append({"project_id": project, "criterion": code, "value": record["accepted_value"], "unit": record["unit"],
                      "source_observation_ids": sorted({link["observation_id"] for link in record["lineage"]})})
    panels = [read(root / "decision/mcda_results.json")["panels"] for root in (before, after)]
    require(panels[0].keys() == panels[1].keys(), "MCDA panel registry changed")
    changed_panels = [key for key in panels[0] if without(panels[0][key], PROTECTED_CONTEXT) != without(panels[1][key], PROTECTED_CONTEXT)]
    if not added:
        require(not changed_panels and without(old["panels"], PROTECTED_CONTEXT) == without(new["panels"], PROTECTED_CONTEXT),
                "MCDA matrices, ranks or sensitivity changed without comparability gain")
    criteria_deltas = []
    require(old["criteria_by_project"].keys() == new["criteria_by_project"].keys(), "MCDA changed the fixed cohort")
    for project, prior in old["criteria_by_project"].items():
        actual = new["criteria_by_project"][project]
        require(prior.keys() == actual.keys(), "MCDA changed the fixed criterion registry")
        for criterion, row in prior.items():
            candidate = actual[criterion]
            before_state = (row["state"], row["reason"])
            after_state = (candidate["state"], candidate["reason"])
            if before_state != after_state:
                criteria_deltas.append({"project_id": project, "criterion": criterion,
                                        "before_state_reason": before_state, "after_state_reason": after_state})
    complete = lambda kind: sorted(project for project, code in kind if code == "R1" and (project, "S1") in kind)
    return {"policy_sha256": old["policy_sha256"], "comparable_before": len(left), "comparable_after": len(right),
            "complete_R1_S1_before": complete(left), "complete_R1_S1_after": complete(right), "new_criteria": added,
            "changed_panels": changed_panels, "criterion_disposition_deltas": criteria_deltas}


def compare_display(before, after):
    old, new = map(read, (before, after))
    columns = [field["field_id"] for field in old["columns"]]
    require(columns == [field["field_id"] for field in new["columns"]] and len(columns) == 9,
            "The 9-field display matrix shifted its denominator")
    require([row["id"] for row in old["projects"]] == [row["id"] for row in new["projects"]],
            "Extension changed fixed Project IDs or row order")
    before_facts, after_facts = (
        {stable_hash(without(record, PROTECTED_CONTEXT)): record for record in document["facts"].values()}
        for document in (old, new)
    )
    require(before_facts.keys() <= after_facts.keys(), "A protected display fact changed original semantics")
    added = [after_facts[key] for key in sorted(after_facts.keys() - before_facts.keys())]
    before_review, after_review = (
        {stable_hash(without(record, PROTECTED_CONTEXT)) for record in document["review_only"]} for document in (old, new)
    )
    require(before_review <= after_review, "Protected display review controls changed scientific meaning")
    role_delta = Counter(record["data_role"] for record in added)
    return {"denominator": 837, "present_before": sum(old["coverage"][field]["present"] for field in columns),
            "present_after": sum(new["coverage"][field]["present"] for field in columns),
            "contract_hash_before_after": [old["contract_sha256"], new["contract_sha256"]],
            "facts_before_after": [len(before_facts), len(after_facts)],
            "review_before_after": [len(before_review), len(after_review)], "new_display_roles": dict(role_delta),
            "new_field_values": [{key: record[key] for key in ("project_id", "field_id", "value", "raw_token", "raw_unit",
                                                               "scope", "data_role", "source_sha256", "locator")}
                                 for record in added]}


def audit(before, after, sources, cas):
    require(sha_file(sources) == FIXED_SOURCE, "Scientific gain must be separately evaluated against the fixed source universe")
    old, new = [jsonl(path / "observations/observations.jsonl", "observation_id") for path in (before, after)]
    require(len(old) == 42 and old.keys() <= new.keys(), "Baseline observation set was not preserved")
    for key, row in old.items():
        require(row == new[key], f"Prior original extraction, typing or admission outcome changed: {key}")
    added, approved, selected, derived, locks = compare_store(before, after, read(sources), cas)
    forms = compare_census(before, after, added, approved, derived, locks)
    decision = compare_mcda(before, after, approved)
    return {"status": "PASS", "source_universe": FIXED_SOURCE, "sqlite_integrity_and_foreign_key_checks": "PASS",
            "observations_before_after": [len(old), len(new)],
            "new_validated_observations": [new[key] for key in sorted(new.keys() - old.keys()) if new[key]["outcome"] == "VALIDATED"],
            "validated_before_after": [sum(row["outcome"] == "VALIDATED" for row in dataset.values()) for dataset in (old, new)],
            "selected_before_after": selected, "new_selections": added, "new_derived_formulas": derived,
            "validation_rule_locks_before_after": locks, "census": forms, "mcda": decision}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "sources", "cas", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--replicas", type=Path, nargs=2)
    parser.add_argument("--replays", type=Path, nargs=2)
    parser.add_argument("--display-views", type=Path, nargs=2, metavar=("STAGE4_JSON", "STAGE5_JSON"))
    args = parser.parse_args()
    analysis = audit(args.baseline, args.candidate, args.sources, args.cas)
    paths = (args.archive_root, args.release_id, args.replicas, args.replays)
    if any(paths):
        if not all(paths):
            parser.error("Provide all archive, release, replica and replay arguments together")
        analysis["cold_archive"] = archive_closure(*paths, args.candidate)
    if args.display_views:
        analysis["extension"] = compare_display(*args.display_views)
    with args.out.open("x", encoding="utf-8") as destination:
        json.dump(analysis, destination, ensure_ascii=False, indent=2)
        destination.write("\n")
    print(json.dumps({"status":analysis["status"],"out":str(args.out)}))
