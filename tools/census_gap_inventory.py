"""Audit the fixed Census and prioritize exact-original evidence work offline."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from hashlib import sha256
from io import StringIO
import json
import re
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from au_ree_v3.census_metadata import load_projection_policy, public_source_url
from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.publication import load_release
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild, compare_research_builds
from au_ree_v3.source_case_inventory import V2_REF
from au_ree_v3.sources import ContentStore

MAIN = "2fc644dc3fadca64cf9cb75db921bd728ea4160a"
RELEASE = "b0f1389f395c7eff4a239c0214c0bb518f2ba504f84c71f3be948404debec1da"
SOURCE_SHA = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"
ROOT = Path(__file__).resolve().parents[1]
PROFILE = "config/research_profiles/au_ree_2026-11-28.json"
ATTRIBUTE_PROFILE = "config/research_profiles/au_ree_census_attributes_v1.json"
PREVIOUS = "work/census-stage2/typed-attributes-22f8565"
FIXED_SOURCES = "work/research-yield/source-inputs/source_manifest.json"
CHILDREN = {
    "inventory": ("work/scientific-admission/inventory-verified-a", "source_case_inventory_receipt.json"),
    "binding": ("work/census-stage2/binding-benchmark/candidate-1", "table_binding_receipt.json"),
}
V2_PROVENANCE = "results/ree-project-census/coverage-locked-20261125/field_provenance.csv"
V2_PROVENANCE_SHA = "f0a963ef1479f53546c614205c251be0c365adc748ce446fd659c4d57d033c6d"
VARIABLE_TO_COLUMN = {"ore_mt": "ore_mt", "grade": "treo_grade_pct", "contained_treo_kt": "contained_treo_kt"}
KIND_TO_COLUMN = {"ORE_MASS": "ore_mt", "TREO_GRADE": "treo_grade_pct", "CONTAINED_TREO_MASS": "contained_treo_kt"}
NATIVE_HEADING_IDS = ("ore_mt", "treo_grade_pct", "contained_treo_kt")
REVIEW_STATUS = {"SOURCE_NOT_INDEXED": "INDEX_SOURCE_FOR_FIELD",
                 "ROW_STRUCTURE_MISSING": "LOCATE_EXACT_ORIGINAL_ROW",
                 "HEADER_OR_UNIT_UNBOUND": "BIND_EXACT_ROW_HEADER_UNIT_AND_QUANTITY",
                 "STATEMENT_SCOPE_UNBOUND": "VERIFY_PROJECT_DEPOSIT_STATEMENT_SCOPE",
                 "TEMPORAL_EVIDENCE_MISSING": "VERIFY_EXACT_EDITION_TIME_OR_EVENT_DATE",
                 "CHEMICAL_OR_MATERIAL_CONFLICT": "RESOLVE_ORIGINAL_CONTEXT_CONFLICT",
                 "VALIDATED_BUT_NO_APPROVED_USE": "VERIFY_APPROVED_USE_WITHOUT_PROMOTION",
                 "SOURCE_EDITION_MISSING": "RECOVER_EXACT_EDITION_AND_VERIFY_TEMPORAL_GATE"}


def fingerprint(content):
    return sha256(content).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def checked(path, expected):
    return check_bytes(path.read_bytes(), expected, str(path))


def check_bytes(content, expected, description):
    require(fingerprint(content) == expected, f"Fingerprint mismatch: {description}")
    return content


def git_blob(repo, commit, path):
    return subprocess.run(
        ["git", "--no-lazy-fetch", "-C", str(repo), "cat-file", "blob", f"{commit}:{path}"],
        check=True, capture_output=True,
    ).stdout


def locked_git_json(path):
    actual = (ROOT / path).read_bytes()
    authority = git_blob(ROOT, MAIN, path)
    require(actual == authority, f"Prior verification receipt differs from pinned main: {path}")
    return json.loads(actual), fingerprint(actual)


def read_jsonl(content):
    return [json.loads(line) for line in content.splitlines()]


def previous_inputs(workspace):
    authorization, inventory_sha = locked_git_json("docs/research/scientific_value_admission_receipt.json")
    refactor, binding_sha = locked_git_json("docs/research/native_extraction_refactor_receipt.json")
    prior, prior_sha = locked_git_json("docs/research/census_stage2_verification.json")
    inventory_dir, inventory_name = CHILDREN["inventory"]
    binding_dir, binding_name = CHILDREN["binding"]
    inventory_pin, = [row["sha256"] for row in authorization["artifacts"]
                      if row["path"] == f"{inventory_dir}/{inventory_name}"]
    binding_pin, = {sample["receipt_sha256"] for artifact in refactor["artifacts"]
                    if artifact["path"].endswith("/binding-benchmark/engineering_benchmark.json")
                    for sample in artifact["measurements"]["measurements"]["candidate"]}
    directories = ((inventory_dir, inventory_name, inventory_pin, "output_sha256"),
                   (binding_dir, binding_name, binding_pin, "outputs"))
    payloads = {}
    receipts = {}
    for directory, name, pin, field in directories:
        receipt = json.loads(checked(workspace / directory / name, pin))
        receipts[name] = receipt
        payloads.update({output: checked(workspace / directory / output, sha)
                         for output, sha in receipt[field].items()})
    binding = receipts[binding_name]
    require(binding["inputs"] == {"source_manifest.json": SOURCE_SHA,
                                  inventory_name: inventory_pin,
                                  "source_case_inventory.jsonl": fingerprint(payloads["source_case_inventory.jsonl"])},
            "Stage 2 binding does not pin the fixed source inventory and manifest")
    require((binding["distinct_native_source_positions"], binding["distinct_resolved_native_positions"]) ==
            (298, 167), "Stage 2 native positions do not match the baseline")
    support = {
        "docs/research/scientific_value_admission_receipt.json": inventory_sha,
        "docs/research/native_extraction_refactor_receipt.json": binding_sha,
        "docs/research/census_stage2_verification.json": prior_sha,
        f"{inventory_dir}/{inventory_name}": inventory_pin, f"{binding_dir}/{binding_name}": binding_pin,
    }
    for name in ("native_value_recovery_verification", "census_stage2_baseline_receipt",
                 "census_project_attribute_verification", "census_treo_derivations_receipt",
                 "forecast_and_geographic_context_receipt", "census_v2_rule_inventory"):
        path = f"docs/research/{name}.json"
        data, source_pin = locked_git_json(path)
        if name == "census_v2_rule_inventory":
            require(len(data) == 100, "V2 rule inventory is incomplete")
        support[path] = source_pin
    return prior, support, payloads, binding


def verify_baseline(workspace, main_dir, cold, prior):
    require(len(cold) == 2 and len({p.resolve() for p in cold}) == 2,
            "Require two distinct cold replay directories")
    historical = prior["replay_and_web"]["integrated_candidate"]
    previous = workspace / PREVIOUS
    store = ContentStore(previous / "cas")
    manifest = load_release(previous / "archive", RELEASE, store)
    before, after = ResearchBuild.load(previous / "build"), ResearchBuild.load(main_dir)
    require(after.receipt["code_commit"] == MAIN and before.receipt["code_commit"] ==
            manifest["code_commit"] == historical["code_commit"] and
            before.receipt_sha == manifest["build_receipt_sha256"] and
            before.receipt["scientific_artifacts"] == manifest["output_hashes"] == historical["output_fingerprints"],
            "Baseline does not match the pinned candidate and main")
    for run in cold:
        receipt = json.loads((run / "replay_receipt.json").read_bytes())
        require(receipt["release_id"] == RELEASE and receipt["offline"] is True and receipt["F4"] == "PASS" and
                not receipt["differences"] and not receipt["build_receipt_difference_fields"] and
                receipt["expected"] == receipt["actual"] == manifest["output_hashes"],
                "Historical candidate cold replay failed")
        for name, sha in manifest["output_hashes"].items():
            checked(run / "rebuilt" / name, sha)
    base_profile, profile = [load_profile(ROOT / config) for config in (PROFILE, ATTRIBUTE_PROFILE)]
    current_fingerprints = after.receipt["input_artifacts"]
    require(current_fingerprints == before.receipt["input_artifacts"] and
            current_fingerprints["profile"] == fingerprint((ROOT / ATTRIBUTE_PROFILE).read_bytes()) ==
            historical["profile_sha256"] and current_fingerprints["sources"] == SOURCE_SHA and
            after.receipt["environment"] == before.receipt["environment"], "Profile, environment or source lock differs")
    source_manifest = json.loads(checked(workspace / FIXED_SOURCES, SOURCE_SHA))
    registry = load_registry(profile.registry_path)
    verified = verify_source_manifest(base_profile, source_manifest, registry, ContentStore(workspace / "work/fresh/source-store"))
    require(verified["status"] == "PASS" and len(verified["verified_sha256"]) ==
            len(verified["eligible_sha256"]) == 144 and len(verified["verified_capture_sha256"]) ==
            148 and verified["projects"] == 93, "Original SHA, capture or project cohort verification failed")
    with TemporaryDirectory(prefix="census-gap-science-") as temporary:
        comparison = compare_research_builds(previous / "build", main_dir, Path(temporary))
        changes = json.loads((Path(temporary) / "comparability_delta.json").read_bytes())["changes"]
    require(comparison["status"] == "PASS" and set(comparison["census_change_counts"]) <=
            {"UNCHANGED", "PROVENANCE_RECEIPT_CHANGED"} and before.observations == after.observations
            and comparison["net_comparable_project_criteria"] == 0, "Verified baseline science was not preserved")
    require(before.receipt["scientific_artifacts"]["observations/observations.jsonl"] ==
            after.receipt["scientific_artifacts"]["observations/observations.jsonl"] and
            before.receipt["scientific_artifacts"]["census/census.csv"] ==
            after.receipt["scientific_artifacts"]["census/census.csv"], "Exact observation or Census data differs")
    for item in changes:
        left, right = item["before"], item["after"]
        for criterion in (left, right):
            criterion["lineage"] = [{key: value for key, value in evidence.items()
                                     if key not in {"release_id", "selection_id"}} for evidence in criterion["lineage"]]
        require(left == right and item["changed_fields"] == ["lineage"],
                "Comparable decision criteria changed outside release lineage")
    require(before.decision_inputs["policy"] == after.decision_inputs["policy"] and
            before.decision_inputs["panels"] == after.decision_inputs["panels"] and
            before.decision_results["panels"] == after.decision_results["panels"],
            "Decision policy, weights, cohort, score or sensitivity differs")
    for key, cell in before.census.items():
        current = after.census[key]
        preserved = ("value", "source_sha256", "observation_id", "scope_id", "statement_id", "effective_date",
                     "temporal_status", "reason", "value_origin", "evidence_state")
        require(all(cell[field] == current[field] for field in preserved), "Stable Census evidence differs")
    return after, source_manifest, {
        "code_commit": MAIN, "stage2_candidate": RELEASE, "stage2_code_commit": historical["code_commit"],
        "research_cutoff": source_manifest["cutoff"], "timezone": source_manifest["timezone"],
        "study_profile_sha256": fingerprint((ROOT / PROFILE).read_bytes()), "build_inputs": current_fingerprints,
        "source_verification": verified, "scientific_artifacts_sha256": after.receipt["scientific_artifacts"],
        "stage2_scientific_artifacts_sha256": manifest["output_hashes"], "main_build_receipt_sha256": after.receipt_sha,
        "stage2_build_receipt_sha256": before.receipt_sha, "cold_stage2_replay": {"runs": 2, "artifacts_per_run": 8, "status": "PASS"},
        "main_vs_stage2_census": comparison["census_change_counts"],
        "mcda_unchanged": ["policy", "criterion_value_and_scientific_context", "cohort", "weights", "ranks", "sensitivity"],
        "release_only_comparable_lineage_changes": len(changes),
        "mcda_policy_sha256": after.decision_inputs["policy_sha256"], "environment": after.receipt["environment"],
        "F0_to_F5": {**after.receipt["gates"], "F4": "PASS", "F5": manifest["replica_inventory"]["status"]},
        "historical_O2": prior["scientific_delta"]["historical_O2"],
    }


def legacy_hints(v2_root, grid):
    raw = check_bytes(git_blob(v2_root, V2_REF, V2_PROVENANCE), V2_PROVENANCE_SHA, V2_PROVENANCE)
    hints = defaultdict(list)
    for index, row in enumerate(csv.DictReader(StringIO(raw.decode("utf8"), newline="")), 1):
        key = row["project_id"], row["column"]
        if key not in grid or not row["value"] or not row["source_sha256"]:
            continue
        locator = re.search(r"\b(?:physical PDF p\s*\d+|PDF page\s+\d+|Table\s+[A-Z]?\d+[A-Z]?)", row["locator"], re.I)
        hints[key + (row["source_sha256"],)].append({
            "column": row["column"], "locator": locator[0] if locator else None,
            "historical_value_receipt": {"git_commit": V2_REF, "csv_path": V2_PROVENANCE, "row_record": index},
            "prior_method": row["method"], "use": "RETRIEVAL_HINT_ONLY",
        })
    return hints


def recoveries(v2_root, data, manifest, legacy):
    known = {row["sha256"] for row in manifest["editions"]}
    editions = {}
    rows = []
    for row in json.loads(data)["rows"]:
        digest = row["source_sha256"]
        require(digest not in known, "Historical gap was wrongly inserted into the fixed manifest")
        snapshot = row["read_only_v2_snapshot"]
        git_exact = snapshot["status"] == "EXACT_SHA"
        if git_exact:
            body = git_blob(v2_root, V2_REF, snapshot["path"])
            check_bytes(body, digest, snapshot["path"])
            require(len(body) == snapshot["bytes"] and snapshot["git_ref"] == V2_REF,
                    "Pinned historical SHA path or byte count differs")
        for target in row["historical_positions"]:
            key = target["project_id"], target["column"], digest
            require(key in legacy, "Missing edition hint does not match pinned V2 field provenance")
            editions[key] = "V2_EXACT_BYTES_ONLY" if git_exact else "SOURCE_UNAVAILABLE"
        distinct = {item["sha256"] for item in row["distinct_v3_editions"]}
        distinct.update(item["actual_sha256"] for item in row["pinned_public_attempts"] if item["actual_sha256"])
        require(digest not in distinct, "Exact edition was labeled as a distinct SHA")
        rows.append({
            "source_sha256": digest, "project_ids": row["impacted_project_ids"],
            "affected_historical_grid_keys": len(row["historical_positions"]), "in_fixed_manifest": False,
            "availability": "V2_EXACT_BYTES_ONLY" if git_exact else "SOURCE_UNAVAILABLE",
            "v2_git_snapshot": snapshot, "different_editions_sha256": sorted(distinct),
            "pinned_public_attempts": row["pinned_public_attempts"],
            "public_locators": [url for url in row["public_source_locators"] if public_source_url(url)],
            "fresh_public_attempt": "NOT_RUN",
            "next_action": ("VERIFY_PUBLIC_EDITION_AND_CAPTURE_IN_NEW_MANIFEST" if git_exact else "RECOVER_EXACT_EDITION"),
        })
    require(len(rows) == 4 and sum(r["availability"] == "V2_EXACT_BYTES_ONLY" for r in rows) == 2 and
            len(editions) == 35, "Historical missing-edition inventory differs from audited work scope")
    return rows, editions


def field_evidence(main_dir, definitions):
    """Index only links explicitly scoped to the field, not all claims for a project."""
    by_id = {definition["column_id"]: name for name, definition in definitions.items()}
    signals = defaultdict(list)
    observations = read_jsonl((main_dir / "observations/observations.jsonl").read_bytes())
    for row in observations:
        heading = by_id.get(VARIABLE_TO_COLUMN.get(row["variable"]))
        if heading is not None:
            signals[row["project_id"], heading].append({
                "reason": row["reason"], "status": row["outcome"], "selection": row["selection_status"],
                "source_sha256": row["source_sha256"], "id": row["observation_id"],
            })
    receipt = json.loads((main_dir / "census/census_receipt.json").read_bytes())
    for case in receipt["attribute_assessments"]["cases"]:
        group = {"DEPOSIT_TYPE": "geology", "DEVELOPMENT_STAGE": "development"}[case["attribute_kind"]]
        for name, definition in definitions.items():
            if definition["display_group"] == group:
                signals[case["project_id"], name].append({
                    "reason": case["reason"], "status": case["verdict"],
                    "selection": "SELECTED" if case["selection"] else None,
                    "source_sha256": case["source_sha256"], "id": case["case_id"],
                })
    science = receipt["science_views"]
    for row in science["derived_candidates"] + science["rejected_derivations"]:
        heading = row.get("census_column") or by_id.get(row["key"])
        if heading is not None:
            signals[row["project_id"], heading].append({
                "reason": row.get("reason") or row["formal_admission_reason"], "status": row["status"],
                "selection": None, "source_sha256": row.get("context", {}).get("source_sha256"),
                "id": row.get("derivation_id"),
            })
    return signals, observations, receipt


def queue_tables(payloads, manifest, definition, legacy, observations, recoveries):
    cases = {r["case_id"]: r for r in read_jsonl(payloads["source_case_inventory.jsonl"])}
    unresolved = read_jsonl(payloads["unbound_numeric_tokens.jsonl"])
    unknown_structure = [r for r in cases.values() if r["kind"] == "UNRECOGNIZED_SOURCE_PROJECT"]
    issue_counts = Counter(row["reason"] for row in unresolved)
    require(len(unresolved) == 143 and len(unknown_structure) == 19 and issue_counts ==
            {"HEADER_OR_UNIT_UNBOUND": 141, "NATIVE_TOKEN_NOT_A_UNIQUE_NUMERIC_CELL": 2},
            "Unbound or unstructured backlog no longer matches Stage 2")
    eligible = {(e["sha256"], p) for e in manifest["editions"] if e["temporal_decision"]["status"] == "ELIGIBLE"
                for p in e["project_ids"]}
    by_id = {row["column_id"]: name for name, row in definition.items()}
    heading_hints = defaultdict(set)
    historical_by_source = defaultdict(list)
    for (project, _, sha), clues in legacy.items():
        historical_by_source[sha, project].extend(clues)
    validated = defaultdict(set)
    for row in observations:
        if row["outcome"] == "VALIDATED":
            validated[row["source_sha256"], row["project_id"]].add(row["observation_id"])
    for row in read_jsonl(payloads["typed_candidate_observations.jsonl"]):
        column = by_id.get(KIND_TO_COLUMN.get(row["quantity_kind"]))
        if column:
            heading_hints[row["source_sha256"], row["project_id"], row["table"]["locator"]].add(column)
    queue = {}
    labels = defaultdict(set)

    def add(case, table, kind, action):
        sha, project = case["source_sha256"], case["project_id"]
        key = sha, project, table["locator"] if table else None
        require((sha, project) in eligible, "Queue reference has no eligible exact Project edition")
        clues = historical_by_source[sha, project]
        if key not in queue:
            columns = sorted({hint["column"] for hint in clues if definition[hint["column"]]["display_group"]
                              in {"resource", "oxide", "aggregate"}} | heading_hints[key])
            minimum = ([action, "UNIQUE_ORIGINAL_TABLE_CELL"] if table else [action, "QUALIFY_DOCUMENT_CONTENT"])
            queue[key] = {
                "case_kind": kind, "source_sha256": sha, "project_id": project, "table": table,
                "status": "READY", "gap_class": "HEADER_OR_UNIT_UNBOUND" if table else "ROW_STRUCTURE_MISSING",
                "source_urls": case["source_urls"], "source_edition_ids": case["eligible_edition_ids"],
                "columns": columns or [by_id[item] for item in NATIVE_HEADING_IDS],
                "column_evidence": "NEIGHBOR_OR_HISTORICAL_LEAD_ONLY" if columns else "INTENDED_SEARCH_FIELDS_ONLY",
                "positions": [], "located_pages_without_structure": case.get("physical_pages_inspected", []),
                "minimum_evidence_to_check": minimum,
                "scope_and_use_requirements": case["context_requirements"],
                "expected_use": "CENSUS_SCIENCE_CANDIDATE",
                "validated_observation_ids_same_project_source": sorted(validated[sha, project]),
                "same_edition_v2_hints": clues, "candidate_only": True, "formal_admission": False,
            }
        for hint in clues:
            column = hint["column"]
            if definition[column]["display_group"] not in {"resource", "oxide", "aggregate"}:
                continue
            # Same document is useful for discovery. Only a stated physical page
            # can associate a historical field lead with a known table gap.
            if table:
                locator = hint["locator"] or ""
                if not re.fullmatch(r"(?:physical PDF p|PDF page)\s*\d+", locator, re.I):
                    continue
                if int(locator.split()[-1].lstrip("pP")) != case["physical_page"]:
                    continue
            labels[project, column, sha].add(queue[key]["gap_class"])
        return queue[key]

    seen_positions = set()
    for row in unresolved:
        case = cases[row["case_id"]]
        for field in ("source_sha256", "project_id", "source_position_id", "physical_page", "token_columns", "source_token", "table"):
            require(row[field] == case[field], f"Unbound position disagrees with original inventory: {field}")
        target = add(case, case["table"], "ORIGINAL_TABLE", "BIND_EXACT_ROW_HEADER_UNIT_AND_QUANTITY")
        identity = target["source_sha256"], target["project_id"], row["source_position_id"]
        require(identity not in seen_positions, "Duplicate scoped source position in the queue")
        seen_positions.add(identity)
        target["positions"].append({key: row[key] for key in ("case_id", "source_position_id", "source_token", "physical_page",
                                                             "reason", "token_columns")} |
                                   {"locator": case["row"]["locator"], "neighbor_header": case["header"]})
    for case in unknown_structure:
        add(case, None, "UNRECOGNIZED_SOURCE_PROJECT", "LOCATE_EXACT_ORIGINAL_ROW")
    require(sum(row["case_kind"] == "UNRECOGNIZED_SOURCE_PROJECT" for row in queue.values()) == 19,
            "Unrecognized work contains duplicate source-project keys")
    for (project, column, sha), disposition in recoveries.items():
        key = sha, project, None
        if key not in queue:
            queue[key] = {
                "case_kind": "HISTORICAL_EDITION_GAP", "source_sha256": sha, "project_id": project,
                "table": None, "status": "BLOCKED", "reason": disposition, "gap_class": "SOURCE_EDITION_MISSING",
                "positions": [], "columns": [], "column_evidence": "HISTORICAL_EDITION_ONLY",
                "minimum_evidence_to_check": ["EXACT_PUBLIC_EDITION", "NEW_MANIFEST_CAPTURE", "TEMPORAL_ELIGIBILITY"],
                "expected_use": "CENSUS_SCIENCE_CANDIDATE", "same_edition_v2_hints": historical_by_source[sha, project],
                "validated_observation_ids_same_project_source": [], "candidate_only": True, "formal_admission": False,
            }
        queue[key]["columns"].append(column)
    result = sorted(queue.values(), key=lambda row: (row["status"] != "READY",
                    not bool(row["positions"]), -len(row["positions"]), row["source_sha256"], row["project_id"],
                    row["table"]["locator"] if row["table"] else ""))
    for row in result:
        row["columns"] = sorted(set(row["columns"]))
        row["positions"].sort(key=lambda p: (p["locator"], p["token_columns"]["start"], p["case_id"]))
        row["queue_id"] = fingerprint(canonical_json([row["source_sha256"], row["project_id"],
                                                      row["table"]["locator"] if row["table"] else None]).encode())
    return result, labels, {"original_number_positions": 298, "resolved_original_number_positions": 167,
                           "unbound_scoped_sightings": len(unresolved),
                           "unbound_unique_original_positions": len({row["source_position_id"] for row in unresolved}),
                           "unbound_reasons": dict(issue_counts),
                           "unrecognized_source_project_items": len(unknown_structure),
                           "queue_items_by_kind": dict(Counter(row["case_kind"] for row in result)),
                           "native_originals_redecoded_by_queue": 0}


def classify(cell, signals, active, unavailable, indexed):
    state, original = cell["value_state"], cell["reason"]
    if state == "PRESENT":
        return "ALREADY_PRESENT"
    if state == "NOT_APPLICABLE":
        require(original not in {"NO_SELECTED_MRE", "NOT_APPLICABLE_NO_MRE"} and cell["lineage_id"],
                "A positive applicability decision requires its own evidence")
        return "AFFIRMATIVE_NOT_APPLICABLE"
    require(state == "UNKNOWN", "Unexpected Census state")
    reasons = {signal["reason"] for signal in signals}
    status = {signal["status"] for signal in signals}
    evidence = cell["evidence_state"]
    if original == "NOT_REPORTED" and cell["source_sha256"] and cell["anchor_id"]:
        return "NOT_REPORTED"
    if evidence == "SCIENTIFIC_CONFLICT" or reasons.intersection({
        "SAME_SCOPE_COMPANION_OXIDE_PRECISION_DISAGREEMENT", "MATERIAL_BASIS_INCOMPATIBLE",
    }):
        return "CHEMICAL_OR_MATERIAL_CONFLICT"
    if "HISTORICAL_OBSERVATION_DATE_MISSING" in reasons or evidence in {"TEMPORAL_INELIGIBLE", "TEMPORAL_CONFLICT"}:
        return "TEMPORAL_EVIDENCE_MISSING"
    if "SHADOW_VALIDATED" in status or any(row["status"] == "VALIDATED" and
                                           row["selection"] != "SHADOW_OUTSIDE_REPORTING_SCOPE" for row in signals):
        return "VALIDATED_BUT_NO_APPROVED_USE"
    if reasons.intersection({"NARROWER_REPORT_SCOPE_THAN_CENSUS_PROJECT", "TREO_CHEMICAL_DEFINITION_UNBOUND",
                             "CONFLICTING_CONTAINED_TREO_IN_SAME_STATEMENT"}) or any(
        row["selection"] == "SHADOW_OUTSIDE_REPORTING_SCOPE" for row in signals
    ):
        return "STATEMENT_SCOPE_UNBOUND"
    if "HEADER_OR_UNIT_UNBOUND" in indexed:
        return "HEADER_OR_UNIT_UNBOUND"
    if "ROW_STRUCTURE_MISSING" in indexed:
        return "ROW_STRUCTURE_MISSING"
    if unavailable and not active:
        return "SOURCE_EDITION_MISSING"
    if active and original == "NO_SELECTED_MRE":
        return "STATEMENT_SCOPE_UNBOUND"
    return "SOURCE_NOT_INDEXED"


def write_csv(path, rows):
    fields = list(rows[0])
    with path.open("x", newline="", encoding="utf8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows({key: canonical_json(value) if isinstance(value, (dict, list)) else value
                          for key, value in row.items()} for row in rows)


def grid_funnel(build, policy, manifest, legacy, signals, labels, missing):
    source_by_project = defaultdict(set)
    target_sources = defaultdict(set)
    for edition in manifest["editions"]:
        if edition["temporal_decision"]["status"] == "ELIGIBLE":
            for project in edition["project_ids"]:
                source_by_project[project].add(edition["sha256"])
    for project, column, sha in legacy:
        if sha in source_by_project[project]:
            target_sources[project, column].add(sha)
    missing_by_cell = defaultdict(set)
    for project, column, sha in missing:
        missing_by_cell[project, column].add(sha)
    for project in build.census_projects:
        for column in build.census_columns:
            key = project, column
            cell = build.census[key]
            signal = signals.get(key, [])
            active = target_sources[key] | {r["source_sha256"] for r in signal if r["source_sha256"]}
            indexed = set().union(*(labels.get(key + (sha,), set()) for sha in active))
            absent = missing_by_cell[key]
            gap = classify(cell, signal, active, absent, indexed)
            actionable = "NO_OPEN_TASK" if gap not in REVIEW_STATUS else (
                "BLOCKED_EXACT_EDITION" if gap == "SOURCE_EDITION_MISSING" else "REVIEW_FIXED_SOURCES")
            yield {
                "project_id": project, "column": column, "column_id": cell["column_id"],
                "data_role": cell["data_role"], "value_state": cell["value_state"],
                "evidence_state": cell["evidence_state"], "census_reason": cell["reason"],
                "gap_class": gap, "actionability": actionable,
                "next_action": REVIEW_STATUS.get(gap, ""),
                "required_projection_evidence": policy[column]["required_evidence"],
                "known_project_sources": sorted(source_by_project[project]),
                "field_hinted_exact_sources": sorted(active), "missing_historical_exact_sources": sorted(absent),
                "field_evidence": signal, "existing_cell_source_sha256": cell["source_sha256"],
                "existing_lineage_id": cell["lineage_id"],
            }


def grouped_evidence(funnel):
    groups = {key: defaultdict(Counter) for key in ("project_id", "column_id", "data_role")}
    for row in funnel:
        stages = [row["gap_class"], f"VALUE_STATE_{row['value_state']}"]
        if row["field_hinted_exact_sources"]:
            stages.append("HAS_FIELD_HINTED_EXACT_SOURCE")
        if row["missing_historical_exact_sources"]:
            stages.append("HAS_MISSING_HISTORICAL_EXACT_SOURCE")
        if row["existing_cell_source_sha256"]:
            stages.append("HAS_EXISTING_PROJECTED_SOURCE")
        if row["evidence_state"] in {"FORMAL_DIRECT", "FORMAL_DERIVED", "VALIDATED_DESCRIPTIVE"}:
            stages.append(row["evidence_state"])
        for dimension in groups:
            groups[dimension][row[dimension]].update(stages)
    return groups


def run(workspace, v2_root, main, cold, out):
    prior, input_hashes, previous_payloads, binding = previous_inputs(workspace)
    build, manifest, receipt = verify_baseline(workspace, main, cold, prior)
    policy = load_projection_policy(build.census_columns)
    require(policy.sha256 == build.receipt["census_projection_policy_sha256"], "Census policy has changed")
    definitions = policy.by_name
    signals, observations, census_receipt = field_evidence(main, definitions)
    legacy = legacy_hints(v2_root, build.census)
    recover, missing = recoveries(v2_root, previous_payloads["source_recovery_gaps.json"], manifest, legacy)
    queue, labels, routing = queue_tables(previous_payloads, manifest, definitions, legacy, observations, missing)
    require(binding["unbound_candidate_sightings"] == sum(len(row["positions"]) for row in queue),
            "Queue did not account for all prior Stage 2 scoped sightings")
    funnel = list(grid_funnel(build, definitions, manifest, legacy, signals, labels, missing))
    evidence = Counter(row["evidence_state"] for row in funnel)
    comparison = Counter(code for cells in build.decision_inputs["criteria_by_project"].values()
                         for code, row in cells.items() if row["state"] == "COMPARABLE")
    counts = {"projects": len(build.census_projects), "columns": len(build.census_columns),
              "cells": len(funnel), "present_cells": sum(row["value_state"] == "PRESENT" for row in funnel),
              "formal_direct_cells": evidence["FORMAL_DIRECT"], "formal_derived_cells": evidence["FORMAL_DERIVED"],
              "descriptive_cells": evidence["VALIDATED_DESCRIPTIVE"],
              "independent_attributes": census_receipt["attribute_assessments"]["selected_attributes"],
              "comparable_keys": dict(comparison),
              "complete_r1_s1_projects": build.decision_results["panels"]["issuer_resource_composition"]["eligible_count"],
              **build.receipt["observation_counts"]}
    require(counts == {"projects": 93, "columns": 51, "cells": 4743, "present_cells": 1115,
                       "formal_direct_cells": 16, "formal_derived_cells": 2, "descriptive_cells": 5,
                       "independent_attributes": 2, "comparable_keys": {"R1": 4, "S1": 2},
                       "complete_r1_s1_projects": 2, "candidate_count": 42, "validated_count": 27, "selected_count": 21},
            "Fixed Stage 2 cohort, projection or science counts differ")
    out.mkdir(parents=True, exist_ok=False)
    names = ("census_gap_queue.jsonl", "gap_funnel.csv", "source_edition_recovery.csv", "evidence_conversion_funnel.json")
    (out / names[0]).write_text("".join(canonical_json(row) + "\n" for row in queue), encoding="utf8")
    write_csv(out / names[1], funnel)
    write_csv(out / names[2], recover)
    (out / names[3]).write_text(canonical_json(grouped_evidence(funnel)) + "\n", encoding="utf8")
    result = {
        "schema": "census-gap-inventory-v1", "status": "PASS", "S3-0": "PASS", **receipt,
        "counts": counts, "routing": routing, "projection_policy_sha256": policy.sha256,
        "baseline_rules_sha256": build.receipt["selection_rules_hash"],
        "formula_registry_sha256": build.receipt["census_derivation_registry_sha256"],
        "gap_classes": {**dict.fromkeys((*REVIEW_STATUS, "ALREADY_PRESENT", "NOT_REPORTED",
                                        "AFFIRMATIVE_NOT_APPLICABLE"), 0),
                        **Counter(row["gap_class"] for row in funnel)},
        "distinct_fixed_and_historical_queue_items": dict(Counter(row["status"] for row in queue)),
        "prior_receipts_sha256": input_hashes,
        "v2_reference": {"commit": V2_REF, "field_provenance_file": V2_PROVENANCE,
                         "field_provenance_sha256": V2_PROVENANCE_SHA, "use": "RETRIEVAL_HINT_ONLY"},
        "stage2_holdout_scores": prior["source_to_admission_funnel"]["holdouts"],
        "fresh_public_attempts": "NOT_RUN", "new_formal_census_cells": 0,
        "outputs_sha256": {name: fingerprint((out / name).read_bytes()) for name in names},
    }
    (out / "stage3_baseline.json").write_text(canonical_json(result) + "\n", encoding="utf8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "v2-root", "main-build", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--replay", required=True, action="append", type=Path)
    args = parser.parse_args()
    receipt = run(args.workspace, args.v2_root, args.main_build, args.replay, args.out)
    print(canonical_json({"status": receipt["status"], "S3-0": receipt["S3-0"],
                          "outputs": receipt["outputs_sha256"]}))


if __name__ == "__main__":
    main()
