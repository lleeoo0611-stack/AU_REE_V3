"""Build a read-only Census coverage, disclosure and original-edition inventory."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from au_ree_v3.census_metadata import load_projection_policy, public_source_url
from au_ree_v3.contracts import canonical_json
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.registry import CATALOG_SCHEMA, load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.sources import ContentStore
from tools.census_criterion_evidence import match_quote
from tools.census_display_coverage import compare_legacy
from tools.census_gap_inventory import FIXED_SOURCES, SOURCE_SHA, checked, fingerprint, git_blob, read_jsonl, require, write_csv
from tools.census_v2_coverage_diagnostic import KEY, SHA, V2_DIR, V2_FILES, V2_REF, frozen, grid, ledger
from tools.verify_census_stage3_release import check_store, verify_closure, verify_replay

ROOT = Path(__file__).resolve().parents[1]
MAIN = "aa92a35c3d5b3593c25b246edcd28ea36c4b0d9a"
CODE = "a7efe276563ff18b8b15c7a8d0d660ec8f0d8445"
CANDIDATE = "8369299733848299022aaf976f51df0d5551be550410f4f5263824cab3e4fdc3"
V2_CODE = "0dff6beb5aca52e565c37289e3ab295737e76942"
VERIFICATION = "docs/research/census_comparability_verification.json"
OLD_VERSION_AUDIT = "docs/research/census_display_coverage_verification.json"
OLD_VERSION_RECEIPT = "work/stage4-context/edition-recovery-final/source_edition_recovery.json"
EXPANDED_PROFILE = "work/census-stage6/additional-sources/expanded-profile/profile.json"
CURRENT = "work/census-stage6/verified"
REVIEW = "docs/research/census_disclosure_search_review_v1.json"
VERIFIED_DISPLAY = {"FORMAL_DIRECT", "FORMAL_DERIVED", "VALIDATED_DESCRIPTIVE"}
SCIENCE_GROUPS = {"resource", "oxide", "aggregate"}
RESOURCES = {"ore_mt", "treo_grade_pct", "contained_treo_kt"}
RAW_FIELD = {"ore_mt": "ore_mt", "grade": "treo_grade_pct", "contained_treo_kt": "contained_treo_kt"}
GROUP_FIELD = {"magnet_recalculated_kt": "recalculated_magnet_oxide_kt", "complete_oxide_sum_kt": "reported_oxide_sum_kt",
               "mreo_kt": "magnet_oxide_kt", "ndpr_kt": "ndpr_kt", "hree8_kt": "hree8_kt"}
NEVER_ATTEMPTED = {"NOT_SEARCHED", "NO_SOURCE_SEARCH"}
ORDER = {
    "CONFLICT": 0, "EXTRACTED_FAILED_VALIDATION": 1, "DISCLOSED_NOT_EXTRACTED": 2,
    "PARTIAL_DISCLOSURE": 3, "SEARCHED_NOT_DISCLOSED": 4, "SOURCE_NOT_ACQUIRED": 5, "NOT_SEARCHED": 6,
}
PROJECT_HEADER = ("project_id", "project_name", "project_source_status", "scoped_capture_count", "attempt_statuses")


def trusted_json(name):
    payload = git_blob(ROOT, MAIN, name)
    return json.loads(payload), fingerprint(payload)


def verify_baseline(workspace, replays, comparison):
    prior, hash_prior = trusted_json(VERIFICATION)
    archive_dir = workspace / CURRENT
    release = json.loads(checked(archive_dir / "archive/releases" / f"{CANDIDATE}.json", CANDIDATE))
    build_dir = archive_dir / "build"
    build = ResearchBuild.load(build_dir)
    require(prior["candidate_id"] == CANDIDATE and prior["code_commit"] == release["code_commit"] ==
            build.receipt["code_commit"] == CODE and release["build_receipt_sha256"] == build.receipt_sha,
            "Frozen stage 6 code, candidate or receipt differs")
    require(release["output_hashes"] == build.receipt["scientific_artifacts"] == prior["science_output_sha256"]
            and len(release["output_hashes"]) == 8, "Stage 6 scientific outputs differ")
    require(len(replays) == len({run.resolve() for run in replays}) == 2, "Two independent cold output directories required")
    for cas_name, replay in zip(("cas", "replica-cas"), replays, strict=True):
        verify_closure(archive_dir / cas_name, {CANDIDATE, *release["required_object_sha256"]})
        verify_replay(replay, CANDIDATE, release["output_hashes"])
        for name, pin in release["output_hashes"].items():
            checked(replay / "rebuilt" / name, pin)
    compare = json.loads((comparison / "comparison_receipt.json").read_bytes())
    for name, pin in compare["outputs_sha256"].items():
        checked(comparison / name, pin)
    require(compare["status"] == "PASS" and compare["census_change_counts"] == {"UNCHANGED": 4743},
            "Full core grid does not match the new offline baseline")
    compare_builds = json.loads((comparison / "release_and_replay.json").read_bytes())
    require(all(compare_builds[key]["build_receipt_sha256"] == build.receipt_sha for key in ("before", "after")),
            "Grid comparison is for other build receipts")
    source_pin = prior["artifacts"]["source_manifest_expansion"]
    sources = json.loads(checked(workspace / source_pin["path"], source_pin["sha256"]))
    fixed = json.loads(checked(workspace / FIXED_SOURCES, SOURCE_SHA))
    require(release["input_artifacts"] == build.receipt["input_artifacts"] == prior["source_profile_pins"]
            and release["input_artifacts"]["sources"] == source_pin["sha256"], "Research inputs differ")
    checked(workspace / EXPANDED_PROFILE, release["input_artifacts"]["profile"])
    profile = load_profile(workspace / EXPANDED_PROFILE)
    registry = load_registry(profile.registry_path)
    cas = ContentStore(archive_dir / "cas")
    lock = {}
    for name, manifest, expected in (("fixed", fixed, (144, 148)), ("candidate", sources, (147, 152))):
        proof = verify_source_manifest(profile, manifest, registry, cas)
        original = set(proof["eligible_sha256"])
        captures = set(proof["verified_capture_sha256"])
        require(proof["status"] == "PASS" and original == set(proof["verified_sha256"]) and
                (len(original), len(captures)) == expected and proof["projects"] == 93, f"Source scope differs: {name}")
        lock[name] = original, captures
    require(all(base <= extended for base, extended in zip(lock["fixed"], lock["candidate"], strict=True)),
            "Expanded research lost frozen source objects or capture events")
    display_pin = prior["artifacts"]["display_package_receipt"]
    display = json.loads(checked(workspace / display_pin["path"], display_pin["sha256"]))
    require(display["release_id"] == CANDIDATE and (len(display["coverage"]),
                sum(row["present"] for row in display["coverage"].values())) == (9, 45), "Stage 6 display receipt differs")
    return build, build_dir, registry, sources, cas, {
        "main_commit": MAIN, "candidate_code_commit": CODE, "candidate_release_id": CANDIDATE,
        "baseline_verification_sha256": hash_prior, "candidate_science_sha256": release["output_hashes"],
        "inputs_sha256": release["input_artifacts"], "fixed_sources_sha256": SOURCE_SHA,
        "fixed_sources": 144, "fixed_captures": 148, "candidate_sources": 147, "candidate_captures": 152,
        "cold_science": {"status": "PASS", "runs": 2, "outputs_each": 8, "core_grid_unchanged": 4743},
        "sqlite": check_store(build_dir), "extension": {"fields": 9, "known": 45, "total": 837},
        "F": {**build.receipt["gates"], "F4": "PASS", "F5": release["replica_inventory"]["status"]},
        "historical_O2": prior["gates"]["historical_O2"],
    }


def frozen_v2(root, build):
    blobs = {name: frozen(root, f"{V2_DIR}/{name}") for name in V2_FILES}
    archive = json.loads(blobs["census_release.json"])
    columns, projects, values = grid(blobs["REE project census.csv"], KEY)
    statuses = ledger(blobs["field_status.csv"], values.keys())
    provenance = ledger(blobs["field_provenance.csv"], values.keys())
    claims = git_blob(root, V2_CODE, "data/claims.json")
    require(archive["code"]["commit"] == V2_CODE and archive["census_csv_sha256"] ==
            fingerprint(blobs["REE project census.csv"]) and archive["claims_sha256"] == fingerprint(claims)
            and tuple(columns) == build.census_columns and set(projects) == set(build.census_projects)
            and values.keys() == build.census.keys(), "V2 science execution, claims, output or exact axes differ")
    require(all(row["value"] == values[key] for key, row in provenance.items()) and
            sum(bool(value) for value in values.values()) == 2500, "V2 field values differ from preserved Census")
    return values, statuses, provenance, {
        "v2_output_ref": V2_REF, "v2_science_code": V2_CODE,
        "v2_research_cutoff": archive["cutoff"], "v2_research_run": archive["run_id"],
        "v2_inputs_sha256": {**{name: fingerprint(data) for name, data in blobs.items()},
                             "data/claims.json": fingerprint(claims)},
    }


def source_indexes(manifest):
    scoped, aliases, events = defaultdict(set), defaultdict(set), defaultdict(list)
    for row in manifest["editions"]:
        if row["temporal_decision"]["status"] == "ELIGIBLE":
            for project in row["project_ids"]:
                scoped[project].add(row["sha256"])
                aliases[row["sha256"]].add(project)
    for row in manifest["events"]:
        for project in row["project_ids"]:
            events[project, row["requested_url"]].append({
                "url": row["requested_url"], "acquired_at": row["acquired_at"], "status": row["status"],
                "http_status": row["http_status"], "reason": row["reason"], "observed_sha256": row.get("sha256"),
                "capture_sha256": row.get("receipt_sha256"), "scope": "STAGE6_CAPTURE_MANIFEST",
            })
    return scoped, aliases, events


def provenance_capture_index(manifest, catalog):
    """Join verified editions to their public capture and exact Project catalog route."""
    require(catalog["schema_version"] == CATALOG_SCHEMA, "Unexpected source catalog schema")
    channels = defaultdict(set)
    for seed in catalog["seed_urls"]:
        for project in seed["project_ids"]:
            channels[project, seed["url"]].add(seed["channel"])
    events = {event["receipt_sha256"]: event for event in manifest["events"] if event["status"] == "PUBLIC_CAPTURED"}
    scopes, captures = defaultdict(dict), {}
    for edition in manifest["editions"]:
        if edition["temporal_decision"]["status"] != "ELIGIBLE":
            continue
        receipt = edition["receipt_sha256"]
        event = events.get(receipt)
        sha = edition["sha256"]
        require(event is not None and event["sha256"] == sha and event["kind"] == edition["kind"] and event["method"]
                and set(event["project_ids"]) == set(edition["project_ids"]), "Capture does not bind the source edition")
        url = event["requested_url"]
        captures[receipt] = {"acquisition_method": event["method"], "kind": event["kind"], "requested_url": url}
        for project in edition["project_ids"]:
            scopes[project, sha][receipt] = sorted(channels[project, url] or {"NOT_IN_PINNED_CATALOG"})
    return scopes, captures


def historical_provenance(historical, manifest, catalog):
    """Count archived cells once and preserve many-to-many exact edition/capture evidence."""
    scopes, captures = provenance_capture_index(manifest, catalog)
    available_sha = {sha for _, sha in scopes}
    scope_methods = {scope: {captures[receipt]["acquisition_method"] for receipt in receipts}
                     for scope, receipts in scopes.items()}
    method_scopes = defaultdict(set)
    method_states, method_acquisitions, group_methods = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    hints = Counter()
    for (project, _), row in historical.items():
        if not row["value"]:
            continue
        method = row["method"] or "UNRECORDED_METHOD"
        keys = {(project, sha) for sha in SHA.findall(row["source_sha256"])}
        matching = keys & scopes.keys()
        state = ("NO_ARCHIVED_EDITION_SHA" if not keys else "EXACT_EDITION_NOT_LINKED_TO_PROJECT" if not matching
                 else "ALL_EXACT_EDITIONS" if matching == keys else "PARTIAL_EXACT_EDITIONS")
        method_states[method][state] += 1
        method_scopes[method].update(keys)
        method_acquisitions[method].update({acquisition for scope in matching for acquisition in scope_methods[scope]})
        for key in keys:
            group_methods[key][method] += 1
        hints.update(name for name in ("source_url", "locator", "pdf_page") if row[name])
        hints.update(name for name in ("observation_ids", "attribute_ids") if json.loads(row[name]))
        if keys:
            hints["source_sha256"] += 1
    routes = []
    used_capture_ids = set()
    for (project, sha), methods in sorted(group_methods.items()):
        route_captures = scopes.get((project, sha), {})
        used_capture_ids.update(route_captures)
        status = ("EXACT_ELIGIBLE" if route_captures else "EXACT_ELIGIBLE_OTHER_PROJECT" if sha in available_sha
                  else "NO_EXACT_ELIGIBLE_EDITION")
        routes.append({"project_id": project, "source_sha256": sha, "edition_state": status,
                       "historical_cells_by_method": dict(methods), "catalog_channels_by_capture_sha256": route_captures})
    by_capture = {receipt: captures[receipt] for receipt in sorted(used_capture_ids)}
    return {
        "schema": "census-historical-provenance-v1",
        "populated_cells": sum(sum(states.values()) for states in method_states.values()),
        "cells_with_historical_hints": dict(hints),
        "distinct_historical_sha256": len({sha for _, sha in group_methods}),
        "project_sha_groups_by_edition_state": dict(Counter(route["edition_state"] for route in routes)),
        "capture_counts_by_acquisition_method": dict(Counter(row["acquisition_method"] for row in by_capture.values())),
        "capture_counts_by_source_kind": dict(Counter(row["kind"] for row in by_capture.values())),
        "project_sha_counts_by_catalog_channel": dict(Counter(
            channel for route in routes for channel in {
                name for names in route["catalog_channels_by_capture_sha256"].values() for name in names
            }
        )),
        "historical_methods": {
            method: {"populated_cells": sum(states.values()), "edition_alignment": dict(states),
                     "populated_cells_by_exact_acquisition_method": dict(method_acquisitions[method]),
                     "project_sha_groups": len(method_scopes[method])}
            for method, states in method_states.items()
        },
        "project_sha_groups": routes,
        "eligible_captures": by_capture,
    }


def provenance_report(workspace, v2_root, replays, comparison):
    build, _, _, manifest, _, baseline = verify_baseline(workspace, replays, comparison)
    _, _, historical, historical_pins = frozen_v2(v2_root, build)
    profile_path = workspace / EXPANDED_PROFILE
    pinned_profile = json.loads(checked(profile_path, baseline["inputs_sha256"]["profile"]))
    profile = load_profile(profile_path)
    catalog = checked(profile.source_catalog_path, pinned_profile["inputs"]["source_seed_catalog"]["sha256"])
    result = historical_provenance(historical, manifest, json.loads(catalog))
    return {**result, "status": "PASS", "candidate_release_id": baseline["candidate_release_id"],
            "source_manifest_sha256": baseline["inputs_sha256"]["sources"], "source_catalog_sha256": fingerprint(catalog),
            "historical_census_sha256": historical_pins["v2_inputs_sha256"]["REE project census.csv"],
            "historical_provenance_sha256": historical_pins["v2_inputs_sha256"]["field_provenance.csv"]}


def archived_attempts(workspace):
    verification, _ = trusted_json(OLD_VERSION_AUDIT)
    pin = verification["other_verification_artifact_sha256"][OLD_VERSION_RECEIPT]
    receipt = json.loads(checked(workspace / OLD_VERSION_RECEIPT, pin))
    result = {}
    for row in receipt["cases"]:
        result[row["project_id"], row["expected_sha256"]] = [{
            "url": item["url"], "acquired_at": item["acquired_at"], "status": item["acquisition"],
            "http_status": item.get("http_status"), "reason": item.get("reason"), "observed_sha256": item.get("candidate_edition_sha256"),
            "capture_sha256": item.get("capture_sha256"), "scope": "STAGE4_RETRY_EXPECTED_EXACT_EDITION",
        } for item in row["attempts"]]
    return result, pin


def editions_for_cells(historical, current, policy, scoped, aliases, events, prior_attempts):
    lookup, missing = {}, {}
    for key, proof in historical.items():
        if not proof["value"]:
            continue
        project, column = key
        expected = list(dict.fromkeys(SHA.findall(proof["source_sha256"])))
        attached = [sha for sha in expected if sha in scoped[project]]
        absent = [sha for sha in expected if sha not in scoped[project]]
        lookup[key] = {
            "matching": attached,
            "missing": absent,
            "coverage": "NO_ARCHIVED_EDITION_SHA" if not expected else (
                "EXACT_EDITION_NOT_LINKED_TO_PROJECT" if not attached else "PARTIAL_EXACT_EDITIONS" if absent else "ALL_EXACT_EDITIONS"),
        }
        urls = [url for u in proof["source_url"].split(";") if (url := public_source_url(u))]
        if len(expected) == len(urls) == 1:
            located_urls = dict(zip(expected, urls, strict=True))
        else:
            located_urls = {}
        field = policy[column]
        for sha in absent:
            identity = project, sha
            if identity not in missing:
                missing[identity] = {
                    "project_id": project, "expected_source_sha256": sha, "edition_status":
                        "EXACT_SHA_NOT_ELIGIBLY_LINKED_TO_PROJECT" if aliases[sha] else "NO_EXACT_SHA_IN_CANDIDATE_MANIFEST",
                    "eligible_other_project_ids": sorted(aliases[sha]), "expected_urls": set(), "unbound_url_hints": set(),
                    "archived_fields": [], "current_unknown_fields": [], "direct_resource_targets": [],
                    "science_targets": [], "historical_field_links": []}
            item = missing[identity]
            url = located_urls.get(sha)
            if url:
                item["expected_urls"].add(url)
            else:
                item["unbound_url_hints"].update(urls)
            item["archived_fields"].append(field["column_id"])
            item["historical_field_links"].append({
                "field_id": field["column_id"], "locator_hint": proof["locator"],
                "effective_date_hint": proof["effective_date"], "status": current[key]["value_state"],
            })
            if current[key]["value_state"] != "UNKNOWN":
                continue
            item["current_unknown_fields"].append(field["column_id"])
            if field["display_group"] in SCIENCE_GROUPS:
                item["science_targets"].append(field["column_id"])
            if field["column_id"] in RESOURCES:
                item["direct_resource_targets"].append(field["column_id"])
    for identity, item in missing.items():
        project, sha = identity
        attempts = [*prior_attempts.get(identity, ()),
                    *(event for url in item["expected_urls"] for event in events[project, url])]
        item["attempts"] = sorted({canonical_json(row): row for row in attempts}.values(),
                                  key=lambda row: (row["acquired_at"] or "", row["url"]))
        item["observed_different_sha256"] = sorted({row["observed_sha256"] for row in attempts
                                                    if row["observed_sha256"] and row["observed_sha256"] != sha})
        item["expected_urls"] = sorted(item["expected_urls"])
        item["unbound_url_hints"] = sorted(item["unbound_url_hints"])
        item["affected_legacy_cells"] = len(item["archived_fields"])
        item["affected_current_unknown_cells"] = len(item["current_unknown_fields"])
    return lookup, missing


def normalize_notes(rows):
    return sorted({canonical_json(row): row for row in rows}.values(),
                  key=lambda item: (ORDER[item["reason"]], item["subreason"], item.get("source_sha256") or ""))


def field_checks(build_dir, definitions):
    evidence = defaultdict(list)
    observations = read_jsonl((build_dir / "observations/observations.jsonl").read_bytes())
    for row in observations:
        outcome, variable = row["outcome"], row["variable"]
        field = RAW_FIELD.get(variable) or (variable.removesuffix("_ppm") + "_kt" if variable.endswith("_ppm") else None)
        if field not in definitions or outcome == "VALIDATED":
            continue
        evidence[row["project_id"], field].append({
            "reason": "CONFLICT" if outcome == "SCIENTIFIC_CONFLICT" else "EXTRACTED_FAILED_VALIDATION",
            "subreason": row["reason"], "source_sha256": row["source_sha256"],
            "observation_id": row["observation_id"], "statement_id": row["statement_id"], "scope_id": row["scope_id"],
            "review_scope": {"page": row["page"], "table": row["table"], "row": row["row"],
                             "variable": variable, "decision_scope": "FORMAL_OBSERVATION_OR_DERIVED_INPUT"},
        })
    census = json.loads((build_dir / "census/census_receipt.json").read_bytes())
    for case in census["attribute_assessments"]["cases"]:
        if case.get("selection") or not case.get("evidence"):
            continue
        group = {"DEPOSIT_TYPE": "geology", "DEVELOPMENT_STAGE": "development"}.get(case.get("attribute_kind"))
        for field, definition in definitions.items():
            if definition["display_group"] != group:
                continue
            evidence[case["project_id"], field].append({
                "reason": "EXTRACTED_FAILED_VALIDATION", "subreason": case["reason"], "source_sha256": case["source_sha256"],
                "review_scope": {"case_id": case["case_id"], "purpose": "SOURCE_BOUND_TEXT_ATTRIBUTE",
                                 "locators": [row["locator"] for row in case["evidence"]]},
            })
    views = census["science_views"]
    for item in views["rejected_derivations"]:
        key = item["key"]
        field = GROUP_FIELD.get(key) or (key.removesuffix("_ppm") + "_kt" if key.endswith("_ppm") else None)
        if field in definitions and item["status"] == "SCIENTIFIC_CONFLICT":
            evidence[item["project_id"], field].append({
                "reason": "CONFLICT", "subreason": item["reason"], "source_sha256": None,
                "review_scope": {"purpose": "CENSUS_DERIVED_VALUE", "input_observation_ids": item["inputs"],
                                 "source_precision_reviews": item["source_precision_reviews"]},
            })
    return evidence, census


def reviewed_originals(path, build, source_sha, scoped, cas, definitions, existing):
    data = path.read_bytes()
    review = json.loads(data)
    require(review["schema"] == "census-disclosure-review-v1" and review["source_manifest_sha256"] == source_sha
            and review["cutoff"] == build.receipt["research_cutoff"], "Review receipt does not bind the baseline")
    cache = DocumentCache()
    for row in review["cases"]:
        project, sha = row["project_id"], row["source_sha256"]
        require(sha in scoped[project], "Disclosure review requires the exact Project edition")
        require(row["disclosure_reason"] in {"DISCLOSED_NOT_EXTRACTED", "SEARCHED_NOT_DISCLOSED", "PARTIAL_DISCLOSURE"}
                and row["review_scope"], "Manual review requires a bounded original search scope")
        pages = row["physical_pages_reviewed"]
        require(pages and set(pages) == {w["physical_page"] for w in row["witnesses"]}, "Unverified stated search scope")
        document = cache.pdf(cas.read(sha), sha, last_page=max(pages))
        require(all(match_quote(document, w["physical_page"], w["quote"]) for w in row["witnesses"]),
                "Manual original disclosure witness not located")
        for field in row["field_ids"]:
            require(field in definitions, "Unknown reviewed field ID")
            existing[project, field].append({
                "reason": row["disclosure_reason"], "subreason": row["subreason"], "source_sha256": sha,
                "review_scope": {"case_id": row["case_id"], "pages": pages, "review": row["review_scope"]},
            })
    return fingerprint(data)


def primary_disclosure(cell, notes):
    """An unknown search history cannot make a statement about publication or applicability."""
    if cell["value_state"] == "PRESENT":
        return "PRESENT"
    if cell["value_state"] == "NOT_APPLICABLE":
        meta = json.loads(cell["metadata_evidence"] or "{}")
        require(meta.get("applicability") == "AFFIRMATIVELY_INAPPLICABLE", "Missing affirmative applicability proof")
        return "NOT_APPLICABLE"
    require(cell["value_state"] == "UNKNOWN", "Unknown Census value state")
    if notes:
        return min((row["reason"] for row in notes), key=ORDER.__getitem__)
    if cell["source_search_status"] in NEVER_ATTEMPTED:
        return "NOT_SEARCHED"
    return "SEARCH_HISTORY_UNKNOWN"


def full_grid(build, policy, values, statuses, source_link, missing, evidence, scoped):
    for key, cell in build.census.items():
        project, heading = key
        definition = policy[heading]
        field = cell["column_id"]
        links = source_link.get(key, {"coverage": "NO_ARCHIVED_POPULATED_CELL", "matching": [], "missing": []})
        notes = list(evidence[project, field])
        for sha in links["missing"]:
            record = missing[project, sha]
            if links["matching"] or not any(
                event["status"] != "PUBLIC_CAPTURED" or event["observed_sha256"] not in (None, sha)
                for event in record["attempts"]
            ):
                continue
            notes.append({
                "reason": "SOURCE_NOT_ACQUIRED", "subreason": "HISTORICAL_FIELD_LINK_EXACT_EDITION_UNRECOVERED",
                "source_sha256": sha, "review_scope": {
                    "audited_event_statuses": sorted({row["status"] for row in record["attempts"]}),
                    "observed_different_sha256": record["observed_different_sha256"],
                    "scope": "V2_FIELD_EDITION_RETRIEVAL_ONLY",
                },
            })
        notes = normalize_notes(notes)
        status = primary_disclosure(cell, notes)
        yield {
            "project_id": project, "field_id": field, "column": heading, "data_role": cell["data_role"],
            "value_state": cell["value_state"], "value": cell["value"], "evidence_state": cell["evidence_state"],
            "value_origin": cell["value_origin"], "baseline_reason": cell["reason"], "disclosure_reason": status,
            "disclosure_notes": notes, "project_source_search_status": cell["source_search_status"],
            "field_search_history": "FIELD_CHECK_RECEIPT" if notes else (
                "NOT_SEARCHED" if status == "NOT_SEARCHED" else "UNKNOWN"),
            "eligible_project_source_editions": len(scoped[project]), "source_sha256": cell["source_sha256"],
            "source_url": cell["source_url"], "observation_id": cell["observation_id"], "anchor_id": cell["anchor_id"],
            "lineage_id": cell["lineage_id"], "scope_id": cell["scope_id"], "temporal_status": cell["temporal_status"],
            "selection_id": cell["selection_id"], "formal_mcda_comparable": cell["comparable"],
            "historical_v2_status": statuses[key]["status"],
            "historical_value_sha256": fingerprint(values[key].encode()) if values[key] else "",
            "historical_display_comparison": compare_legacy(cell["value"], values[key], definition["data_type"]),
            "historical_source_alignment": links["coverage"], "historical_project_exact_sha256": links["matching"],
            "historical_missing_sha256": links["missing"],
        }


def distributions(rows):
    """Expand counts while preserving the scope of each disclosure check."""
    status = defaultdict(int)
    for row in rows:
        for scope, project, field in (("TOTAL", "", ""), ("PROJECT", row["project_id"], ""),
                                      ("FIELD", "", row["field_id"]), ("CELL", row["project_id"], row["field_id"])):
            key = scope, project, field, row["value_state"], row["evidence_state"], row["disclosure_reason"]
            status[key] += 1
    return [{
        "scope": scope, "project_id": project, "field_id": field, "value_state": state, "evidence_state": evidence,
        "disclosure_reason": reason, "count": count,
    } for (scope, project, field, state, evidence, reason), count in sorted(status.items())]


def no_project_sources(manifest, registry, scoped):
    projects = {row.project_id: row.name for row in registry.projects}
    return [{
        "project_id": row["project_id"], "project_name": projects[row["project_id"]],
        "project_source_status": row["status"], "scoped_capture_count": row["attempt_count"],
        "attempt_statuses": row["attempt_statuses"],
    } for row in manifest["projects"] if not scoped[row["project_id"]]]


def old_applicability(rows, census):
    documented = census["science_views"]["verified_resource_presence"]
    related_resources = census["science_views"]["scoped_reported_resources"]
    for row in rows:
        if not row["historical_v2_status"].startswith("NOT_APPLICABLE"):
            continue
        project = row["project_id"]
        yield {
            "project_id": project, "field_id": row["field_id"], "column": row["column"],
            "v2_status": row["historical_v2_status"], "v3_value_state": row["value_state"],
            "v3_reason": row["baseline_reason"], "v3_disclosure_reason": row["disclosure_reason"],
            "affirmative_inapplicability_in_baseline": row["value_state"] == "NOT_APPLICABLE",
            "separate_verified_resource_on_project": project in documented,
            "separate_scoped_resource_on_project": project in related_resources,
            "checked_project_source_editions": row["eligible_project_source_editions"],
            "effect_on_formal_value": "NONE",
        }


def run(workspace, v2_root, replays, comparison, out, review_path=ROOT / REVIEW):
    build, folder, registry, source_manifest, cas, result = verify_baseline(workspace, replays, comparison)
    values, statuses, provenance, v2 = frozen_v2(v2_root, build)
    policy = load_projection_policy(build.census_columns).by_name
    definitions = {item["column_id"]: item for item in policy.values()}
    scoped, aliases, events = source_indexes(source_manifest)
    prior_attempts, attempt_pin = archived_attempts(workspace)
    link, missing = editions_for_cells(provenance, build.census, policy, scoped, aliases, events, prior_attempts)
    evidence, census = field_checks(folder, definitions)
    review_sha = reviewed_originals(review_path, build, result["inputs_sha256"]["sources"], scoped, cas, definitions, evidence)
    rows = list(full_grid(build, policy, values, statuses, link, missing, evidence, scoped))
    unavailable_projects = no_project_sources(source_manifest, registry, scoped)
    historic = list(old_applicability(rows, census))
    work = sorted(missing.values(), key=lambda row: (not bool(row["direct_resource_targets"]),
                  -len(row["direct_resource_targets"]), -len(row["science_targets"]), -row["affected_current_unknown_cells"],
                  row["project_id"], row["expected_source_sha256"]))
    states = dict(Counter(row["value_state"] for row in rows))
    require(len(rows) == 4743 and states == {"PRESENT": 1141, "UNKNOWN": 3602}
            and len(historic) == 1715 and sum(row["v2_status"] == "NOT_APPLICABLE_NO_MRE" for row in historic) == 1650,
            "Frozen field or historical applicability denominator differs")
    out.mkdir(parents=True, exist_ok=False)
    datasets = {
        "census_gap_inventory.csv": rows, "missing_sources.csv": work,
        "v2_not_applicable_cells.csv": historic, "census_disclosure_distribution.csv": distributions(rows),
    }
    for filename, data in datasets.items():
        write_csv(out / filename, data)
    projects_name = "projects_without_sources.csv"
    if unavailable_projects:
        write_csv(out / projects_name, unavailable_projects)
    else:
        (out / projects_name).write_text(",".join(PROJECT_HEADER) + "\n", encoding="utf-8")
    result.update({
        "schema": "census-disclosure-inventory-v1", "status": "PASS", "T0": "PASS", "T1": "PASS",
        "research_cutoff": source_manifest["cutoff"], "timezone": source_manifest["timezone"],
        "projects": len(build.census_projects), "columns": len(build.census_columns), "cells": len(rows),
        "value_states": {**states, "NOT_APPLICABLE": states.get("NOT_APPLICABLE", 0)},
        "verified_display": sum(row["evidence_state"] in VERIFIED_DISPLAY for row in rows),
        "source_relationships": {
            "projects_without_eligible_source": len(unavailable_projects), "missing_project_sha_pairs": len(work),
            "distinct_expected_sha_missing_project_link": len({row["expected_source_sha256"] for row in work}),
            "projects_with_missing_expected_edition": len({row["project_id"] for row in work}),
            "affected_historical_populated_cells": sum(bool(item["missing"]) for item in link.values()),
            "affected_current_unknown_with_v2_value": sum(
                row["value_state"] == "UNKNOWN" and bool(row["historical_missing_sha256"]) for row in rows),
            "old_sha_relationship_by_cell": dict(Counter(item["coverage"] for item in link.values())),
            "current_manifest_acquisition_events": dict(Counter(row["status"] for row in source_manifest["events"])),
        },
        "historical_v2_display_comparison": dict(Counter(row["historical_display_comparison"] for row in rows)),
        "historical_v2_not_applicable_audit": {
            "old_reason_counts": dict(Counter(row["v2_status"] for row in historic)),
            "current_states": dict(Counter(row["v3_value_state"] for row in historic)),
            "unknown_with_separate_scoped_resource_on_project": sum(
                row["v3_value_state"] == "UNKNOWN" and row["separate_scoped_resource_on_project"] for row in historic),
        },
        "independent_current_unknown_reasons": dict(Counter(
            row["disclosure_reason"] for row in rows if row["value_state"] == "UNKNOWN")),
        "review_sha256": review_sha, "historical_attempt_receipt_sha256": attempt_pin,
        "outputs_sha256": {name: fingerprint((out / name).read_bytes()) for name in (*datasets, projects_name)},
        "scientific_values_added": 0, "current_value_states_reclassified": 0, **v2,
    })
    (out / "census_fill_inventory_receipt.json").write_text(canonical_json(result) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("workspace", "v2-root", "comparison"):
        parser.add_argument(f"--{flag}", required=True, type=Path)
    parser.add_argument("--replays", required=True, nargs=2, type=Path)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--out", type=Path, help="Write the existing disclosure inventory files")
    output.add_argument("--provenance", action="store_true", help="Print the historical source provenance report to stdout")
    args = parser.parse_args()
    if args.provenance:
        print(canonical_json(provenance_report(args.workspace, args.v2_root, args.replays, args.comparison)))
        return
    result = run(args.workspace, args.v2_root, args.replays, args.comparison, args.out)
    print(canonical_json({"status": result["status"], "cells": result["cells"], "T0": result["T0"], "T1": result["T1"]}))


if __name__ == "__main__":
    main()
