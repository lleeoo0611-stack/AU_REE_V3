"""Measure the fixed Census display baseline and prioritize original evidence review."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
from io import StringIO
import json
from pathlib import Path

from au_ree_v3.census_metadata import load_projection_policy, public_source_url
from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.sources import ContentStore
from tools.census_gap_inventory import (
    FIXED_SOURCES, SOURCE_SHA, V2_PROVENANCE, V2_PROVENANCE_SHA, V2_REF,
    check_bytes, checked, fingerprint, git_blob, read_jsonl, require, write_csv,
)
from tools.verify_census_stage3_release import check_store, comparable, evidence, verify_closure, verify_replay

ROOT = Path(__file__).resolve().parents[1]
MAIN = "293cdfedea31874e0d757a7513695aaa77da30f0"
CANDIDATE = "47bcd58e568dc912f78c887ad83377adc891f81a472b3e71a266cf2e5abd60dd"
PROFILE = "config/research_profiles/au_ree_census_evidence_v1.json"
STAGE3 = "work/census-stage3"
FINAL = f"{STAGE3}/final"
GAPS = f"{STAGE3}/a-final"
BINDING = f"{STAGE3}/binding/verified/expanded-1"
VERIFICATION = "docs/research/census_stage3_verification.json"
RECOVERY = "docs/research/native_resource_table_recovery_receipt.json"
VERIFIED = {"FORMAL_DIRECT", "FORMAL_DERIVED", "VALIDATED_DESCRIPTIVE", "SOURCE_REPORTED_DISPLAY"}
ROUTES = {"FIXED_ORIGINAL_REREAD": 1, "OFFICIAL_STRUCTURED_SOURCE": 2,
          "EXACT_EDITION_REQUIRED": 3, "SCIENTIFICALLY_INCOMPATIBLE": 4}
FORMULA_INPUTS = {"resource": "REPORTED_RESOURCE_FIELD", "oxide": "REPORTED_OXIDE_FIELD",
                  "aggregate": "REPORTED_COMBINATION_OR_APPROVED_FORMULA"}
GROUPS = {"geology": "DEPOSIT_TYPE", "development": "DEVELOPMENT_STAGE",
          "production": "PRODUCT_OR_FORECAST_SCOPE", "location": "REPRESENTATIVE_SITE_POINT",
          "identity": "LEGAL_OR_LISTING_STATUS", "source": "SOURCE_ATTRIBUTION", **FORMULA_INPUTS}
ATTRIBUTE_GROUPS = {"DEPOSIT_TYPE": "geology", "DEVELOPMENT_STAGE": "development"}
AUTHORITY_COLUMNS = {"latitude", "longitude", "unlisted_company", "asx_code"}


def pinned_document(path):
    blob = git_blob(ROOT, MAIN, path)
    require((ROOT / path).read_bytes() == blob, f"Verification record differs from fixed main: {path}")
    return json.loads(blob), fingerprint(blob)


def verify_candidate(workspace, current, replays, verification):
    """Read back archived bytes and the new fixed-main build without promoting a release."""
    prior = workspace / FINAL
    require(len(replays) == len({item.resolve() for item in replays}) == 2, "Two distinct cold replay directories required")
    index = prior / "archive/releases" / f"{CANDIDATE}.json"
    manifest = json.loads(checked(index, CANDIDATE))
    old, main = ResearchBuild.load(prior / "build"), ResearchBuild.load(current)
    require(main.receipt["code_commit"] == MAIN and manifest["code_commit"] ==
            verification["repository_code_at_candidate"] == old.receipt["code_commit"], "Candidate or new main Code SHA differs")
    require(old.receipt_sha == manifest["build_receipt_sha256"] and
            old.receipt["scientific_artifacts"] == manifest["output_hashes"] == verification["scientific_artifacts"],
            "Archived candidate science pins differ")
    dirs = (prior / "cas", prior / "replica-cas")
    pins = {CANDIDATE, *manifest["required_object_sha256"]}
    counts = [verify_closure(cas, pins) for cas in dirs]
    for replay in replays:
        verify_replay(replay, CANDIDATE, manifest["output_hashes"])
        for name, sha in manifest["output_hashes"].items():
            checked(replay / "rebuilt" / name, sha)
    manifest_bytes = checked(workspace / FIXED_SOURCES, SOURCE_SHA)
    source_manifest = json.loads(manifest_bytes)
    profile = load_profile(ROOT / PROFILE)
    received = verify_source_manifest(profile, source_manifest, load_registry(profile.registry_path),
                                      ContentStore(workspace / "work/fresh/source-store"))
    require(received["status"] == "PASS" and len(received["verified_sha256"]) ==
            len(received["eligible_sha256"]) == 144 and len(received["verified_capture_sha256"]) == 148
            and received["projects"] == 93, "Fixed original/source proof cohort differs")
    require(main.receipt["input_artifacts"] == old.receipt["input_artifacts"] == verification["input_artifacts"]
            and old.receipt["environment"] == main.receipt["environment"]
            and fingerprint((ROOT / PROFILE).read_bytes()) == main.receipt["input_artifacts"]["profile"],
            "Current build does not share fixed archived inputs and environment")
    require(list(main.census) == list(old.census) and main.observations == old.observations
            and comparable(prior / "build") == comparable(current)
            and old.decision_inputs["panels"] == main.decision_inputs["panels"], "Cohort, selected observations or MCDA changed")
    stable = ("value", "value_state", "evidence_state", "reason", "value_origin", "source_sha256", "scope_id",
              "statement_id", "temporal_decision_id", "effective_date", "raw_value", "raw_unit")
    require(all(tuple(old.census[key][field] for field in stable) == tuple(cell[field] for field in stable)
                for key, cell in main.census.items()), "Census value or stable original-source lineage differs")
    identical = sorted(k for k, sha in old.receipt["scientific_artifacts"].items()
                       if sha == main.receipt["scientific_artifacts"][k])
    require({"census/census.csv", "observations/observations.jsonl"} <= set(identical),
            "Core Census presentation values or original observations are no longer byte-identical")
    return old, source_manifest, {
        "code_commit": MAIN, "candidate_release_id": CANDIDATE, "candidate_code_commit": manifest["code_commit"],
        "candidate_build_receipt_sha256": old.receipt_sha, "main_build_receipt_sha256": main.receipt_sha,
        "build_inputs_sha256": main.receipt["input_artifacts"], "environment": main.receipt["environment"],
        "candidate_scientific_artifacts_sha256": manifest["output_hashes"],
        "main_scientific_artifacts_sha256": main.receipt["scientific_artifacts"],
        "identical_main_vs_candidate_artifacts": identical,
        "sources": {"unique_sha256": 144, "eligible_captures": 148,
                    "verified_sha256": received["verified_sha256"],
                    "verified_capture_sha256": received["verified_capture_sha256"]},
        "cold_replay": {"status": "PASS", "runs": len(replays), "artifacts_per_run": 8, "objects_per_cas": counts,
                        "same_filesystem": dirs[0].stat().st_dev == dirs[1].stat().st_dev},
        "sqlite": check_store(current), "mcda_equivalence": "PASS",
        "gates": {**main.receipt["gates"], "F4": "PASS", "F5": manifest["replica_inventory"]["status"],
                  "O2_HISTORICAL": verification["gates"]["O2_HISTORICAL"]},
    }


def cell_key(cell):
    return cell["project_id"], cell["column"]


def index_csv(payload):
    rows = list(csv.DictReader(StringIO(payload.decode("utf-8"), newline="")))
    index = {cell_key(row): row for row in rows}
    require(len(index) == len(rows), "Duplicate historical or grid key")
    return index


def compare_legacy(current, historical, dtype):
    if not current or not historical:
        return "V3_ONLY" if current else ("V2_ONLY" if historical else "BOTH_EMPTY")
    if current == historical:
        return "EXACT_TEXT_MATCH"
    if dtype in {"decimal", "integer"}:
        try:
            if Decimal(current) == Decimal(historical):
                return "FORMATTING_ONLY"
        except InvalidOperation:
            pass
    return "DIFFERENT_DISPLAY_VALUE"


def scoped_known_sources(manifest):
    links = defaultdict(lambda: defaultdict(set))
    for edition in manifest["editions"]:
        if edition["temporal_decision"]["status"] != "ELIGIBLE":
            continue
        sources = links[edition["sha256"]]
        for project in edition["project_ids"]:
            sources[project].update(url for url in (edition["url"], edition["final_url"]) if public_source_url(url))
    return links


def prior_work(workspace, verification):
    recovered, recovery_hash = pinned_document(RECOVERY)
    receipt_hash = recovered["exact_expanded_candidate_reruns"]["receipt_sha256"]
    binding = json.loads(checked(workspace / BINDING / "table_binding_receipt.json", receipt_hash))
    outputs = {name: checked(workspace / BINDING / name, sha) for name, sha in binding["outputs"].items()}
    inherited_hashes = verification["artifact_sha256"]
    queue_path, base_path = f"{GAPS}/census_gap_queue.jsonl", f"{GAPS}/stage3_baseline.json"
    base = json.loads(checked(workspace / base_path, inherited_hashes[base_path]))
    gap = index_csv(checked(workspace / GAPS / "gap_funnel.csv", base["outputs_sha256"]["gap_funnel.csv"]))
    prior_queue = read_jsonl(checked(workspace / queue_path, inherited_hashes[queue_path]))
    dispositions = {row["case_id"]: row for row in read_jsonl(outputs["prior_binding_dispositions.jsonl"])}
    require(len(dispositions) == 162 and binding["inputs"]["source_manifest.json"] == SOURCE_SHA,
            "Inherited route case total or manifest differs")
    open_by_cell, edition_gap_cases = defaultdict(list), []
    open_counts = Counter()
    for queue in prior_queue:
        if queue["case_kind"] == "HISTORICAL_EDITION_GAP":
            edition_gap_cases.append(queue)
            continue
        if queue["case_kind"] == "ORIGINAL_TABLE":
            records = [p for p in queue["positions"] if dispositions[p["case_id"]]["status"] == "EVIDENCE_INSUFFICIENT"]
            open_counts["unbound_sightings"] += len(records)
            if not records:
                continue
            locators = sorted({row["locator"] for row in records})
        else:
            key = queue["source_sha256"], queue["project_id"]
            matching = [row for row in dispositions.values() if (row["source_sha256"], row["project_id"]) == key
                        and row["source_position_id"] is None]
            require(len(matching) == 1, "Missing structurally unrecognized disposition")
            if matching[0]["status"] != "NO_TABLE_STRUCTURE":
                continue
            open_counts["unrecognized_structure"] += 1
            locators = [f"PDF page {page}" for page in queue["located_pages_without_structure"]]
        for hint in queue["same_edition_v2_hints"]:
            if hint["column"] in queue["columns"]:
                open_by_cell[queue["project_id"], hint["column"]].append({
                    "prior_queue_id": queue["queue_id"], "source_sha256": queue["source_sha256"],
                    "case_kind": queue["case_kind"], "locators": locators,
                })
    require(open_counts == {"unbound_sightings": 142, "unrecognized_structure": 15},
            "Stage 3 open case grain or dispositions differ")
    return gap, open_by_cell, edition_gap_cases, dict(open_counts), {
        RECOVERY: recovery_hash, f"{BINDING}/table_binding_receipt.json": receipt_hash,
        queue_path: inherited_hashes[queue_path], base_path: inherited_hashes[base_path],
    }


def display_state(cell, quotes, exact_known):
    """Keep quote availability separate from policy-verified display."""
    if cell["value_state"] == "NOT_APPLICABLE":
        require(evidence(cell).get("applicability") == "AFFIRMATIVELY_INAPPLICABLE",
                "Missing value is not affirmative inapplicability")
        return "AFFIRMATIVE_NOT_APPLICABLE"
    if cell["value_state"] == "PRESENT":
        return cell["evidence_state"] if cell["evidence_state"] in VERIFIED else "PRESENT_REGISTRY_OR_REFERENCE"
    require(cell["value_state"] == "UNKNOWN", "Unknown Census cell state")
    if quotes:
        return "SOURCE_QUOTE_ONLY"
    if not exact_known:
        return "SOURCE_MISSING"
    return "UNKNOWN"  # A separate field records whether this attribute has been indexed.


def source_quotes(archive, policy):
    """Collect already located evidence for an unselected field without assigning a value."""
    receipt = json.loads((archive / "census/census_receipt.json").read_bytes())
    quotes = defaultdict(list)
    for case in receipt["attribute_assessments"]["cases"]:
        if case.get("selection") or not case.get("evidence"):
            continue
        group = ATTRIBUTE_GROUPS[case["attribute_kind"]]
        for column, definition in policy.items():
            if definition["display_group"] == group:
                quotes[case["project_id"], column].append({
                    "source_sha256": case["source_sha256"], "case_id": case["case_id"], "reason": case["reason"],
                    "locators": [a["locator"] for a in case["evidence"] if a["quote"]],
                })
    return quotes


def route_task(cell, definition, historic_source, prior, fixed, quotes, open_cases):
    sha = historic_source or None
    field = cell["column_id"]
    original_signals = json.loads(prior["field_evidence"])
    conflict = any(item["status"] == "SCIENTIFIC_CONFLICT" for item in original_signals)
    conflict |= prior["gap_class"] == "CHEMICAL_OR_MATERIAL_CONFLICT" or cell["evidence_state"] == "SCIENTIFIC_CONFLICT"
    if conflict:
        return "SCIENTIFICALLY_INCOMPATIBLE", {r["source_sha256"] for r in original_signals if r["source_sha256"]}, (
            "RESOLVE_ORIGINAL_CHEMISTRY_MATERIAL_OR_STATEMENT_CONTEXT")
    if field in AUTHORITY_COLUMNS:
        return "OFFICIAL_STRUCTURED_SOURCE", set(), (
            "CONFIRM_OFFICIAL_PROJECT_POINT_WITH_NATIVE_ID_ROLE_CRS_TIME_AND_PRECISION"
            if field in {"latitude", "longitude"} else "VERIFY_DATED_ISSUER_OR_EXCHANGE_LISTING_STATUS")
    original_hints = {row["source_sha256"] for row in quotes + open_cases} | set(json.loads(prior["field_hinted_exact_sources"]))
    viable = (original_hints | {sha}) & fixed.keys()
    if viable:
        return "FIXED_ORIGINAL_REREAD", viable, "VERIFY_EXACT_FIELD_SCOPE_DATE_AND_APPROVED_CENSUS_USE"
    if sha and sha not in fixed:
        return "EXACT_EDITION_REQUIRED", {sha}, "RECOVER_EXACT_HISTORICAL_EDITION_OR_SEPARATELY_CAPTURE_RELEVANT_ORIGINAL"
    if fixed and definition["display_group"] in {"geology", "development", "production", "resource"}:
        return "FIXED_ORIGINAL_REREAD", set(fixed), "INDEX_PROJECT_LINKED_ORIGINALS_FOR_THIS_ATTRIBUTE"
    return "OFFICIAL_STRUCTURED_SOURCE", set(), "LOCATE_PURPOSE_SPECIFIC_OFFICIAL_OR_ISSUER_EVIDENCE"


def group_task(queue, *, key, row, task, exact_sources, existing_reach):
    route, targets, action = task
    supplemental = row["historical_source_sha256"] or None
    for sha in sorted(targets or {None}):
        identity = route, row["project_id"], row["assertion_family"], sha
        if identity not in queue:
            queue[identity] = {
                "route": route, "status": "BLOCKED" if route in {"EXACT_EDITION_REQUIRED", "SCIENTIFICALLY_INCOMPATIBLE"}
                else ("READY" if route == "FIXED_ORIGINAL_REREAD" else "NOT_RUN"),
                "project_id": row["project_id"], "assertion_family": row["assertion_family"], "source_sha256": sha,
                "source_urls": sorted(exact_sources.get(sha, [])),
                "adds_new_project_reach_if_valid": not existing_reach and row["potentially_meaningful"],
                "candidate_only": True, "formal_admission": False,
                "fields": [], "source_quote_cases": [], "historical_source_sha256": set(), "historical_source_urls": set(),
                "next_verification": set(), "retained_stage3_queue_ids": set(),
            }
        item = queue[identity]
        field = {"column": key[1], "column_id": row["column_id"], "data_role": row["data_role"], "data_type": row["data_type"],
                 "prior_census_reason": row["census_reason"], "required_evidence": row["required_evidence"]}
        item["fields"].append(field)
        item["source_quote_cases"].extend(case for case in row["source_quote_cases"] if case["source_sha256"] == sha)
        if supplemental:
            item["historical_source_sha256"].add(supplemental)
            if row["historical_source_url"]:
                item["historical_source_urls"].add(row["historical_source_url"])
        item["next_verification"].add(action)
        item["retained_stage3_queue_ids"].update(row["stage3_queue_ids"])


def scoped_projects(build, state=VERIFIED):
    return {row["project_id"] for row in build.census.values() if row["evidence_state"] in state}


def primary_assertion_ids(cell):
    if cell["evidence_state"] == "VALIDATED_DESCRIPTIVE":
        return [evidence(cell)["assertion"]["assertion_id"]]
    if cell["evidence_state"] == "FORMAL_DERIVED":
        return json.loads(cell["input_observation_ids"])
    return [cell["observation_id"]] if cell["observation_id"] else []


def stage4_grid(build, historical, gap, open_by_cell, policy, manifest, quotes):
    require(historical.keys() == build.census.keys() == gap.keys(), "V2, V3 or prior gap grid axes differ")
    require(len(build.census) == 4743 and sum(bool(c["value"]) for c in historical.values()) == 2500,
            "Fixed V2 display totals differ")
    sources = scoped_known_sources(manifest)
    by_project = defaultdict(dict)
    for sha, projects in sources.items():
        for project, urls in projects.items():
            by_project[project][sha] = urls
    reach, queue, cells = scoped_projects(build), {}, []
    for key, cell in build.census.items():
        project, column = key
        definition, hint, prior = policy[column], historical[key], gap[key]
        exact = by_project[project]
        quote = [row for row in quotes.get(key, []) if row["source_sha256"] in exact and row["locators"]]
        kind = display_state(cell, quote if cell["value_state"] == "UNKNOWN" else [],
                             bool(exact) or bool(cell["source_sha256"]))
        group = definition["display_group"]
        field = cell["column_id"]
        # One typed deposit/stage/point assertion can support more than one projection.
        assertion = field if group in FORMULA_INPUTS or group == "production" else GROUPS.get(group, field)
        historical_sha = hint["source_sha256"] if hint["value"] else ""
        open_cases = open_by_cell.get(key, [])
        original_index = bool(quote or open_cases or cell["source_sha256"] or historical_sha in exact
                              or json.loads(prior["field_hinted_exact_sources"]))
        row = {
            "project_id": project, "column_id": field, "column": column, "data_role": cell["data_role"],
            "data_type": definition["data_type"], "assertion_family": assertion, "value_state": cell["value_state"],
            "census_reason": cell["reason"], "evidence_state": cell["evidence_state"], "coverage_state": kind,
            "source_index_state": "FIELD_INDEXED" if original_index else (
                "PROJECT_SOURCE_ONLY" if exact else "SOURCE_MISSING"),
            "current_source_sha256": cell["source_sha256"],
            "historical_source_sha256": historical_sha,
            "historical_source_url": public_source_url(hint["source_url"]) or "",
            "potentially_meaningful": group not in {"source", "report"} and field != "has_treo_resource",
            "historic_exact_sha_available_for_project": bool(historical_sha and historical_sha in exact),
            "historical_value_has_source_sha": bool(historical_sha),
            "historical_comparison": compare_legacy(cell["value"], hint["value"], definition["data_type"]),
            "v3_display_value_sha256": fingerprint(cell["value"].encode()) if cell["value"] else "",
            "v2_display_value_sha256": fingerprint(hint["value"].encode()) if hint["value"] else "",
            "required_evidence": definition["required_evidence"],
            "stage3_queue_ids": sorted({item["prior_queue_id"] for item in open_cases}),
            "source_quote_cases": quote,
            "stage3_disposition_affects_field": bool(open_cases),
            "work_route": "",
        }
        if cell["value_state"] == "UNKNOWN":
            task = route_task(cell, definition, historical_sha, prior, exact, quote, open_cases)
            row["work_route"] = task[0]
            group_task(queue, key=key, row=row, task=task, exact_sources=exact, existing_reach=project in reach)
        cells.append(row)
    items = []
    for identity, row in queue.items():
        for key in ("historical_source_sha256", "historical_source_urls", "next_verification", "retained_stage3_queue_ids"):
            row[key] = sorted(row[key])
        quote_cases = {item["case_id"]: item for item in row["source_quote_cases"]}
        row["source_quote_cases"] = [quote_cases[i] for i in sorted(quote_cases)]
        row["fields"].sort(key=lambda item: item["column_id"])
        row["queue_id"] = fingerprint(canonical_json(identity).encode())
        row["distinct_assertion_target_upper_bound"] = 1
        items.append(row)
    sorted_items = sorted(items, key=lambda i: (ROUTES[i["route"]], not i["adds_new_project_reach_if_valid"],
                          not bool(i["source_quote_cases"] or i["retained_stage3_queue_ids"]),
                          -len(i["fields"]), i["project_id"], i["assertion_family"], i["source_sha256"] or ""))
    return cells, sorted_items


def report_groups(build, cells):
    by_column, by_project = defaultdict(list), defaultdict(list)
    for row in cells:
        by_column[row["column_id"]].append(row)
        by_project[row["project_id"]].append(row)
    fields, projects = [], []
    for column_id, rows in by_column.items():
        status = Counter(row["coverage_state"] for row in rows)
        verified = [row for row in rows if row["coverage_state"] in VERIFIED]
        fields.append({
            "column_id": column_id, "column": rows[0]["column"], "data_role": rows[0]["data_role"],
            "data_type": rows[0]["data_type"], "denominator": len(rows),
            "present": sum(row["value_state"] == "PRESENT" for row in rows),
            "verified_display": len(verified), "source_quote_only": status["SOURCE_QUOTE_ONLY"],
            "unknown": sum(row["value_state"] == "UNKNOWN" for row in rows),
            "affirmative_not_applicable": status["AFFIRMATIVE_NOT_APPLICABLE"],
            "unknown_with_no_fixed_project_source": status["SOURCE_MISSING"],
            "unknown_without_field_index": sum(row["value_state"] == "UNKNOWN" and
                    row["source_index_state"] == "PROJECT_SOURCE_ONLY" for row in rows),
            "missing_referenced_edition": sum(row["work_route"] == "EXACT_EDITION_REQUIRED" for row in rows),
            "verified_state_counts": dict(status),
            "verified_project_ids": sorted({r["project_id"] for r in verified}),
            "verified_source_sha256": sorted({r["current_source_sha256"] for r in verified if r["current_source_sha256"]}),
            "verified_representatives": [{"project_id": r["project_id"], "source_sha256": r["current_source_sha256"]}
                                         for r in verified[:3]],
            "historical_comparison_counts": dict(Counter(row["historical_comparison"] for row in rows)),
        })
    for project, rows in by_project.items():
        verified = [build.census[project, row["column"]] for row in rows if row["coverage_state"] in VERIFIED]
        projects.append({
            "project_id": project,
            "denominator": len(rows), "present": sum(row["value_state"] == "PRESENT" for row in rows),
            "meaningful_reach": "YES" if verified else "NO", "verified_display": len(verified),
            "state_counts": dict(Counter(row["coverage_state"] for row in rows)),
            "verified_fields": [row["column_id"] for row in verified],
            "distinct_source_sha256": sorted({row["source_sha256"] for row in verified if row["source_sha256"]}),
            "original_observation_ids": sorted({identity for row in verified
                if row["evidence_state"] in {"FORMAL_DIRECT", "FORMAL_DERIVED"} for identity in primary_assertion_ids(row)}),
            "attribute_assertion_ids": sorted({identity for row in verified
                if row["evidence_state"] == "VALIDATED_DESCRIPTIVE" for identity in primary_assertion_ids(row)}),
        })
    return fields, projects


def estimated_review_scope(queue):
    """Compute an upper bound on planned review targets, not on verified future results."""
    by_source = defaultdict(set)
    by_project = defaultdict(set)
    potential_new_projects = defaultdict(set)
    for row in queue:
        if row["route"] == "FIXED_ORIGINAL_REREAD" and row["source_sha256"]:
            for dtype in {item["data_type"] for item in row["fields"]}:
                by_source[row["source_sha256"], dtype].add((row["project_id"], row["assertion_family"]))
                by_project[row["source_sha256"], dtype].add(row["project_id"])
                if row["adds_new_project_reach_if_valid"]:
                    potential_new_projects[row["source_sha256"], dtype].add(row["project_id"])
    return [{"source_sha256": sha, "data_type": dtype, "distinct_project_upper_bound": len(by_project[sha, dtype]),
             "distinct_new_project_upper_bound": len(potential_new_projects[sha, dtype]),
             "distinct_assertion_target_upper_bound": len(targets)}
            for (sha, dtype), targets in sorted(by_source.items())]


def run(workspace, v2_root, current, replays, out):
    verification, verification_sha = pinned_document(VERIFICATION)
    build, manifest, baseline = verify_candidate(workspace, current, replays, verification)
    policy = load_projection_policy(build.census_columns)
    require(policy.sha256 == build.receipt["census_projection_policy_sha256"], "Census policy differs")
    historical = index_csv(check_bytes(git_blob(v2_root, V2_REF, V2_PROVENANCE), V2_PROVENANCE_SHA, V2_PROVENANCE))
    gap, opened, historic_edition_gaps, outstanding, receipts = prior_work(workspace, verification)
    archive = workspace / FINAL / "build"
    cells, queue = stage4_grid(build, historical, gap, opened, policy.by_name, manifest, source_quotes(archive, policy.by_name))
    fields, projects = report_groups(build, cells)
    verified = Counter(row["coverage_state"] for row in cells)
    kpis = {
        "core_51_denominator": len(cells),
        "core_51_present": sum(row["value_state"] == "PRESENT" for row in cells),
        "core_51_verified_display": sum(verified[status] for status in VERIFIED),
        "meaningful_project_reach": sum(row["meaningful_reach"] == "YES" for row in projects),
        "meaningful_project_denominator": len(projects),
        "verified_display_by_evidence_state": {status: verified[status] for status in sorted(VERIFIED)},
        "original_observations_supporting_verified_display": len({i for row in projects for i in row["original_observation_ids"]}),
        "independent_verified_attribute_assertions": len({i for row in projects for i in row["attribute_assertion_ids"]}),
        "independent_governed_derivations": verified["FORMAL_DERIVED"],
        "distinct_verified_display_originals": len({i for row in projects for i in row["distinct_source_sha256"]}),
        "new_original_observations": 0, "new_verified_attribute_assertions": 0, "new_core_51_present": 0,
        "extension_fields": {"status": "NOT_RUN", "count": 0},
    }
    require((kpis["core_51_present"], kpis["core_51_verified_display"], kpis["meaningful_project_reach"]) == (1122, 30, 9)
            and (verified["FORMAL_DIRECT"], verified["FORMAL_DERIVED"], verified["VALIDATED_DESCRIPTIVE"]) ==
            (16, 3, 11), "Verified Census baseline counts differ")
    require(build.receipt["observation_counts"] == {"candidate_count": 42, "validated_count": 27, "selected_count": 21},
            "Accepted numeric observation counts differ")
    comparability = Counter(code for criteria in build.decision_inputs["criteria_by_project"].values()
                            for code, row in criteria.items() if row["state"] == "COMPARABLE")
    require(comparability == {"R1": 4, "S1": 2}, "Comparable criterion keys differ")
    out.mkdir(parents=True, exist_ok=False)
    datasets = {"core51_coverage_by_role.csv": fields, "project_display_reach.csv": projects,
                "core51_v2_v3_cell_comparison.csv": cells,
                "source_evidence_review_bounds.csv": estimated_review_scope(queue)}
    for name, rows in datasets.items():
        write_csv(out / name, rows)
    queue_name = "source_attribute_work_queue.jsonl"
    (out / queue_name).write_text("".join(canonical_json(row) + "\n" for row in queue), encoding="utf-8")
    result = {
        "schema": "census-display-coverage-baseline-v1", "C4-0": "PASS", "C4-1": "PASS", **baseline,
        "cutoff": manifest["cutoff"], "timezone": manifest["timezone"], "kpis": kpis,
        "other_present_excludes": ["administrative_registry", "source_inventory", "dependent_resource_presence_flags"],
        "comparison_keys": dict(comparability), "complete_r1_s1_projects":
            build.decision_results["panels"]["issuer_resource_composition"]["eligible_count"],
        "observation_counts": build.receipt["observation_counts"],
        "coverage_states": dict(verified),
        "source_index_states": dict(Counter(row["source_index_state"] for row in cells)),
        "historical_comparison_counts": dict(Counter(row["historical_comparison"] for row in cells)),
        "v2_present": 2500, "v2_display_cells_with_no_source_sha256": sum(
            bool(row["value"]) and not row["source_sha256"] for row in historical.values()),
        "v2": {"commit": V2_REF, "field_provenance": V2_PROVENANCE, "sha256": V2_PROVENANCE_SHA,
               "use": "DISPLAY_COMPARISON_AND_RETRIEVAL_ONLY"},
        "queue_routes": dict(Counter(row["route"] for row in queue)),
        "queue_statuses": dict(Counter(row["status"] for row in queue)),
        "unresolved_stage3_native": outstanding,
        "historical_edition_gaps": [{"source_sha256": row["source_sha256"], "project_id": row["project_id"],
                                     "disposition": row["reason"]} for row in historic_edition_gaps],
        "source_evidence_review_bounds": "Source/Project/assertion-family review targets; projections and historical hints are not new findings",
        "prior_receipt_sha256": {VERIFICATION: verification_sha, **receipts},
        "outputs_sha256": {name: fingerprint((out / name).read_bytes()) for name in (*datasets, queue_name)},
        "new_public_acquisition": "NOT_RUN", "formal_precision_recall": "UNMEASURED",
    }
    (out / "coverage_baseline_receipt.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "v2-root", "current-main", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--replays", nargs=2, type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.workspace, args.v2_root, args.current_main, args.replays, args.out)
    print(canonical_json({"C4-0": receipt["C4-0"], "C4-1": receipt["C4-1"], "kpis": receipt["kpis"]}))


if __name__ == "__main__":
    main()
