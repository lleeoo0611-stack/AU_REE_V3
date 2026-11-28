"""Read-only inventory of the pinned V2 Census and comparison with one V3 build."""

import argparse
import ast
from collections import Counter, defaultdict
import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import subprocess

V2_REF = "2af6f611e51eb4ac9dda73419d1afeba70d1307e"
V3_BASE = "40309ea006d2eaace3fe4bbf33beccca492261de"
V2_DIR = "results/ree-project-census/coverage-locked-20261125"
V2_FILES = (
    "REE project census.csv", "field_status.csv", "field_provenance.csv", "census_release.json", "coverage.json",
)
V2_METHODS = {
    "src/au_ree_v2/census.py": {
        "summary_resource_fields", "summary_resource_bundle", "summary_scoped_oxide_fields",
        "summary_complete_sum", "summary_reported_oxide_group", "summary_template_fills",
        "summary_stage_fields", "summary_deposit_fields", "summary_missing", "summary_coverage",
    },
    "src/au_ree_v2/spatial.py": {"summary_locations"},
    "src/au_ree_v2/technical.py": {"summary_forecasts"},
}
V2_RULES = "config/summary_rules.json"
KEY = "專案ID"
SHA = re.compile(r"(?<![a-f0-9])[a-f0-9]{64}(?![a-f0-9])")
CLASSIFICATIONS = (
    "MATCHED", "UPDATED_WITH_EVIDENCE", "NO_NEW_V3_SOURCE", "DIFFERENT_SCIENTIFIC_SCOPE",
    "UNVERIFIED_LEGACY_VALUE", "FORMAL_UNAVAILABLE",
)
STRUCTURAL = {
    "EXISTING_CANONICAL_PROJECT_RELATION", "EXISTING_PROJECT_REGISTRY",
    "EXISTING_PROJECT_WITH_EXPLICIT_RESOURCE_SUBSCOPE_LABEL", "AUSTRALIAN_CENSUS_SCOPE", "SOURCE_STATE_CODE_LABEL",
}
DIRECT_RESOURCE = ("礦石蘊藏量_Mt", "TREO品位_pct", "總TREO蘊藏量_kt")
STAGE = ("開發階段", "開發階段代碼", "開發階段類別")
DEPOSIT = ("礦床型態", "礦床型態代碼")
POINT = ("緯度", "經度")
SOURCE = ("有來源文件", "資料來源名稱", "資料來源URL")
GEO_TEXT = ("所在地_中文", "所在地_英文", "國家", "州省地區")
FORECAST = ("預估年產TREO_t_per_year", "預估年產NdPr_t_per_year", "產品/產出")
COMPOSITION_GROUPS = ("磁鐵稀土氧化物蘊藏量_kt", "磁鐵稀土佔TREO_pct", "NdPr蘊藏量_kt_需兩者皆公布",
                      "完整重稀土組合蘊藏量_kt_需8元素皆公布", "四項磁性元素皆公布", "磁性元素重算值_NdPrTbDy_kt",
                      "已公布元素數_共15項", "已公布元素總和_kt", "元素總和/TREO")


def hash_bytes(value):
    return sha256(value).hexdigest()


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"


def frozen(root, path):
    return subprocess.run(["git", "-C", str(root), "cat-file", "blob", f"{V2_REF}:{path}"],
                          check=True, capture_output=True).stdout


def csv_input(content):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig"), newline=""))
    fields, records = tuple(reader.fieldnames or ()), list(reader)
    if not fields or len(set(fields)) != len(fields) or any(None in row or None in row.values() for row in records):
        raise ValueError("Malformed frozen Census CSV")
    return fields, records


def grid(census, key):
    columns, rows = csv_input(census)
    projects = tuple(row[key] for row in rows)
    if len(columns) != 51 or len(projects) != 93 or len(set(projects)) != 93:
        raise ValueError("Expected unique 93 by 51 Census")
    return columns, projects, {(row[key], col): row[col] for row in rows for col in columns}


def ledger(content, keys):
    _, rows = csv_input(content)
    indexed = {(row["project_id"], row["column"]): row for row in rows}
    if indexed.keys() != keys or len(indexed) != len(rows):
        raise ValueError("Census ledgers do not cover all exact 4,743 cell keys")
    return indexed


def is_numeric(value):
    try:
        return Decimal(value).is_finite()
    except InvalidOperation:
        return False


def describe_rule(key, columns, params):
    oxides = tuple(col for col in columns if re.match(r"[A-Z][a-z]?[0-9]*O[0-9]+_氧化", col))
    composition = (*oxides, *COMPOSITION_GROUPS)
    scope = {
        "identity": ((KEY, "專案名稱"), "Registry snapshot and exact Project IDs", "registry.py; Census projection"),
        "metadata": (GEO_TEXT, "Registry status with pinned state-label map and Australian cohort", "registry.py; Census projection"),
        "resource": ((*DIRECT_RESOURCE, "有TREO資源估計", *composition),
                     "Same source edition, statement, scope, date, classification and cutoff", "fresh_census.py; comparability.py"),
        "composition": (composition, "Source rows, original unit, exact material denominator and oxide membership",
                        "composition_extraction.py; normalization.py; comparability.py"),
        "state": (tuple(columns), "A rule-specific positive fact before NOT_APPLICABLE", "fresh_census.py; Census policy"),
        "stage": (STAGE, "Dated issuer proof of actual milestone, matched independently within scope",
                  "development.py shadow only; no formal stage fill"),
        "deposit": (DEPOSIT, "Explicit source geology and attributable Project/Deposit scope", "Census projection unavailable"),
        "template": ((*DEPOSIT, *STAGE), "Scoped mapping attached to the original dated native observation",
                     "Registry mapping for metadata; development.py for shadow milestone classification"),
        "notes": (("總說明",), "Declared support fields, provenance and deterministic terms of use",
                  "Census evidence-bounded metadata summary"),
        "location": (POINT, "Verified mine or deposit geometry, CRS, native ID, role and date",
                     "geography.py shadows candidate or incomplete point evidence"),
        "forecast": (FORECAST, "Documented scenario, measured-versus-forecast role, calibrated process endpoint and time",
                     "semantics.py; no formal Census forecast adapter"),
        "source": (SOURCE, "Exact source SHA, public retrieval evidence and explicitly project-level relationship",
                   "sources.py; Census projection"),
        "report": (tuple(columns), "Immutable ledger, reason codes and deterministic output contract",
                   "fresh_census.py; research_delta.py"),
    }
    selected = None
    if key in {"cohort", "states", "oxides", "magnet", "heavy", "resource_columns", "identity_columns"}:
        selected = (
            "KEEP", "identity" if key=="cohort" else "metadata" if key=="states" else
            "source" if key=="identity_columns" else "resource" if key=="resource_columns" else "composition",
            "Preserve the declared vocabulary or fixed axis. Admission and scientific roles still require V3 evidence.",
        )
    elif key == "output_files":
        selected = ("MERGE", "report", "One V3 CSV/status/release contract replaces presentation filenames.")
    elif key in {"stage_cases", "stage_case_reasons"}:
        selected = ("REMOVE", "stage", "Do not promote named or in-progress studies to completed milestones.")
    elif key in {"blanks.rules", "blanks.excluded_from_adjusted_denominator", "blanks.default_class", "blanks.version"}:
        selected = ("REFACTOR", "state", "Keep historical denominator context separate. No selected MRE remains UNKNOWN absent positive evidence.")
    elif key in {"stage_exit", "cummins_staged_rule", "cummins_checks", "cummins_stages", "cummins_production_pattern",
                  "splinter_header_y", "splinter_unit_y", "circle_header_y", "circle_unit_y", "template_source_sha256",
                  "oxide_exception_identities", "stage_hits.source_classifications"}:
        group = ("forecast" if key.startswith("cummins") else "composition"
                 if key.startswith(("splinter","circle","oxide")) else "stage" if key.startswith("stage") else "deposit")
        selected = ("ARCHIVE", group, "Keep reviewed historical case and source-bound layout only at its frozen edition.")
    elif key.startswith(("ocr.", "stage_scan.", "stage_hits.", "stage_pattern_")) or key in {
        "ocr_settings", "stage_visual_renderer", "stage_visual_keywords", "stage_keyword_rule",
        "stage_image_fraction", "stage_native_min_letters", "stage_hit_settings", "stage_future",
    }:
        selected = ("MERGE", "stage", "Use shared native/OCR candidate views and anchored wording; lexical/OCR hit cannot attest completion.")
    elif key.startswith("template.") or key == "template_fields":
        selected = ("REFACTOR", "template", "Retain reviewed vocabulary and original source term; mapping alone does not establish geology or operating phase.")
    elif key.startswith("deposit"):
        selected = ("REFACTOR", "deposit", "Separate source term, speculative host and formal ontology mapping; do not infer deposit type by resemblance.")
    elif key in {"stage_rules", "stage_categories", "stage_approval_scope", "source_stage_labels", "completed_drilling_stage_reviews"}:
        selected = ("REFACTOR", "stage", "Preserve dated event and source scope; do not imply current operations or make a new ordinal policy.")
    elif key in {"aliases.projects", "alias_forbidden"}:
        selected = ("MERGE", "identity", "Preserve evidenced aliases only; forbid name-based Project or Deposit merges.")
    elif key in {"point_rules.rules", "point_rules.shared_row_names", "tenement_precision_m", "government_datasets",
                 "ga_display_digits", "point_display_digits", "frozen_tenement_scopes", "reviewed_sarig_points"}:
        selected = ("REFACTOR", "location", "Keep datum, precision and representative role; no tenement centroid gives a mine/deposit location.")
    elif key in {"product_labels", "annual_columns", "annual_parameters", "forecast_scales", "model_annual_method", "model_annual_profile"}:
        selected = ("REFACTOR", "forecast", "Keep scenario and output basis separate from operating observations; no study assumption as actual.")
    elif key in {"atomic_weights", "oxide_exception_rules", "linked_ndpr_oxide_grades", "missing_resource_variables"}:
        selected = (
            "MERGE" if key in {"atomic_weights","missing_resource_variables"} else "REFACTOR",
            "resource" if key=="missing_resource_variables" else "composition",
            "Reuse exact Decimal composition methods only after explicit formula, scoped source and species/denominator review.",
        )
    elif key == "notes":
        selected = ("REFACTOR", "notes", "Compose display notes from explicit attributable fields and limitations.")
    if selected is None:
        raise ValueError(f"Unreviewed disposition for {key}")
    disposition, group, why = selected
    cols, evidence, adapter = scope[group]
    if key == "identity_columns":
        cols = tuple(col for col in params[key]["value"] if col in columns)
    if key == "resource_columns":
        cols = tuple(col for col in params[key]["value"] if col in columns)
    risk = "HIGH" if group in {"composition","resource","state","stage","deposit","location","forecast","identity"} else "MEDIUM"
    return disposition, group, cols, evidence, adapter, why, risk


def rule_inventory(v2, cols, counts, statuses, provenance, used_methods):
    payload = frozen(v2, V2_RULES)
    config = json.loads(payload)
    params = config["parameters"]
    if len(params) != 100:
        raise ValueError("Pinned V2 rules require exactly 100 parameters")
    pointers = defaultdict(set)
    methods = {}
    files = (*V2_METHODS, "src/au_ree_v2/census_policy.py", "src/au_ree_v2/census_replay.py",
             "src/au_ree_v2/cli.py", "tools/compare_census_release.py")
    for file in files:
        for node in ast.walk(ast.parse(frozen(v2, file))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in params:
                pointers[node.value].add(f"{file}:{node.lineno}")
            if isinstance(node, ast.FunctionDef) and node.name in V2_METHODS.get(file, ()):
                methods[node.name] = f"{file}:{node.lineno}"
    if any(name not in methods for names in V2_METHODS.values() for name in names):
        raise ValueError("Expected frozen V2 method is absent")
    stage_case_counts = Counter()
    stage_cases = params["stage_cases"]["value"]
    for (project, _), row in provenance.items():
        if row["method"] != "APPROVED_RESEARCHER_STAGE_RULE" or project not in stage_cases:
            continue
        proposal = json.loads(row["scope"])["proposal"]
        if [proposal["code"], proposal["rule"]] == stage_cases[project]:
            stage_case_counts[proposal["rule"]] += 1
    exact_lineage_methods = {
        "cohort": ("EXISTING_CANONICAL_PROJECT_RELATION",),
        "states": ("SOURCE_STATE_CODE_LABEL",),
        "template_fields": ("USER_PROVIDED_TEMPLATE_MAPPING_FROM_礦床型態", "USER_PROVIDED_TEMPLATE_MAPPING_FROM_開發階段"),
        "notes": ("DETERMINISTIC_SOURCE_GROUNDED_PRESENTATION_NOTES_V1",),
        "stage_exit": ("STAGE_EXIT_RESEARCHER_DECISION_RELINQUISHMENT",),
        "source_stage_labels": ("DATED_STUDY_DISCLOSURE_NOT_CURRENT_OPERATING_STATUS",),
        "atomic_weights": ("DERIVED_STOICHIOMETRIC_CE2O3_TO_CEO2",),
        "linked_ndpr_oxide_grades": ("ORE_MT_X_LINKED_RESOURCE_NDPR_OXIDE_PERCENT_X_10",),
        "cummins_staged_rule": ("STAGED_ANNUAL_USE_FIRST_FULL_PRODUCTION_STAGE",),
    }
    results = []
    for name, rule in params.items():
        disposition, group, related, required, adapter, why, risk = describe_rule(name, cols, params)
        method_attribution = {method: used_methods[method] for method in exact_lineage_methods.get(name, ())}
        if name in {"stage_cases", "stage_case_reasons"}:
            method_attribution.update(stage_case_counts)
        attributed_count = sum(method_attribution.values()) if method_attribution else None
        associated_status_count = sum(
            statuses[reason] for reason in ("NOT_APPLICABLE_NO_MRE","NOT_APPLICABLE_STAGE",
                                            "NOT_APPLICABLE_UNLISTED_DEVELOPER")
        ) if name == "blanks.rules" else None
        results.append({
            "rule_key": name, "rule_version": config["version"], "rule_sha256": hash_bytes(json_bytes(rule)),
            "origin_file": rule["source_file"], "origin_key": rule["source_key"], "disposition": disposition,
            "scientific_group": group, "risk": risk, "why": why, "required_evidence": required,
            "v3_equivalent_or_gap": adapter, "related_columns": list(related),
            "legacy_present_cells_in_related_columns": sum(counts[col] for col in related),
            "archived_exact_linkage_count": attributed_count,
            "archived_exact_linkage_by_method_or_case": method_attribution,
            "associated_archived_status_count": associated_status_count,
            "rule_specific_scientific_effect_status": "UNMEASURED",
            "pinned_v2_literal_references": sorted(pointers[name]),
        })
    return hash_bytes(payload), config["version"], results, methods


def v3_data(directory):
    table = (directory/"census/census.csv").read_bytes()
    columns, projects, values = grid(table, KEY)
    status = (directory/"census/cell_status.csv").read_bytes()
    rows = ledger(status, values.keys())
    receipt = json.loads((directory/"build_receipt.json").read_bytes())
    report = json.loads((directory/"census/census_receipt.json").read_bytes())
    for key, value in values.items():
        state = rows[key]["value_state"]
        if (state=="PRESENT") != bool(value) or rows[key]["value"] != value or not rows[key]["reason"]:
            raise ValueError(f"Invalid V3 Census cell {key}")
    for key, expected in receipt["scientific_artifacts"].items():
        if hash_bytes((directory/key).read_bytes()) != expected:
            raise ValueError(f"Corrupt V3 build artifact {key}")
    if receipt["scientific_artifacts"]["census/census.csv"] != hash_bytes(table):
        raise ValueError("V3 build Census differs from its receipt")
    return columns, projects, values, rows, receipt, report


def diagnostic(legacy, new, key):
    cell, proof = legacy
    row, value = new
    display_equal = cell == value
    if not display_equal and key[1] not in (KEY,"ASX代碼") and is_numeric(cell) and is_numeric(value):
        display_equal = Decimal(cell) == Decimal(value)
    sources = set(SHA.findall(proof.get("source_sha256", "")))
    if row["value_state"] == "PRESENT":
        if key[1] == KEY:
            return "MATCHED", "EXACT_PROJECT_KEY"
        formal = (
            row.get("data_role") in {"FORMAL_OBSERVATION", "FORMAL_DERIVED"}
            or row.get("value_origin") in {"FORMAL_SELECTION", "FORMAL_DIRECT", "FORMAL_DERIVED"}
        )
        if formal and proof["effective_date"] and row.get("effective_date") and proof["effective_date"] != row["effective_date"]:
            return "DIFFERENT_SCIENTIFIC_SCOPE", "REPORTED_EFFECTIVE_DATE_DIFFERS"
        if display_equal:
            return "MATCHED", "DISPLAY_EQUAL_ONLY_SCIENCE_SCOPE_NOT_PROVEN" if formal else "DISPLAY_VALUE_EQUAL"
        if any(row.get(name) for name in ("source_sha256", "registry_sha256", "lineage_id", "evidence_id",
                                         "selection_id", "metadata_evidence", "registry_snapshot_sha256")):
            return "UPDATED_WITH_EVIDENCE", "NEW_V3_VALUE_HAS_INDEPENDENT_PROJECT_OR_CELL_EVIDENCE"
        return "FORMAL_UNAVAILABLE", "V3_PRESENT_PROVENANCE_NOT_EXPOSED_TO_DIAGNOSTIC"
    reason, source_search = row["reason"], row.get("source_search_status")
    if (source_search in {"SOURCE_OBJECT_UNAVAILABLE","NO_SOURCE_FOUND","NOT_SEARCHED","SOURCE_LOOKUP_BLOCKED"}
            or reason in {"SOURCE_UNAVAILABLE","NO_SOURCE_FOUND","NO_VERIFIED_DOCUMENT"}):
        return "NO_NEW_V3_SOURCE", "V3_SOURCE_AVAILABILITY_OR_SEARCH_GAP"
    if "SCOPE_MISMATCH" in reason or "CONFLICTING_SELECTED_CONTEXT" in reason:
        return "DIFFERENT_SCIENTIFIC_SCOPE", "V3_EXPLICIT_SCOPE_FAILURE"
    if not ((sources and proof.get("locator")) or proof["method"] in STRUCTURAL):
        return "UNVERIFIED_LEGACY_VALUE", "NO_COMPLETE_HISTORICAL_SOURCE_LOCATOR_CHAIN"
    return "FORMAL_UNAVAILABLE", "NO_GOVERNED_V3_VALUE_FOR_CELL"


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as file:
        output = csv.DictWriter(file, fieldnames=list(rows[0]), lineterminator="\n")
        output.writeheader()
        output.writerows(rows)


def independent_research_value(value, release_id):
    if isinstance(value, list):
        return [independent_research_value(item, release_id) for item in value]
    if not isinstance(value, dict):
        return value
    if value.get("release_id") and value["release_id"] != release_id:
        raise ValueError("Scientific output references a different Release")
    result = {
        key: independent_research_value(item, release_id) for key, item in value.items()
        if key != "release_id"
    }
    if value.get("selection_id"):
        envelope = ["fresh-resource-selection-v1", release_id, value["validation_id"]]
        expected = hash_bytes(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if value["selection_id"] != expected:
            raise ValueError("Selected evidence has an invalid release-derived identifier")
        result["selection_id"] = value["validation_id"]
    return result


def formal_preservation(baseline, current, latest_status, latest_receipt):
    _, _, _, previous_status, prior_receipt, _ = v3_data(baseline)
    previous_formal = {
        key: row for key, row in previous_status.items()
        if row["value_origin"] == "FORMAL_SELECTION"
    }
    previous_release = prior_receipt["observation_release_id"]
    latest_release = latest_receipt["observation_release_id"]
    differences = []
    rekeyed = 0
    for key, previous_cell in previous_formal.items():
        prior_value = independent_research_value(previous_cell, previous_release)
        latest_value = independent_research_value(latest_status[key], latest_release)
        changed = [name for name, value in prior_value.items() if latest_value.get(name) != value]
        if previous_cell["selection_id"] != latest_status[key]["selection_id"]:
            rekeyed += 1
        if changed:
            differences.append({"project_id": key[0], "column": key[1], "fields": changed})

    def decision(directory):
        return json.loads((directory / "decision/mcda_inputs.json").read_bytes())

    def comparable(inputs, release_id):
        return {
            (project_id, criterion): independent_research_value(entry, release_id)
            for project_id, entries in inputs["criteria_by_project"].items()
            for criterion, entry in entries.items()
            if entry["state"] == "COMPARABLE"
        }

    def observations(directory, release_id):
        rows = (directory / "observations/observations.jsonl").read_text(encoding="utf-8").splitlines()
        return {
            (entry["project_id"], entry["source_sha256"], entry["observation_id"]): independent_research_value(entry, release_id)
            for row in rows
            if (entry := json.loads(row))["observation_id"]
        }

    prior_inputs, latest_inputs = decision(baseline), decision(current)
    before = comparable(prior_inputs, previous_release)
    after = comparable(latest_inputs, latest_release)
    lost = [{"project_id": key[0], "criterion": key[1]} for key, value in before.items() if after.get(key) != value]
    prior_rank = json.loads((baseline / "decision/mcda_results.json").read_bytes())["panels"]
    latest_rank = json.loads((current / "decision/mcda_results.json").read_bytes())["panels"]
    panel_changes = sorted(name for name, result in prior_rank.items() if latest_rank.get(name) != result)
    panel_input_changes = sorted(
        name for name, result in prior_inputs["panels"].items()
        if independent_research_value(latest_inputs["panels"].get(name), latest_release)
        != independent_research_value(result, previous_release)
    )
    policy_changed = prior_inputs["policy_sha256"] != latest_inputs["policy_sha256"]
    prior_observations = observations(baseline, previous_release)
    next_observations = observations(current, latest_release)
    observation_changes = [list(key) for key, value in prior_observations.items() if next_observations.get(key) != value]
    checks = (differences, lost, panel_changes, panel_input_changes, observation_changes, policy_changed)
    return {
        "status": "FAIL" if any(checks) else "PASS",
        "comparison_identity_treatment": {
            "release_id": "Checked against each build's observation Release then omitted from cross-version comparisons",
            "selection_id": "Independently recomputed from its own Release and validation ID, then compared by stable validation ID",
        },
        "formal_cells_with_release_rekeyed_selections": rekeyed,
        "baseline_code_commit": prior_receipt["code_commit"],
        "formal_cells_checked": len(previous_formal),
        "formal_cell_differences": differences,
        "criterion_keys_checked": len(before),
        "criterion_keys_changed_or_lost": lost,
        "additional_comparable_criterion_keys": len(after.keys() - before.keys()),
        "mcda_policy_changed": policy_changed,
        "panel_inputs_changed": panel_input_changes,
        "panel_outputs_changed": panel_changes,
        "observation_rows_checked": len(prior_observations),
        "observation_rows_changed_or_lost": observation_changes,
        "additional_observation_rows": len(next_observations.keys() - prior_observations.keys()),
    }


def resource_statement_references(rows, receipt):
    references = []
    for (project, column), cell in rows.items():
        if cell.get("column_id") != "has_treo_resource" or cell["value_state"] != "PRESENT":
            continue
        if (cell["value"], cell["evidence_state"], cell["comparable"]) != ("是", "DESCRIPTIVE_ONLY", "NO"):
            raise ValueError(f"Resource statement reference has an invalid role for {project}")
        evidence = json.loads(cell["metadata_evidence"])
        release_id = receipt["observation_release_id"]
        independent_research_value(evidence, release_id)
        lineage = evidence["inputs"]
        raw_ids = json.loads(cell["input_observation_ids"])
        for key in ("scope_id", "statement_id", "source_sha256", "effective_date"):
            if not cell[key] or any(row[key] != cell[key] for row in lineage) or evidence["statement"][key] != cell[key]:
                raise ValueError(f"Resource statement reference is broken in {key} for {project}")
        if len(lineage) != 3 or len(set(raw_ids)) != 3 or [row["observation_id"] for row in lineage] != raw_ids:
            raise ValueError(f"Resource statement reference lacks exact source inputs for {project}")
        for row in lineage:
            if (row["project_id"] != project or not row["anchor_id"]
                    or not row["temporal_decision"]["evidence_ids"] or row["temporal_decision"]["status"] != "ELIGIBLE"):
                raise ValueError(f"Resource statement reference has invalid source/anchor/cutoff for {project}")
        references.append({"project_id": project, "column": column, "source_sha256": cell["source_sha256"],
                           "scope_id": cell["scope_id"], "statement_id": cell["statement_id"],
                           "input_observation_ids": raw_ids})
    return references


def projection_delta(previous, latest):
    if previous is None:
        return {"status": "NOT_RUN"}
    _, _, _, past, receipt, _ = v3_data(previous)
    changed = []
    for (project, column), old in past.items():
        new = latest[project, column]
        if (old["value_state"], old["value"]) != (new["value_state"], new["value"]):
            changed.append({"project_id": project, "column": column, "before_state": old["value_state"],
                            "after_state": new["value_state"], "after_origin": new["value_origin"]})
    return {"previous_code_commit": receipt["code_commit"], "cell_keys_checked": len(past), "display_changes": changed,
            "reason_only_or_explanation_changes": sum(
                old["reason"] != latest[key]["reason"] and (old["value_state"], old["value"])
                == (latest[key]["value_state"], latest[key]["value"]) for key, old in past.items()
            )}


def matching_source_objects(manifest_path, expected_sha):
    if manifest_path is None:
        return None
    content = manifest_path.read_bytes()
    if hash_bytes(content) != expected_sha:
        raise ValueError("Source availability overlay must use this build's exact source manifest")
    manifest = json.loads(content)
    return {
        (project_id, entry["sha256"])
        for entry in manifest["editions"]
        if entry["integrity"] == "EXACT_SHA"
        for project_id in entry["project_ids"]
    }


def run(v2, v3, out, baseline=None, sources=None, previous=None):
    archived = {name: frozen(v2, V2_DIR+"/"+name) for name in V2_FILES}
    cols, ids, prior = grid(archived[V2_FILES[0]], KEY)
    states = ledger(archived["field_status.csv"], prior.keys())
    provenance = ledger(archived["field_provenance.csv"], prior.keys())
    old_release = json.loads(archived["census_release.json"])
    old_coverage = json.loads(archived["coverage.json"])
    if old_release["census_csv_sha256"] != hash_bytes(archived[V2_FILES[0]]):
        raise ValueError("Pinned V2 Census release does not confirm exact CSV")
    columns, project_ids, current, observed, receipt, report = v3_data(v3)
    if cols != columns or set(ids) != set(project_ids):
        raise ValueError("V2 and V3 diagnostic cohorts or headings differ")
    exact_editions = matching_source_objects(sources, receipt["input_artifacts"]["sources"])
    raw, precise, methods, old_filled_cols, by_category = Counter(), Counter(), Counter(), Counter(), Counter()
    availability_counts = Counter()
    provenance_ids = {"observation_ids": set(), "attribute_ids": set()}
    populated_cells_with_ids = Counter()
    category_counts_by_column = defaultdict(Counter)
    diff, una = [], []
    for cell, old in prior.items():
        project_id, column = cell
        state, proof = states[cell], provenance[cell]
        raw[state["value_state"]] += 1
        precise[state["status"]] += 1
        if proof["value"] != old or (state["value_state"] == "PRESENT") != bool(old):
            raise ValueError(f"Frozen V2 ledger mismatch for {cell}")
        new = observed[cell]
        if state["status"].startswith("NOT_APPLICABLE"):
            una.append({"project_id":project_id,"column":column,"v2_status":state["status"],
                        "v3_state":new["value_state"],"v3_reason":new["reason"]})
        if old:
            methods[proof["method"]] += 1
            for field, identifiers in provenance_ids.items():
                references = json.loads(proof[field])
                if not isinstance(references, list) or not all(isinstance(value, str) for value in references):
                    raise ValueError(f"Malformed historical provenance references in {cell}")
                if references:
                    populated_cells_with_ids[field] += 1
                    identifiers.update(references)
            old_filled_cols[column] += 1
            category, reason = diagnostic((old, proof),(new, current[cell]),cell)
            by_category[category] += 1
            category_counts_by_column[column][category]+=1
            old_shas = set(SHA.findall(proof["source_sha256"]))
            new_shas = set(SHA.findall(new.get("source_sha256", "")))
            metadata_evidence = json.loads(new["metadata_evidence"]) if new.get("metadata_evidence") else {}
            if isinstance(metadata_evidence, dict) and SHA.fullmatch(metadata_evidence.get("source_sha256") or ""):
                new_shas.add(metadata_evidence["source_sha256"])
            if not old_shas:
                exact_state = "NO_ARCHIVED_EDITION_SHA"
            elif exact_editions is None:
                exact_state = "NOT_RUN"
            elif any((project_id, digest) in exact_editions for digest in old_shas):
                exact_state = "EXACT_PROJECT_EDITION_IN_CURRENT_MANIFEST"
            else:
                exact_state = "EXACT_PROJECT_EDITION_NOT_IN_CURRENT_MANIFEST"
            availability_counts[exact_state] += 1
            diff.append({
                "project_id":project_id, "column":column, "diagnostic":category, "reason":reason,
                "legacy_value_sha256":hash_bytes(old.encode()),"v3_value_sha256":hash_bytes(current[cell].encode()) if current[cell] else "",
                "legacy_method":proof["method"],"legacy_source_locator_bound":bool(proof["source_sha256"] and proof["locator"]),
                "v3_state":new["value_state"],"v3_origin":new.get("value_origin",""),
                "v3_data_role":new.get("data_role",""),"v3_reason":new["reason"],
                "source_sha_overlap": bool(old_shas & new_shas),
                "legacy_edition_in_current_manifest": exact_state,
                "scope_label_match":(proof["scope"]==new.get("scope")) if proof["scope"] and new.get("scope") else "",
            })
    if (raw["PRESENT"],len(una),raw["BLANK"]-len(una),precise["NOT_APPLICABLE_NO_MRE"]) != (2500,1715,528,1650):
        raise ValueError("Archived V2 denominator and state counts differ from fixed premise")
    fixed_denominator = old_coverage["gap_review"]["baseline_applicable"]
    adjusted_denominator = len(prior) - len(una)
    if old_coverage["field_status_counts"] != precise or (fixed_denominator, adjusted_denominator) != (3034, 3028):
        raise ValueError("Archived coverage statement differs from the measured ledger")
    rules_sha, rules_version, rules, static_methods = rule_inventory(v2, cols, old_filled_cols, precise, provenance, methods)
    out.mkdir(parents=True,exist_ok=False)
    write_csv(out/"v2_census_present_diagnostic.csv",diff)
    write_csv(out/"v2_not_applicable_diagnostic.csv",una)
    (out/"v2_census_rule_inventory.json").write_bytes(json_bytes(rules))
    report_sha = hash_bytes((v3/"census/census_receipt.json").read_bytes())
    v3_roles = Counter(row.get("value_origin","UNKNOWN") for row in observed.values() if row["value_state"]=="PRESENT")
    roles = {
        "registry_identity_affiliation_and_admin": (KEY,"專案名稱","開發商","未上市公司","ASX代碼",*GEO_TEXT),
        "source_reference_and_notes": (*SOURCE,"總說明"),
        "geology_and_development": (*STAGE,*DEPOSIT),
        "location": POINT,
        "forecast_and_product": FORECAST,
        "resource_and_composition": (*DIRECT_RESOURCE,"有TREO資源估計",*COMPOSITION_GROUPS),
    }
    classified = {column: name for name, fields in roles.items() for column in fields}
    historical_groups=Counter()
    for column,count in old_filled_cols.items():
        historical_groups[classified.get(column,"resource_and_composition")]+=count
    preserved = formal_preservation(baseline, v3, observed, receipt) if baseline else {"status":"NOT_RUN"}
    references = resource_statement_references(observed, receipt)
    delta = projection_delta(previous, observed)
    result = {
        "schema":"census-web-historical-diagnostic-v1","status":"FAIL" if preserved["status"]=="FAIL" else "PASS",
        "formal_baseline_preservation":preserved,
        "incremental_projection_delta": delta,
        "resource_statement_references_verified": references,
        "v2_legacy_populated_field_groups":dict(historical_groups),
        "legacy_edition_availability_in_current_manifest": dict(availability_counts),
        "source_overlay_manifest_sha256": hash_bytes(sources.read_bytes()) if sources else None,
        "scope":"V2 archival accounting against V3 Census state for stable cell keys. No V2 value promotion or raw value copy.",
        "v2_fixed_commit":V2_REF,"v2_release_science_commit":old_release["code"]["commit"],
        "v2_rule_version":rules_version,"v2_rule_sha256":rules_sha,
        "v2_archived_rules_sha256":old_release["configuration_files"]["summary_rules.json"],
        "v2_rules_match_frozen_archive":rules_sha==old_release["configuration_files"]["summary_rules.json"],
        "v2_archive_sha256":{name:hash_bytes(data) for name,data in archived.items()},
        "projects":len(ids),"columns":len(cols),"cells":len(prior),"v2_filled_cells":len(diff),
        "v2_legacy_NA":len(una),"v2_legacy_applicable_missing":raw["BLANK"]-len(una),
        "v2_exact_status_totals":dict(precise),"v2_methods_at_populated_cells":dict(methods),
        "v2_pinned_method_implementations": static_methods,
        "v2_populated_cells_with_linked_ids": dict(populated_cells_with_ids),
        "v2_distinct_referenced_ids": {key: len(ids) for key, ids in provenance_ids.items()},
        "v2_archived_historical_coverage": f"{len(diff)} / {fixed_denominator} = {len(diff) / fixed_denominator:.2%}",
        "v2_archived_reclassified_coverage": f"{len(diff)} / {adjusted_denominator} = {len(diff) / adjusted_denominator:.2%}",
        "v2_n_a_no_mre_to_v3_states":dict(Counter(r["v3_state"] for r in una if r["v2_status"]=="NOT_APPLICABLE_NO_MRE")),
        "v2_legacy_diagnostics":{label:by_category[label] for label in CLASSIFICATIONS},
        "v2_populated_cells_by_column":dict(old_filled_cols),
        "by_column":dict(category_counts_by_column),
        "v2_rule_dispositions":dict(Counter(row["disposition"] for row in rules)),
        "v3_build_receipt_sha256":hash_bytes((v3/"build_receipt.json").read_bytes()),
        "v3_census_receipt_sha256":report_sha,"v3_code_commit":receipt["code_commit"],
        "v3_census_roles":dict(v3_roles),"v3_census_states":report["cell_states"],
        "v3_science_scope":{"formal_scientific_cells":receipt["scientific_output_count"],
                            "comparable_project_criteria":receipt["comparable_project_criteria"],"panels":receipt["mcda_panels"]},
        "outputs_sha256":{path.name:hash_bytes(path.read_bytes()) for path in sorted(out.iterdir())},
    }
    (out/"census_historical_diagnostic.json").write_bytes(json_bytes(result))
    return result


_GAP_EVIDENCE = {
    "resource": ("NATIVE_RESOURCE_STATEMENT_UNBOUND", "PROJECT_DEPOSIT_DATE_CUTOFF_CATEGORY_AND_EXACT_ORE_OR_TREO_ROW"),
    "resource_formula": ("HISTORICAL_RESOURCE_FORMULA_UNREPRODUCED", "SAME_STATEMENT_VERIFIED_VALUES_AND_APPROVED_FORMULA"),
    "composition": ("OXIDE_MEMBERS_AND_DENOMINATOR_UNVERIFIED", "SAME_STATEMENT_OXIDE_ROWS_UNITS_SPECIES_AND_MASS_BASIS"),
    "location": ("OFFICIAL_FEATURE_ROLE_OR_TIME_UNVERIFIED", "OFFICIAL_NATIVE_FEATURE_ROLE_CRS_ACCURACY_AND_EFFECTIVE_DATE"),
    "stage": ("DATED_COMPLETED_MILESTONE_SCOPE_UNBOUND", "ACTUAL_COMPLETED_MILESTONE_WITH_ISSUER_SCOPE_AND_DATE"),
    "geology": ("GEOLOGIC_CLASS_AND_SCOPE_UNVERIFIED", "SCOPED_PRIMARY_DEPOSIT_GEOLOGY_DESCRIPTION"),
    "corporate": ("LEGAL_LISTING_STATUS_AS_OF_UNVERIFIED", "DATED_OFFICIAL_LISTING_OR_ENTITY_REGISTRATION"),
    "forecast": ("PRODUCT_SCENARIO_OR_PERIOD_UNVERIFIED", "SCOPED_STUDY_ENDPOINT_SCENARIO_AND_NAMED_TIME_BASIS"),
    "source": ("VERIFIED_SOURCE_ATTRIBUTION_UNBOUND", "EXACT_EDITION_WITH_PROJECT_SCOPE_AND_PUBLIC_CAPTURE"),
}


def _historical_gap_type(column, method):
    if column in DIRECT_RESOURCE:
        is_calculated_mass = column == DIRECT_RESOURCE[2] and method not in {"SOURCE_REPORTED", "REPORTED"}
        return "resource_formula" if is_calculated_mass else "resource"
    if column == "有TREO資源估計":
        return "resource"
    if column in COMPOSITION_GROUPS or re.match(r"[A-Z][a-z]?[0-9]*O[0-9]+_氧化", column):
        return "composition"
    for fields, kind in ((POINT, "location"), (STAGE, "stage"), (DEPOSIT, "geology"), (FORECAST, "forecast"),
                         (SOURCE, "source"), (("未上市公司",), "corporate")):
        if column in fields:
            return kind
    raise ValueError(f"Unreviewed historical gap field {column}")


def historical_source_gaps(v2, historical_csv, sources, out):
    """Classify the pinned historical gap denominator with exact lookup evidence only."""
    pinned = Path(__file__).resolve().parents[1] / "docs/research/census_science_historical_diagnostic.json"
    reference = json.loads(pinned.read_bytes())
    payload = historical_csv.read_bytes()
    if hash_bytes(payload) != reference["outputs_sha256"]["v2_census_present_diagnostic.csv"]:
        raise ValueError("Historical gaps require the exact 1,396-slot diagnostic ledger")
    fixed = matching_source_objects(sources, reference["source_overlay_manifest_sha256"])
    archived = {name: frozen(v2, f"{V2_DIR}/{name}") for name in (V2_FILES[0], "field_provenance.csv")}
    if any(hash_bytes(data) != reference["v2_archive_sha256"][name] for name, data in archived.items()):
        raise ValueError("Historical gap provenance differs from the pinned V2 tree")
    columns, projects, grid_values = grid(archived[V2_FILES[0]], KEY)
    proofs = ledger(archived["field_provenance.csv"], grid_values.keys())
    _, prior = csv_input(payload)
    output, field_counts, seen = [], defaultdict(Counter), set()
    for row in prior:
        if row["diagnostic"] != "FORMAL_UNAVAILABLE":
            continue
        project, column = key = row["project_id"], row["column"]
        proof = proofs[key]
        if (key in seen or proof["method"] != row["legacy_method"]
                or hash_bytes(proof["value"].encode()) != row["legacy_value_sha256"]):
            raise ValueError("Historical gap locator and canonical slot differ")
        seen.add(key)
        source_shas = set(SHA.findall(proof["source_sha256"]))
        present = {digest for digest in source_shas if (project, digest) in fixed}
        edition_status = ("EXACT_PROJECT_EDITION_IN_CURRENT_MANIFEST" if present
                          else "EXACT_PROJECT_EDITION_NOT_IN_CURRENT_MANIFEST")
        if not source_shas or not proof["locator"] or edition_status != row["legacy_edition_in_current_manifest"]:
            raise ValueError("Historical root cause requires the pinned original Project and source edition")
        evidence_type = _historical_gap_type(column, proof["method"])
        blocker, next_evidence = _GAP_EVIDENCE[evidence_type]
        state = "EVIDENCE_INSUFFICIENT" if present else "SOURCE_UNAVAILABLE"
        field_counts[column][state] += 1
        field_counts[column][blocker] += 1
        output.append({
            "project_id": project, "field_id": column, "historical_baseline_value_state": row["v3_state"],
            "historical_baseline_reason": row["v3_reason"], "source_root_state": state,
            "evidence_blocker": blocker, "next_evidence": next_evidence, "legacy_method": proof["method"],
            "legacy_value_sha256": row["legacy_value_sha256"],
            "exact_project_sha256_in_fixed": "|".join(sorted(present)),
            "exact_project_sha256_outside_fixed": "|".join(sorted(source_shas - present)),
            "source_url_hint": proof["source_url"], "original_locator_hint": proof["locator"],
            "scope_hint": proof["scope"], "effective_date_hint": proof["effective_date"],
            "mcda_relevance_hint": {"resource": "R1", "resource_formula": "R1", "composition": "S1",
                                    "location": "I1–I4", "stage": "D1"}.get(evidence_type, "NONE"),
            "historical_value_copied": False,
        })
    total = reference["v2_legacy_diagnostics"]["FORMAL_UNAVAILABLE"]
    if len(output) != total or len(projects) != 93 or len(columns) != 51:
        raise ValueError("Historical gap denominator disagrees with the pinned release")
    if out.resolve().is_relative_to(v2.resolve()):
        raise ValueError("Never write inside the V2 reference directory")
    out.mkdir(parents=True, exist_ok=False)
    listing = out / "historical_source_gap_cells.csv"
    write_csv(listing, output)
    result = {
        "schema": "historical-display-source-gap-census-v1", "status": "PASS", "historical_gap_slots": total,
        "fixed_source_manifest_sha256": reference["source_overlay_manifest_sha256"], "v2_reference": V2_REF,
        "historical_v3_baseline_commit": reference["v3_code_commit"],
        "historical_input_sha256": hash_bytes(payload), "gap_cells_sha256": hash_bytes(listing.read_bytes()),
        "by_source_state": dict(Counter(row["source_root_state"] for row in output)),
        "by_evidence_blocker": dict(Counter(row["evidence_blocker"] for row in output)),
        "by_column": {column: dict(field_counts[column]) for column in columns},
        "retained_scientific_values_from_v2": 0, "new_independent_verified_observations": 0,
    }
    (out / "historical_source_gap_receipt.json").write_bytes(json_bytes(result))
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root",type=Path,required=True)
    parser.add_argument("--v3-build",type=Path)
    parser.add_argument("--historical-gaps", type=Path, help="Classify the pinned historical display gap CSV")
    parser.add_argument("--v3-baseline",type=Path)
    parser.add_argument("--sources", type=Path)
    parser.add_argument("--v3-previous", type=Path)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if args.out.resolve().is_relative_to(args.v2_root.resolve()):
        raise ValueError("Never write inside the V2 reference directory")
    if args.historical_gaps:
        if not args.sources or args.v3_build:
            parser.error("--historical-gaps requires --sources and is independent of --v3-build")
        result = historical_source_gaps(args.v2_root, args.historical_gaps, args.sources, args.out)
        print({key: result[key] for key in ("status", "historical_gap_slots", "by_source_state", "gap_cells_sha256")})
        raise SystemExit(result["status"] != "PASS")
    if not args.v3_build:
        parser.error("--v3-build is required for the standard coverage diagnostic")
    result=run(args.v2_root,args.v3_build,args.out,args.v3_baseline,args.sources,args.v3_previous)
    print({key:result[key] for key in ("status","v2_rule_dispositions","v2_legacy_diagnostics","v3_census_roles")})
    raise SystemExit(result["status"] != "PASS")
