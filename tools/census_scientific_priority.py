"""Audit a frozen Census and order exact-original scientific evidence work."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import platform

from au_ree_v3.census_metadata import load_projection_policy
from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.sources import ContentStore
from tools.census_display_coverage import compare_legacy, index_csv, prior_work, scoped_projects
from tools.census_gap_inventory import (
    FIXED_SOURCES, SOURCE_SHA, V2_PROVENANCE, V2_PROVENANCE_SHA, V2_REF,
    check_bytes, checked, fingerprint, git_blob, read_jsonl, require, write_csv,
)
from tools.verify_census_stage3_release import check_store, verify_closure, verify_replay

ROOT = Path(__file__).resolve().parents[1]
MAIN = "44bc122d1337434cb8f496d8d0d6d0dd2dc4d981"
CANDIDATE = "a1805f68b22d73ab070be50f6d2a110edfcb30230e21778b6b62513ccbcc96ee"
CODE = "69a111dd56420d364c1ddf2547fe81888b4e51ed"
PROFILE = "config/research_profiles/au_ree_census_display_v1.json"
PREVIOUS = "work/census-stage4/final"
VERIFICATION = "docs/research/census_display_coverage_verification.json"
STAGE3_VERIFICATION = "docs/research/census_stage3_verification.json"
RECOVERY = "docs/research/native_resource_table_recovery_receipt.json"
EDITION_RECOVERY = "work/stage4-context/edition-recovery-final/source_edition_recovery.json"
QUEUE = "work/census-stage4/coverage/source_attribute_work_queue.jsonl"
BINDING = "work/census-stage3/binding/verified/expanded-1"
DISPLAY_FILES = ("census_display.json", "census_display_extended.csv", "census_display_facts.jsonl",
                 "display_package_receipt.json")
FORMAL_GROUPS = {"resource", "oxide", "aggregate"}
MAGNET_FIELDS = {"magnet_oxide_kt", "magnet_pct_of_treo", "recalculated_magnet_oxide_kt",
                 "ndpr_kt", "nd2o3_kt", "pr6o11_kt", "tb4o7_kt", "dy2o3_kt"}
RESOURCE_FIELDS = {"ore_mt", "treo_grade_pct", "contained_treo_kt"}
VARIABLES = {"ore_mt": "ore_mt", "grade": "treo_grade_pct", "contained_treo_kt": "contained_treo_kt"}
ACTION = {
    "HISTORICAL_OBSERVATION_DATE_MISSING": "BIND_ORIGINAL_EFFECTIVE_DATE_FOR_EXACT_STATEMENT",
    "CONFLICTING_CONTAINED_TREO_IN_SAME_STATEMENT": "RECONCILE_CONFLICT_ON_ORIGINAL_SCOPE_AND_NATIVE_PRECISION",
    "TREO_CHEMICAL_DEFINITION_UNBOUND": "BIND_EXACT_OXIDE_MEMBERS_AND_DENOMINATOR",
    "EXACT_SOURCE_AND_RESOURCE_EVIDENCE": "VERIFY_PURPOSE_AND_REPORTING_SCOPE_WITHOUT_PROMOTING_SHADOW",
}


def pinned(path):
    content = git_blob(ROOT, MAIN, path)
    require((ROOT / path).read_bytes() == content, f"Pinned file differs: {path}")
    return json.loads(content), fingerprint(content)


def fixed_baseline(workspace, cold, display, verification):
    old = workspace / PREVIOUS
    archive = json.loads(checked(old / "archive/releases" / f"{CANDIDATE}.json", CANDIDATE))
    build_dir = old / "build"
    build = ResearchBuild.load(build_dir)
    require(archive["code_commit"] == CODE == build.receipt["code_commit"] == verification["candidate_code_commit"]
            and build.receipt_sha == archive["build_receipt_sha256"] and
            archive["input_artifacts"] == build.receipt["input_artifacts"] == verification["profile_input_artifacts"],
            "Frozen candidate code or input receipt differs")
    outputs = archive["output_hashes"]
    require(len(outputs) == 8 and outputs == build.receipt["scientific_artifacts"] == verification["scientific_output_hashes"],
            "Scientific artifact pins differ")
    require(len(cold) == len({p.resolve() for p in cold}) == 2 and len(display) == len({p.resolve() for p in display}) == 2,
            "Require two distinct CAS replays and two distinct display outputs")
    cas_roots = (old / "cas", old / "replica-cas")
    keys = {CANDIDATE, *archive["required_object_sha256"]}
    for cas, science, presentation in zip(cas_roots, cold, display, strict=True):
        verify_closure(cas, keys)
        verify_replay(science, CANDIDATE, outputs)
        for name, digest in outputs.items():
            checked(science / "rebuilt" / name, digest)
        for name in DISPLAY_FILES:
            digest = verification["other_verification_artifact_sha256"][f"{PREVIOUS}/display/{name}"]
            require(checked(presentation / name, digest) == checked(old / "display" / name, digest),
                    f"Frozen display output differs: {name}")
    display_receipt = json.loads((old / "display/display_package_receipt.json").read_bytes())
    require(display_receipt["release_id"] == CANDIDATE and display_receipt["build_receipt_sha256"] == build.receipt_sha
            and display_receipt["contract_sha256"] == verification["display_contract_sha256"], "Display archive lineage differs")
    manifest = json.loads(checked(workspace / FIXED_SOURCES, SOURCE_SHA))
    profile = load_profile(ROOT / PROFILE)
    status = verify_source_manifest(profile, manifest, load_registry(profile.registry_path), ContentStore(cas_roots[0]))
    require(status["status"] == "PASS" and len(status["verified_sha256"]) == len(status["eligible_sha256"]) == 144
            and len(status["verified_capture_sha256"]) == 148 and status["projects"] == 93, "Fixed source scope differs")
    states = Counter(row["evidence_state"] for row in build.census.values())
    criteria = Counter(code for group in build.decision_inputs["criteria_by_project"].values()
                       for code, row in group.items() if row["state"] == "COMPARABLE")
    baseline = {
        "grid_keys": len(build.census), "project_count": len(build.census_projects), "column_count": len(build.census_columns),
        "present": sum(r["value_state"] == "PRESENT" for r in build.census.values()),
        "formal_direct": states["FORMAL_DIRECT"], "formal_derived": states["FORMAL_DERIVED"],
        "validated_descriptive": states["VALIDATED_DESCRIPTIVE"], "meaningful_projects": len(scoped_projects(build)),
        "extension_fields": len(display_receipt["coverage"]),
        "extension_present": sum(row["present"] for row in display_receipt["coverage"].values()),
        "comparable_keys": dict(criteria),
        "complete_panels_projects": build.decision_results["panels"]["issuer_resource_composition"]["eligible_count"],
        **build.receipt["observation_counts"],
    }
    require(baseline == {
        "grid_keys": 4743, "project_count": 93, "column_count": 51, "present": 1138, "formal_direct": 16,
        "formal_derived": 3, "validated_descriptive": 27, "meaningful_projects": 12, "extension_fields": 9,
        "extension_present": 39, "comparable_keys": {"R1": 4, "S1": 2}, "complete_panels_projects": 2,
        "candidate_count": 42, "validated_count": 27, "selected_count": 21,
    }, "Frozen display, scientific or comparison denominators differ")
    require(build.receipt["input_artifacts"]["profile"] == fingerprint((ROOT / PROFILE).read_bytes()),
            "Historical root profile does not match the archived profile input")
    return build, manifest, {
        "frozen_main": MAIN, "candidate": CANDIDATE, "candidate_code": CODE,
        "source_manifest_sha256": SOURCE_SHA, "input_artifacts_sha256": archive["input_artifacts"],
        "scientific_outputs_sha256": outputs,
        "display_outputs_sha256": {name: verification["other_verification_artifact_sha256"][f"{PREVIOUS}/display/{name}"]
                                   for name in DISPLAY_FILES},
        "display_settings_sha256": display_receipt["input_files"], "display_contract_sha256": display_receipt["contract_sha256"],
        "selection_rules_sha256": build.receipt["selection_rules_hash"], "environment": archive["environment_hash"],
        "environment_lock": build.receipt["environment"], "runtime_machine": platform.machine(),
        "cold_replay": {"science_runs": 2, "display_runs": 2, "science_files_per_run": 8, "display_files_per_run": 4,
                        "readback_objects_per_science_CAS": len(keys), "status": "PASS", "fault_domain": "SHARED_LOCAL_FILESYSTEM"},
        "sqlite": check_store(build_dir),
        "coverage": {**baseline, "verified_display": 46, "original_sha_count": 144, "eligible_capture_count": 148},
        "gates": {**build.receipt["gates"], "F4": "PASS", "F5": archive["replica_inventory"]["status"],
                  "historical_O2": verification["gates"]["O2_HISTORICAL"]},
    }


def read_backlog(workspace, verification):
    stage3, stage3_pin = pinned(STAGE3_VERIFICATION)
    gap, _, editions, work, prior = prior_work(workspace, stage3)
    recovery, recovery_pin = pinned(RECOVERY)
    binding_pin = recovery["exact_expanded_candidate_reruns"]["receipt_sha256"]
    binding_path = workspace / BINDING / "table_binding_receipt.json"
    binding = json.loads(checked(binding_path, binding_pin))
    native = {name: read_jsonl(checked(workspace / BINDING / name, pin)) for name, pin in binding["outputs"].items()}
    other = verification["other_verification_artifact_sha256"]
    historical = read_jsonl(checked(workspace / QUEUE, other[QUEUE]))
    recovery_attempts = json.loads(checked(workspace / EDITION_RECOVERY, other[EDITION_RECOVERY]))
    outcomes = {row["expected_sha256"]: row["result"] for row in recovery_attempts["cases"]}
    require(recovery_attempts["requested_public_urls"] == 7 and
            recovery_attempts["exact_requested_editions_recovered"] == 0 and len(outcomes) == 4
            and {row["source_sha256"] for row in editions} == outcomes.keys(),
            "Historical source recovery scope differs")
    edition_report = [{"source_sha256": row["source_sha256"], "stage3_retrieval_hint": row["reason"],
                       "stage4_exact_public_edition": outcomes[row["source_sha256"]]} for row in editions]
    return gap, edition_report, work, native, historical, {
        **prior, STAGE3_VERIFICATION: stage3_pin, RECOVERY: recovery_pin, QUEUE: other[QUEUE],
        EDITION_RECOVERY: other[EDITION_RECOVERY],
        f"{BINDING}/table_binding_receipt.json": binding_pin,
    }


def field_relevance(field, group):
    if field in RESOURCE_FIELDS:
        return {"R1", "S1"} if field == "treo_grade_pct" else {"R1"}
    if field in MAGNET_FIELDS:
        return {"S1"}
    if group == "development":
        return {"D1"}
    if field in {"latitude", "longitude"}:
        return {"I1", "I2", "I3", "I4"}
    return set()


def evidence_key(project, sha, edition, scope, date, cutoff, analyte, locator, *, source_request_family=None):
    """Unbound dimensions remain null; never infer identity from a similar title."""
    return {
        "project_id": project, "source_sha256": sha, "source_edition_id": edition, "statement_scope": scope,
        "estimate_date": date, "cutoff": cutoff, "analyte": analyte, "raw_locator": locator,
        "source_request_family": source_request_family,
    }


def source_index(manifest):
    editions, sources = {}, defaultdict(set)
    for row in manifest["editions"]:
        if row["temporal_decision"]["status"] == "ELIGIBLE":
            editions[row["edition_id"]] = row
            for project in row["project_ids"]:
                sources[project, row["sha256"]].add(row["edition_id"])
    return editions, sources


def unbound_locator(row):
    native = row.get("native_evidence") or {}
    anchor = native.get("value_anchor") or {}
    return anchor.get("locator") or {"page": row["physical_page"], "table": row["table"]["locator"],
                                      "token_columns": row["token_columns"], "position_id": row["source_position_id"]}


def observation_candidates(rows, exact):
    for row in rows:
        if row["selected_for_census"] or row["variable"] not in VARIABLES:
            continue
        project, sha = row["project_id"], row["source_sha256"]
        matching = exact[project, sha]
        require(matching, "Scoped original observation lacks a currently eligible edition")
        yield (
            evidence_key(project, sha, min(matching), {"scope_id": row["scope_id"], "statement_id": row["statement_id"]},
                         row["effective_date"], {"grade": row["cutoff_grade"], "unit": row["cutoff_unit"]},
                         row["cutoff_analyte"], {"page": row["page"], "table": row["table"], "row": row["row"],
                                                 "column": row["column"]}),
            {VARIABLES[row["variable"]]}, "REVIEWED_ORIGINAL_OBSERVATION", row["reason"],
            ACTION[row["reason"]], row["observation_id"], row.get("raw_value"),
        )


def native_candidates(native, editions, stage3):
    for name in ("typed_candidate_observations.jsonl", "unbound_numeric_tokens.jsonl", "discovered_native_rows.jsonl"):
        for row in native[name]:
            sha, project, edition = row["source_sha256"], row["project_id"], row["edition_id"]
            original = editions[edition]
            require(original["sha256"] == sha and project in original["project_ids"], "Raw table case edition differs from exact parent")
            if name == "typed_candidate_observations.jsonl":
                variable = row["variable"]
                # A raw ppm token may be an input to a governed oxide mass derivation.
                # Joining a target column leaves the original role and units intact.
                field = VARIABLES.get(variable) or (variable.removesuffix("_ppm") + "_kt"
                                                     if variable and variable.endswith("_ppm") else None)
                fields = {field} if field else set()
                locator = row["value_anchor"]["locator"]
                reason, action = "STATEMENT_SCOPE_AND_USE_UNVALIDATED", "VERIFY_SAME_STATEMENT_CUTOFF_DATE_MATERIAL_AND_CHEMISTRY"
            elif name == "unbound_numeric_tokens.jsonl":
                fields, locator, reason = set(), unbound_locator(row), row["reason"]
                action = "BIND_NATIVE_HEADER_AND_QUANTITY_OR_RETAIN_UNBOUND"
            else:
                fields, locator, reason = set(), row["row_locator"], row["statement_scope_status"]
                action = "BIND_EXACT_RESOURCE_STATEMENT_AND_SCOPE_BEFORE_COMPARISON"
            yield (evidence_key(project, sha, edition, None, row.get("estimate_effective_date"), row.get("cutoff_context"),
                                None, locator), fields, name.removesuffix(".jsonl").upper(), reason, action,
                   row["case_id"], row.get("source_token"))
    unstructured = [row for row in native["prior_binding_dispositions.jsonl"] if row["status"] == "NO_TABLE_STRUCTURE"]
    require(len(unstructured) == stage3["unrecognized_structure"], "No-structure work count differs")
    for row in unstructured:
        yield (evidence_key(row["project_id"], row["source_sha256"], row["edition_id"], None, None, None, None, None),
               set(), "NO_TABLE_STRUCTURE", row["reason"], "LOCATE_ORIGINAL_NATIVE_TABLE", row["case_id"], None)


def inherited_candidates(legacy, unknown, exact):
    for row in legacy:
        project, sha = row["project_id"], row["source_sha256"]
        targets = {r["column_id"] for r in row["fields"]} & unknown[project]
        if not targets:
            continue
        choices = exact.get((project, sha))
        edition = min(choices) if choices else None
        # Requests with no original stay distinct by evidence family. An archived SHA is preserved as a search key.
        request = row["assertion_family"] if not edition else None
        yield (evidence_key(project, sha, edition, None, None, None, None, None, source_request_family=request), targets,
               row["route"], row["route"], " | ".join(row["next_verification"]), row["queue_id"], None)


def direction(record, definitions, existing_criteria):
    project, fields, kinds = record["project_id"], record["field_ids"], record["kinds"]
    relevance = set().union(*(field_relevance(field, definitions[field]["display_group"]) for field in fields))
    missing = sorted(code for code in relevance if existing_criteria[project][code]["state"] != "COMPARABLE")
    numeric = any(definitions[field]["display_group"] in FORMAL_GROUPS for field in fields)
    blocked = bool(kinds & {"SCIENTIFICALLY_INCOMPATIBLE", "EXACT_EDITION_REQUIRED"}) or any(
        "CONFLICT" in reason for reason in record["blocking_reasons"])
    raw = bool(record["raw_locator"] and record["source_edition_id"])
    single = bool("REVIEWED_ORIGINAL_OBSERVATION" not in kinds and record["native_tokens"] and raw and numeric
                  and any(field["value_state"] == "UNKNOWN" for field in record["baseline_fields"]))
    if blocked or not record["source_edition_id"]:
        tier = 5 if numeric else 7
    elif numeric and raw:
        tier = 1 if missing else 2
    elif numeric or kinds & {"UNBOUND_NUMERIC_TOKENS", "DISCOVERED_NATIVE_ROWS", "NO_TABLE_STRUCTURE"}:
        tier = 3
    elif relevance or any(definitions[field]["display_group"] in {"geology", "development", "location"} for field in fields):
        tier = 4
    else:
        tier = 6
    state = "SCIENTIFIC_CONFLICT" if any("CONFLICT" in item for item in record["blocking_reasons"]) or (
        "SCIENTIFICALLY_INCOMPATIBLE" in kinds) else "EVIDENCE_INSUFFICIENT"
    if "EXACT_EDITION_REQUIRED" in kinds:
        state = "SOURCE_UNAVAILABLE"
    if not record["source_edition_id"] and state == "EVIDENCE_INSUFFICIENT":
        state = "NOT_RUN"
    return {
        "priority_tier": tier, "disposition": state, "mcda_relevance": sorted(relevance),
        "existing_comparable_criteria": sorted(relevance - set(missing)),
        "conditional_new_criterion_targets": missing,
        "upper_bound": {
            "new_independent_direct_observations_for_one_located_token": int(single and not blocked),
            "new_project_criterion_keys_if_all_purpose_requirements_met": len(missing) if not blocked else 0,
            "new_complete_r1_s1_panel_project_if_all_requirements_met": int(
                bool(set(missing) & {"R1", "S1"}) and all(existing_criteria[project][code]["state"] == "COMPARABLE"
                or code in missing for code in ("R1", "S1")) and not blocked),
            "bounded_by": "REVIEW_TARGET_ONLY; JOINT_ORIGINAL_SCOPE_TIME_CHEMISTRY_AND_POLICY_NOT_ESTABLISHED",
        },
    }


def assemble_queue(build, definitions, candidates, editions):
    project_cells = {(row["project_id"], row["column_id"]): row for row in build.census.values()}
    records = {}
    for key, fields, kind, reason, action, record_id, token in candidates:
        # Keep blockers for previously selected cells available for exact-estimate review.
        # Inherited display retrieval entries only carry fields still unknown in this candidate.
        identity = fingerprint(canonical_json(key).encode())
        if identity not in records:
            original = editions.get(key["source_edition_id"])
            records[identity] = {
                **key, "queue_id": identity,
                "source_candidate": {"status": "ELIGIBLE_EXACT_ORIGINAL", "url": original["url"],
                                     "capture_receipt_sha256": original["receipt_sha256"]} if original else {
                    "status": "UNRESOLVED_SOURCE_REQUEST", "url": None, "capture_receipt_sha256": None},
                "field_ids": set(), "kinds": set(), "blocking_reasons": set(), "next_evidence": set(),
                "input_case_ids": set(), "native_tokens": set(), "candidate_only": True,
                "formal_admission": False,
            }
        record = records[identity]
        record["field_ids"].update(field for field in fields if field in definitions)
        record["kinds"].add(kind)
        record["blocking_reasons"].add(reason)
        record["next_evidence"].add(action)
        record["input_case_ids"].add(record_id)
        if token is not None:
            record["native_tokens"].add(token)
    for row in records.values():
        row["baseline_fields"] = [
            {"field_id": field, "value_state": project_cells[row["project_id"], field]["value_state"],
             "evidence_class": project_cells[row["project_id"], field]["evidence_state"]}
            for field in sorted(row["field_ids"])
        ]
        row.update(direction(row, definitions, build.decision_inputs["criteria_by_project"]))
        for name in ("field_ids", "kinds", "blocking_reasons", "next_evidence", "input_case_ids", "native_tokens"):
            row[name] = sorted(row[name])
    return sorted(records.values(), key=lambda row: (
        row["priority_tier"], -len(row["conditional_new_criterion_targets"]), row["project_id"], row["queue_id"]))


def gap_grid(build, policy, historical, prior_gap, queue, manifest):
    require(build.census.keys() == historical.keys() == prior_gap.keys(), "Historical or current grid keys differ")
    require(sum(bool(row["value"]) for row in historical.values()) == 2500, "Expected all frozen historic display values")
    by_key = defaultdict(list)
    for row in queue:
        for field in row["field_ids"]:
            by_key[row["project_id"], field].append(row)
    _, sources = source_index(manifest)
    result = []
    for key, cell in build.census.items():
        project, label = key
        field = cell["column_id"]
        definition, old, gap = policy[label], historical[key], prior_gap[key]
        leads = by_key[project, field]
        source = old["source_sha256"] if old["value"] else ""
        exact_v2 = bool(sources.get((project, source)))
        if cell["value_state"] == "PRESENT":
            issue = "ALREADY_PRESENT"
        elif cell["value_state"] == "NOT_APPLICABLE":
            issue = "AFFIRMATIVE_NOT_APPLICABLE"
        else:
            issue = gap["gap_class"]
        result.append({
            "project_id": project, "column": label, "field_id": field, "data_role": cell["data_role"],
            "value_state": cell["value_state"], "evidence_class": cell["evidence_state"], "reason": cell["reason"],
            "gap": issue, "v2_comparison": compare_legacy(cell["value"], old["value"], definition["data_type"]),
            "v2_claimed_source_sha256": source, "v2_source_exact_for_project_in_fixed_manifest": exact_v2,
            "v2_value_sha256": fingerprint(old["value"].encode()) if old["value"] else "",
            "v3_value_sha256": fingerprint(cell["value"].encode()) if cell["value"] else "",
            "selected_observation_id": cell["observation_id"], "selected_source_sha256": cell["source_sha256"],
            "selection_id": cell["selection_id"], "lineage_id": cell["lineage_id"],
            "source_candidates": sorted({row["source_sha256"] for row in leads if row["source_sha256"]}),
            "queue_ids": [row["queue_id"] for row in leads],
            "minimum_priority_tier": min((row["priority_tier"] for row in leads), default=""),
            "next_evidence": sorted({action for row in leads for action in row["next_evidence"]}),
            "blocking_reasons": sorted({reason for row in leads for reason in row["blocking_reasons"]}),
            "mcda_relevance": sorted(field_relevance(field, definition["display_group"])),
        })
    return result


def run(workspace, v2_root, cold, display, out):
    verification, verification_sha = pinned(VERIFICATION)
    build, manifest, baseline = fixed_baseline(workspace, cold, display, verification)
    gap, edition_gaps, work, native, inherited, pins = read_backlog(workspace, verification)
    v2 = index_csv(check_bytes(git_blob(v2_root, V2_REF, V2_PROVENANCE), V2_PROVENANCE_SHA, V2_PROVENANCE))
    profile = load_projection_policy(build.census_columns)
    definitions = {row["column_id"]: row for row in profile.rows}
    editions, exact = source_index(manifest)
    unknown = defaultdict(set)
    for row in build.census.values():
        if row["value_state"] == "UNKNOWN":
            unknown[row["project_id"]].add(row["column_id"])
    rows = read_jsonl((workspace / PREVIOUS / "build/observations/observations.jsonl").read_bytes())
    originals = (
        *observation_candidates(rows, exact),
        *native_candidates(native, editions, work),
        *inherited_candidates(inherited, unknown, exact),
    )
    queue = assemble_queue(build, definitions, originals, editions)
    grid = gap_grid(build, profile.by_name, v2, gap, queue, manifest)
    pins[VERIFICATION] = verification_sha
    pins[f"{V2_REF}:{V2_PROVENANCE}"] = V2_PROVENANCE_SHA
    out.mkdir(parents=True, exist_ok=False)
    queue_path = out / "census_scientific_priority_queue.jsonl"
    queue_path.write_text("".join(canonical_json(row) + "\n" for row in queue), encoding="utf-8")
    grid_path = out / "census_scientific_gap_grid.csv"
    write_csv(grid_path, grid)
    result = {
        "schema": "census-scientific-priority-v1", "status": "PASS", **baseline,
        "received_prior_unbound_sightings": work["unbound_sightings"],
        "received_prior_no_structure_jobs": work["unrecognized_structure"],
        "prior_edition_gaps": edition_gaps,
        "v2_display_comparison": dict(Counter(row["v2_comparison"] for row in grid)),
        "queue": {"input_mappings": len(originals), "unique_review_keys": len(queue),
                  "by_priority": dict(Counter(str(row["priority_tier"]) for row in queue)),
                  "by_disposition": dict(Counter(row["disposition"] for row in queue)),
                  "throughput_scope": "Frozen input receipt joins; zero PDF decodes in queue assembly",
                  "source_keys": sorted({row["source_sha256"] for row in queue if row["source_edition_id"]})},
        "increment": {"new_independent_verified_observations": 0, "new_selected_scientific_values": 0,
                      "new_comparable_project_criterion_keys": 0, "new_complete_panel_projects": 0},
        "input_receipt_sha256": pins, "outputs_sha256": {
            path.name: fingerprint(path.read_bytes()) for path in (queue_path, grid_path)},
    }
    (out / "census_scientific_baseline_receipt.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "v2-root", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    for name in ("science-replays", "display-replays"):
        parser.add_argument(f"--{name}", type=Path, nargs=2, required=True)
    args = parser.parse_args()
    receipt = run(args.workspace, args.v2_root, args.science_replays, args.display_replays, args.out)
    print(canonical_json({"status": receipt["status"], "coverage": receipt["coverage"],
                          "queue_keys": receipt["queue"]["unique_review_keys"]}))


if __name__ == "__main__":
    main()
