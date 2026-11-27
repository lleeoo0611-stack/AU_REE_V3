"""Audit and compare Census, comparability and actual MCDA across V3 builds."""

from collections import Counter
import csv
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO
import json
from math import isfinite
from pathlib import Path
import re

from .census import COLUMNS
from .census_metadata import METADATA_ORIGINS, coverage_report, load_projection_policy, metadata_projection
from .comparability import census_quantity
from .contracts import canonical_json
from .decision import leave_one_out, rank_panel
from .fresh_census import PROJECTION_SCHEMA, STATUS_FIELDS, validate_projected_cell
from .publication import ContentReplica, load_release
from .registry import PROJECT_ID_COLUMN
from .research_artifacts import ARTIFACTS

SCHEMA = "research-delta-v1"
_RECEIPT_FIELDS = {"release_id", "selection_id", "validation_id", "rules_sha256",
                   "source_search_status", "context_diagnostics", "column_id", "data_role", "evidence_state",
                   "projection_policy_version", "projection_policy_sha256", "precision", "lineage_id",
                   "metadata_evidence", "formula_id", "formula_sha256", "input_observation_ids", "geometry_role"}
_SCIENCE_FIELDS = {"value", "unit", "raw_value", "raw_unit"}
_LINEAGE_KEYS = {"release_id", "selection_id", "validation_id", "observation_id",
                 "source_sha256", "scope_id", "anchor_id"}
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _digest(data: bytes) -> str:
    return sha256(data).hexdigest()


def _object(data: bytes, name: str) -> dict:
    def pairs(entries):
        result = dict(entries)
        if len(result) != len(entries):
            raise ValueError(f"Duplicate JSON keys in {name}")
        return result

    def invalid_constant(value):
        raise ValueError(f"Nonfinite JSON number in {name}: {value}")

    value = json.loads(data, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {name}")
    return value


def _csv(data: bytes, name: str) -> tuple[tuple[str, ...], list[dict]]:
    reader = csv.DictReader(StringIO(data.decode("utf-8"), newline=""))
    headings = tuple(reader.fieldnames or ())
    rows = list(reader)
    if not headings or len(headings) != len(set(headings)) or any(
        None in row or None in row.values() for row in rows
    ):
        raise ValueError(f"Invalid CSV headings or row widths in {name}")
    return headings, rows


def _decimal_token(value) -> bool:
    try:
        return isinstance(value, str) and Decimal(value).is_finite()
    except InvalidOperation:
        return False


@dataclass(frozen=True)
class ResearchBuild:
    receipt: dict
    receipt_sha: str
    census: dict
    decision_inputs: dict
    decision_results: dict
    observations: dict
    census_columns: tuple[str, ...]
    census_projects: tuple[str, ...]

    @classmethod
    def load(cls, directory: Path):
        receipt_data = (directory / "build_receipt.json").read_bytes()
        receipt = _object(receipt_data, "build_receipt.json")
        if receipt.get("schema") != "fresh-research-build-v1":
            raise ValueError("Comparable inputs must be V3 fresh research build receipts")
        if set(receipt["scientific_artifacts"]) != set(ARTIFACTS):
            raise ValueError("V3 build receipt scientific artifact set differs from the comparison contract")
        artifacts = {name: (directory / name).read_bytes() for name in ARTIFACTS}
        if {name: _digest(content) for name, content in artifacts.items()} != receipt["scientific_artifacts"]:
            raise ValueError("V3 build artifact SHA-256 differs from its receipt")
        observations = _selected_observations(artifacts["observations/observations.jsonl"], receipt)
        census, projects, columns = _census(artifacts, receipt, observations)
        decisions, results = _decision(artifacts, receipt, projects, observations)
        return cls(receipt, _digest(receipt_data), census, decisions, results, observations, columns, projects)


def _selected_observations(data, receipt):
    observations = {}
    for line in data.splitlines():
        observation = _object(line, "observations.jsonl")
        if observation.get("selected_for_census") is not True:
            continue
        key = observation["observation_id"]
        if key in observations:
            raise ValueError("Selected V3 Observation ID is duplicated in the extraction artifact")
        if observation["outcome"] != "VALIDATED" or not _DIGEST.fullmatch(observation["source_sha256"]):
            raise ValueError("Selected extraction has an unverified outcome or source fingerprint")
        observations[key] = observation
    if len(observations) != receipt["observation_counts"]["selected_count"]:
        raise ValueError("Selected observations do not match the V3 build receipt")
    return observations


def _check_observation(lineage, observation, project_id, observation_release_id):
    if not _LINEAGE_KEYS <= lineage.keys() or any(not lineage[key] for key in _LINEAGE_KEYS):
        raise ValueError(f"Incomplete formal lineage for {project_id}")
    if lineage["release_id"] != observation_release_id:
        raise ValueError(f"Formal provenance belongs to another governed release for {project_id}")
    if observation is None or any(
        observation[key] != value for key, value in (
            ("project_id", project_id), ("source_sha256", lineage["source_sha256"]), ("scope_id", lineage["scope_id"]),
        )
    ):
        raise ValueError(f"Formal provenance does not match a selected observation for {project_id}")


def _same_census_quantity(cell, observation) -> bool:
    variable = observation["variable"]
    return (
        COLUMNS.get(variable) == cell["column"]
        and _decimal_token(observation["value"])
        and census_quantity(variable, observation["value"], observation["unit"]) == (cell["value"], cell["unit"])
        and (observation["raw_value"], observation["raw_unit"]) == (cell["raw_value"], cell["raw_unit"])
    )


def _check_display_metadata(cell, definition, receipt):
    origin = cell["value_origin"]
    if origin not in METADATA_ORIGINS or not metadata_projection(definition) or cell["evidence_state"] != "DESCRIPTIVE_ONLY":
        raise ValueError("Census display value lacks an allowed metadata projection")
    evidence = _object(cell["metadata_evidence"].encode("utf-8"), "metadata_evidence")
    if evidence.get("display_only") is not True:
        raise ValueError("Census display value lacks a bounded evidence role")
    if origin == "SOURCE_EVIDENCE":
        if (evidence.get("source_manifest_sha256") != receipt["input_artifacts"]["sources"]
            or evidence.get("source_sha256") != cell["source_sha256"]
            or evidence.get("temporal_decision_id") != cell["temporal_decision_id"]
            or evidence.get("scope") != "PROJECT_REFERENCE_ONLY" or cell["temporal_status"] != "ELIGIBLE"
            or not _DIGEST.fullmatch(cell["temporal_decision_id"]) or not json.loads(cell["temporal_evidence_ids"])):
            raise ValueError("Census source metadata lacks an eligible project-level source")
    elif cell["registry_sha256"] != receipt["project_registry_hash"] or evidence.get("registry_sha256") != cell["registry_sha256"]:
        raise ValueError("Census display value does not reference the pinned Registry")


def _census(artifacts, receipt, observations):
    columns, table = _csv(artifacts["census/census.csv"], "census.csv")
    fields, cells = _csv(artifacts["census/cell_status.csv"], "cell_status.csv")
    census_receipt = _object(artifacts["census/census_receipt.json"], "census_receipt.json")
    if census_receipt["outputs"] != {
        name: receipt["scientific_artifacts"]["census/" + name] for name in ("census.csv", "cell_status.csv")
    } or census_receipt["status"] != "PASS":
        raise ValueError("Census receipt does not confirm its artifacts")
    if (census_receipt["profile_id"], census_receipt["research_cutoff"], census_receipt["registry_sha256"],
            census_receipt["column_contract_sha256"], census_receipt["release_id"]) != (
        receipt["profile_id"], receipt["research_cutoff"], receipt["project_registry_hash"],
        receipt["census_columns_sha256"], receipt["observation_release_id"],
    ):
        raise ValueError("Census schema or cohort differs from the build receipt")
    projects = tuple(row[PROJECT_ID_COLUMN] for row in table)
    if (len(projects), len(columns), len(cells)) != (93, 51, 4743) or len(set(projects)) != 93:
        raise ValueError("Expected the exact 93 Project by 51 column Census grid")
    policy = None
    if census_receipt["schema_version"] == PROJECTION_SCHEMA:
        policy = load_projection_policy(columns)
        if (fields != STATUS_FIELDS or receipt.get("census_projection_policy_sha256") != policy.sha256
            or census_receipt.get("projection_policy_sha256") != policy.sha256
            or census_receipt.get("projection_policy_version") != policy.version
            or census_receipt.get("coverage") != coverage_report(cells, policy)):
            raise ValueError("Census projection or coverage differs from the pinned column policy")
    elif "census_projection_policy_sha256" in receipt:
        raise ValueError("Census projection is missing its versioned ledger")
    definitions = policy.by_name if policy else {}
    tabular = {(row[PROJECT_ID_COLUMN], column): row[column] for row in table for column in columns}
    result = {}
    formal = 0
    for cell in cells:
        key = cell["project_id"], cell["column"]
        if key in result or key not in tabular or tabular[key] != cell["value"] or not cell["reason"]:
            raise ValueError(f"Census value or identity lacks a matching ledger cell: {key}")
        state, origin = cell["value_state"], cell["value_origin"]
        if state not in {"PRESENT", "UNKNOWN", "NOT_APPLICABLE"} or (state == "PRESENT") != bool(cell["value"]):
            raise ValueError(f"Invalid Census value state: {key}")
        if policy:
            definition = definitions[key[1]]
            if (cell["column_id"] != definition["column_id"] or cell["data_role"] != definition["data_role"]
                or cell["projection_policy_sha256"] != policy.sha256
                or cell["projection_policy_version"] != policy.version):
                raise ValueError(f"Census ledger column role differs from its policy: {key}")
            validate_projected_cell(cell, definition)
        if key[1] == PROJECT_ID_COLUMN:
            if origin != "IDENTITY" or cell["value"] != key[0] or cell["registry_sha256"] != receipt["project_registry_hash"]:
                raise ValueError(f"Registry identity differs from its pinned Census key: {key}")
        elif state == "PRESENT" and policy and origin in METADATA_ORIGINS:
            _check_display_metadata(cell, definition, receipt)
        elif state == "PRESENT":
            observation = observations.get(cell["observation_id"])
            _check_observation(cell, observation, key[0], receipt["observation_release_id"])
            if (
                census_receipt["release_mode"] != "FORMAL" or origin != "FORMAL_SELECTION"
                or (policy and cell["evidence_state"] != "FORMAL_DIRECT")
                or cell["temporal_status"] != "ELIGIBLE"
                or not _DIGEST.fullmatch(cell["temporal_decision_id"])
                or not json.loads(cell["temporal_evidence_ids"])
                or not _same_census_quantity(cell, observation)
            ):
                raise ValueError(f"Census value lacks an exact eligible selection: {key}")
            formal += 1
        elif origin == "FORMAL_SELECTION":
            raise ValueError(f"Missing Census value incorrectly marked as formally present: {key}")
        result[key] = cell
    if census_receipt["formal_scientific_cells"] != formal or receipt["scientific_output_count"] != formal:
        raise ValueError("Formal Census value count differs from its exact evidence rows")
    return result, projects, columns


def _checked_criterion(project, criterion, cell, receipt, observations):
    state = cell["state"]
    if state not in {"COMPARABLE", "UNKNOWN"} or not cell["reason"]:
        raise ValueError(f"Invalid criterion state for {project}/{criterion}")
    if state == "UNKNOWN":
        if cell["accepted_value"] is not None or cell["utility"] is not None:
            raise ValueError(f"Unavailable criterion has a value or utility for {project}/{criterion}")
    else:
        utility = cell["utility"]
        if not (
            _decimal_token(cell["accepted_value"]) and type(utility) in (int, float) and isfinite(utility)
            and 0 <= utility <= 1 and cell["unit"] and cell["lineage"] and cell["scientific_context"]
        ):
            raise ValueError(f"Comparable criterion lacks scientific inputs for {project}/{criterion}")
    seen = set()
    for entry in cell["lineage"]:
        observation_id = entry["observation_id"]
        observation = observations.get(observation_id)
        _check_observation(entry, observation, project, receipt["observation_release_id"])
        temporal = entry["temporal_decision"]
        if (
            observation_id in seen or entry["project_id"] != project
            or temporal["source_sha256"] != entry["source_sha256"]
            or temporal["cutoff"] != receipt["research_cutoff"]
            or temporal["status"] != "ELIGIBLE" or not temporal["matching_evidence_ids"]
            or not _DIGEST.fullmatch(temporal["decision_id"])
        ):
            raise ValueError(f"Criterion has duplicate or unverified evidence for {project}/{criterion}")
        seen.add(observation_id)


def _checked_panel(panel, result, criteria, policy, name):
    codes = panel["criterion_codes"]
    if codes != policy["panels"][name]:
        raise ValueError(f"Panel criteria diverge from the pinned policy: {name}")
    matrix = {}
    exclusions = {}
    for project, cells in criteria.items():
        missing = {code: cells[code]["reason"] for code in codes if cells[code]["state"] != "COMPARABLE"}
        if missing:
            exclusions[project] = missing
        else:
            matrix[project] = {code: cells[code]["utility"] for code in codes}
    if (panel["eligible_projects"] != sorted(matrix) or panel["benefit_matrix"] != matrix
            or panel["project_exclusions"] != exclusions):
        raise ValueError(f"Panel does not contain the exact complete-case cohort: {name}")
    minimum = policy["minimum_cohort"]
    enough = len(matrix) >= minimum
    status = "PASS" if enough else "INSUFFICIENT_COHORT"
    if (result["eligible_count"], result["minimum_cohort"], result["status"]) != (len(matrix), minimum, status):
        raise ValueError(f"Panel outcome differs from its complete-case cohort: {name}")
    rankings, sensitivity = result["point_rankings"], result["leave_one_out"]
    if not enough:
        if rankings or sensitivity:
            raise ValueError(f"Insufficient panel must contain no rankings or sensitivity output: {name}")
        return
    scenarios = set(panel["scenario_weights"])
    if set(rankings) != scenarios or set(sensitivity) != scenarios:
        raise ValueError(f"Ranked panel does not cover its exact scenarios: {name}")
    domains = {code: policy["criteria"][code]["domain"] for code in codes}
    threshold = policy["promethee_threshold"]
    for scenario, weights in panel["scenario_weights"].items():
        recalculated = {
            method: {project: asdict(score) for project, score in scores.items()}
            for method, scores in rank_panel(matrix, weights, threshold).items()
        }
        changes = [
            {"type": kind, "removed": removed,
             "projects": {project: asdict(change) for project, change in ranks.items()}}
            for (kind, removed), ranks in leave_one_out(matrix, weights, domains, threshold).items()
        ]
        if rankings[scenario] != recalculated or sensitivity[scenario] != changes:
            raise ValueError(f"MCDA rankings or sensitivity disagree with recomputation: {name}/{scenario}")


def _decision(artifacts, receipt, projects, observations):
    inputs = _object(artifacts["decision/mcda_inputs.json"], "mcda_inputs.json")
    results = _object(artifacts["decision/mcda_results.json"], "mcda_results.json")
    decision_receipt = _object(artifacts["decision/decision_receipt.json"], "decision_receipt.json")
    inputs_sha = receipt["scientific_artifacts"]["decision/mcda_inputs.json"]
    if (
        inputs_sha != results["mcda_inputs_sha256"] or inputs_sha != receipt["mcda_inputs_hash"]
        or inputs_sha != decision_receipt["mcda_inputs_sha256"]
        or decision_receipt["mcda_results_sha256"] != receipt["scientific_artifacts"]["decision/mcda_results.json"]
    ):
        raise ValueError("MCDA input and output hashes do not form one build")
    for artifact in (inputs, results, decision_receipt):
        if (artifact["profile_id"], artifact["research_cutoff"], artifact["registry_sha256"], artifact["release_id"]) != (
            receipt["profile_id"], receipt["research_cutoff"], receipt["project_registry_hash"],
            receipt["observation_release_id"],
        ):
            raise ValueError("MCDA artifact is for a different cohort, cutoff or observation release")
    if any(artifact["policy_sha256"] != inputs["policy_sha256"] for artifact in (results, decision_receipt)):
        raise ValueError("MCDA artifacts mix different decision policies")
    policy = inputs["policy"]
    criteria = inputs["criteria_by_project"]
    if set(criteria) != set(projects) or any(set(row) != set(policy["criteria"]) for row in criteria.values()):
        raise ValueError("Decision artifacts lack the exact Project by Criterion grid")
    for project_id, row in criteria.items():
        for code, cell in row.items():
            _checked_criterion(project_id, code, cell, receipt, observations)
    if set(inputs["panels"]) != set(results["panels"]) or set(inputs["panels"]) != set(policy["panels"]):
        raise ValueError("Missing or additional MCDA panels in decision artifacts")
    for name, panel in inputs["panels"].items():
        _checked_panel(panel, results["panels"][name], criteria, policy, name)
    comparable = sum(cell["state"] == "COMPARABLE" for row in criteria.values() for cell in row.values())
    if comparable != receipt["comparable_project_criteria"] or comparable != decision_receipt["comparable_project_criteria"]:
        raise ValueError("Comparable Project by Criterion count differs from the decision ledger")
    return inputs, results


def _fields_changed(before, after):
    missing = object()
    return sorted(key for key in before.keys() | after.keys() if before.get(key, missing) != after.get(key, missing))


def _census_delta(baseline, current):
    rows = []
    for project in baseline.census_projects:
        for column in baseline.census_columns:
            key = project, column
            before, after = baseline.census[key], current.census[key]
            changed = _fields_changed(before, after)
            formal_before = before["value_origin"] == "FORMAL_SELECTION"
            formal_after = after["value_origin"] == "FORMAL_SELECTION"
            if formal_after and not formal_before:
                kind = "FORMAL_ADDED"
            elif formal_before and not formal_after:
                kind = "FORMAL_RETURNED"
            elif not formal_before and not formal_after and before["value"] != after["value"]:
                kind = "DISPLAY_VALUE_CHANGED"
            elif set(changed) & _SCIENCE_FIELDS:
                kind = "VALUE_OR_PRECISION_CHANGED"
            elif set(changed) & {"value_state", "value_origin"}:
                kind = "STATE_CHANGED"
            elif not changed:
                kind = "UNCHANGED"
            elif set(changed) <= _RECEIPT_FIELDS:
                kind = "PROVENANCE_RECEIPT_CHANGED"
            else:
                kind = "CONTEXT_OR_REASON_CHANGED"
            rows.append({
                "project_id": project, "column": column, "change": kind,
                "changed_fields": canonical_json(changed),
                "before_value": before["value"], "after_value": after["value"],
                "before_state": before["value_state"], "after_state": after["value_state"],
                "before_source_sha256": before["source_sha256"], "after_source_sha256": after["source_sha256"],
                "before_selection_id": before["selection_id"], "after_selection_id": after["selection_id"],
                "before_reason": before["reason"], "after_reason": after["reason"],
                "before_cell": canonical_json(before), "after_cell": canonical_json(after),
            })
    return rows


def _comparability_delta(baseline, current):
    previous = baseline.decision_inputs["criteria_by_project"]
    latest = current.decision_inputs["criteria_by_project"]
    changes = []
    before = Counter()
    after = Counter()
    counts = Counter()
    for project in sorted(previous):
        for criterion in sorted(previous[project]):
            old, new = previous[project][criterion], latest[project][criterion]
            comparable_before = old["state"] == "COMPARABLE"
            comparable_after = new["state"] == "COMPARABLE"
            before[criterion] += comparable_before
            after[criterion] += comparable_after
            if comparable_after and not comparable_before:
                change = "ADDED"
            elif comparable_before and not comparable_after:
                change = "RETURNED"
            elif not comparable_before and not comparable_after:
                if old["reason"] != new["reason"]:
                    change = "UNKNOWN_REASON_CHANGED"
                elif any(old.get(key) != new.get(key) for key in ("lineage", "scientific_context")):
                    change = "UNKNOWN_EVIDENCE_CHANGED"
                else:
                    change = "UNCHANGED"
            else:
                change = "COMPARABLE_INPUT_CHANGED" if old != new else "UNCHANGED"
            counts[change] += 1
            if change != "UNCHANGED":
                changes.append({
                    "project_id": project, "criterion_id": criterion, "change": change,
                    "changed_fields": _fields_changed(old, new), "before": old, "after": new,
                })
    net = sum(after.values()) - sum(before.values())
    return {
        "schema": SCHEMA, "policy_version": current.decision_inputs["policy_version"],
        "policy_sha256": current.decision_inputs["policy_sha256"], "before_count": sum(before.values()),
        "after_count": sum(after.values()), "net_comparable_project_criteria": net,
        "target_net_gain": 10, "target_status": "ACHIEVED" if net >= 10 else "EVIDENCE_INSUFFICIENT",
        "comparison_counts": dict(counts),
        "count_by_criterion": {
            code: {"before": before[code], "after": after[code], "net": after[code] - before[code]}
            for code in sorted(before)
        },
        "changes": changes,
    }


def _missing_counts(project_exclusions):
    return dict(Counter(code for missing in project_exclusions.values() for code in missing))


def _ranking_changes(before, after):
    changes = []
    for scenario in sorted(before.keys() | after.keys()):
        old_scenario, new_scenario = before.get(scenario, {}), after.get(scenario, {})
        for method in sorted(old_scenario.keys() | new_scenario.keys()):
            old_method, new_method = old_scenario.get(method, {}), new_scenario.get(method, {})
            for project in sorted(old_method.keys() | new_method.keys()):
                old, new = old_method.get(project), new_method.get(project)
                if old != new:
                    changes.append({
                        "scenario": scenario, "method": method, "project_id": project,
                        "before": old, "after": new,
                    })
    return changes


def _cohort_and_mcda(baseline, current):
    result = {}
    for name, old in baseline.decision_inputs["panels"].items():
        new = current.decision_inputs["panels"][name]
        if old["scenario_weights"] != new["scenario_weights"]:
            raise ValueError(f"Fixed MCDA scenario weights changed for {name}")
        before, after = baseline.decision_results["panels"][name], current.decision_results["panels"][name]
        before_ids, after_ids = set(old["eligible_projects"]), set(new["eligible_projects"])
        result[name] = {
            "criterion_ids": new["criterion_codes"], "minimum_cohort": after["minimum_cohort"],
            "before_status": before["status"], "after_status": after["status"],
            "before_eligible_count": before["eligible_count"], "after_eligible_count": after["eligible_count"],
            "projects_added": sorted(after_ids - before_ids), "projects_returned": sorted(before_ids - after_ids),
            "before_missing_criterion_counts": _missing_counts(old["project_exclusions"]),
            "after_missing_criterion_counts": _missing_counts(new["project_exclusions"]),
            "project_exclusion_changes": [
                {"project_id": project, "before": old["project_exclusions"].get(project),
                 "after": new["project_exclusions"].get(project)}
                for project in sorted(old["project_exclusions"].keys() | new["project_exclusions"].keys())
                if old["project_exclusions"].get(project) != new["project_exclusions"].get(project)
            ],
            "scenario_weights": new["scenario_weights"], "after_benefit_matrix": new["benefit_matrix"],
            "ranking_changes": _ranking_changes(before["point_rankings"], after["point_rankings"]),
            "after_rankings": after["point_rankings"], "after_sensitivity": after["leave_one_out"],
        }
    return {"schema": SCHEMA, "panels": result}


def _bind_archive(build, release_root, release_id, storage):
    if release_id is None:
        return {}
    if release_root is None:
        raise ValueError("An archive release ID requires its immutable release root")
    manifest = load_release(release_root, release_id, storage)
    if (
        manifest["build_receipt_sha256"] != build.receipt_sha
        or manifest["output_hashes"] != build.receipt["scientific_artifacts"]
        or manifest["code_commit"] != build.receipt["code_commit"]
        or manifest["input_artifacts"] != build.receipt["input_artifacts"]
    ):
        raise ValueError(f"Build does not match the immutable V3 release {release_id}")
    return manifest


def _replay_readback(run, current, release_id):
    receipt_data = (run / "replay_receipt.json").read_bytes()
    receipt = _object(receipt_data, "replay_receipt.json")
    if receipt["release_id"] != release_id:
        raise ValueError("Replay receipt belongs to another immutable V3 release")
    if receipt["offline"] is not True:
        raise ValueError("A replay audit requires cold offline execution")
    result = {
        "receipt_sha256": _digest(receipt_data), "reported_F4": receipt["F4"],
        "status": receipt["status"], "reason": receipt["reason"],
        "elapsed_seconds": receipt.get("elapsed_seconds"),
    }
    if receipt["status"] != "PASS":
        return result
    expected = current.receipt["scientific_artifacts"]
    actual = {name: _digest((run / "rebuilt" / name).read_bytes()) for name in ARTIFACTS}
    if (
        receipt["F4"] != "PASS" or expected != receipt["expected"] or actual != receipt["actual"]
        or actual != expected or receipt["compared_artifacts"] != len(actual) or receipt["differences"]
        or receipt["build_receipt_difference_fields"]
    ):
        raise ValueError("Offline replay receipt or reconstructed output does not match the current build")
    return {**result, "verified_outputs": actual, "verified_output_count": len(actual)}


def _build_lineage(build, release_id, manifest):
    receipt = build.receipt
    return {
        "archive_release_id": release_id, "observation_release_id": receipt["observation_release_id"],
        "code_commit": receipt["code_commit"], "code_archive_sha256": manifest.get("code_archive_sha256"),
        "build_receipt_sha256": build.receipt_sha, "inputs": receipt["input_artifacts"],
        "selection_rules_sha256": receipt["selection_rules_hash"],
        "criterion_policy_sha256": build.decision_inputs["policy_sha256"],
        "environment_sha256": manifest.get("environment_hash"),
        "environment_fingerprint": _digest(canonical_json(receipt["environment"]).encode("utf-8")),
        "outputs": receipt["scientific_artifacts"],
        "F0_F3": receipt["gates"],
    }


def _write_json(path, receipt):
    path.write_text(canonical_json(receipt) + "\n", encoding="utf-8")


def compare_research_builds(
    baseline: Path, current: Path, out: Path, *,
    baseline_release_id: str | None = None, current_release_id: str | None = None,
    release_root: Path | None = None, storage: ContentReplica | None = None,
    replay_runs: tuple[Path, ...] = (),
) -> dict:
    """Compare checked V3 builds and optionally audit exact archived cold-replay outputs."""
    baseline, current, out = (Path(directory).resolve() for directory in (baseline, current, out))
    if any(out.is_relative_to(directory) for directory in (baseline, current)):
        raise ValueError("Comparison outputs must be outside both build directories")
    if release_root is not None and out.is_relative_to(Path(release_root).resolve()):
        raise ValueError("Comparison outputs must be outside the immutable release root")
    if replay_runs and current_release_id is None:
        raise ValueError("Replay comparisons require the current archive release ID")
    if len({Path(run).resolve() for run in replay_runs}) != len(replay_runs):
        raise ValueError("Replay inputs must identify distinct run directories")
    initial, latest = ResearchBuild.load(baseline), ResearchBuild.load(current)
    comparable_fields = ("profile_id", "research_cutoff", "project_registry_hash", "census_columns_sha256")
    if (
        any(initial.receipt[key] != latest.receipt[key] for key in comparable_fields)
        or initial.census_columns != latest.census_columns or initial.census_projects != latest.census_projects
    ):
        raise ValueError("Research delta requires the same profile, cutoff, Project grid and headings")
    if any(initial.decision_inputs[key] != latest.decision_inputs[key] for key in ("policy_sha256", "policy_version", "policy")):
        raise ValueError("Research delta requires the same pinned decision policy")
    for version in ("point_method_version", "deterministic_sensitivity_version"):
        if initial.decision_results[version] != latest.decision_results[version]:
            raise ValueError(f"Research delta requires the same {version}")
    before_manifest = _bind_archive(initial, release_root, baseline_release_id, storage)
    after_manifest = _bind_archive(latest, release_root, current_release_id, storage)
    replay = [_replay_readback(Path(run), latest, current_release_id) for run in replay_runs]
    f4 = "NOT_RUN"
    if replay:
        statuses = {item["status"] for item in replay}
        f4 = "FAIL" if "FAIL" in statuses else "PASS" if statuses == {"PASS"} else "BLOCKED"
    replica = after_manifest.get("replica_inventory", {})
    f5 = "BLOCKED" if replica.get("status") == "BLOCKED" else "NOT_RUN"
    release_receipt = {
        "schema": SCHEMA, "before": _build_lineage(initial, baseline_release_id, before_manifest),
        "after": _build_lineage(latest, current_release_id, after_manifest),
        "F4": f4, "F5": f5, "replica_status": replica.get("status"),
        "replica_reason": replica.get("reason"), "replays": replay,
    }

    cells = _census_delta(initial, latest)
    comparability = _comparability_delta(initial, latest)
    panels = _cohort_and_mcda(initial, latest)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Comparison requires an empty output directory")
    with (out / "census_delta.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cells[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(cells)
    for name, payload in (
        ("comparability_delta.json", comparability), ("cohort_and_mcda.json", panels),
        ("release_and_replay.json", release_receipt),
    ):
        _write_json(out / name, payload)
    receipt = {
        "schema": SCHEMA, "status": "FAIL" if f4 == "FAIL" else "PASS",
        "profile_id": latest.receipt["profile_id"], "research_cutoff": latest.receipt["research_cutoff"],
        "compared_census_cells": len(cells), "census_change_counts": dict(Counter(row["change"] for row in cells)),
        "before_formal_census_cells": initial.receipt["scientific_output_count"],
        "after_formal_census_cells": latest.receipt["scientific_output_count"],
        "observation_counts": {"before": initial.receipt["observation_counts"], "after": latest.receipt["observation_counts"]},
        "before_comparable_project_criteria": comparability["before_count"],
        "after_comparable_project_criteria": comparability["after_count"],
        "net_comparable_project_criteria": comparability["net_comparable_project_criteria"],
        "target_status": comparability["target_status"],
        "panels": {name: {key: value[key] for key in ("after_eligible_count", "after_status")}
                   for name, value in panels["panels"].items()},
        "F4": f4, "F5": f5,
        "outputs_sha256": {
            name: _digest((out / name).read_bytes()) for name in ("census_delta.csv", "comparability_delta.json",
                                                                "cohort_and_mcda.json", "release_and_replay.json")
        },
    }
    _write_json(out / "comparison_receipt.json", receipt)
    return receipt
