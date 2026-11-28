"""Verify exact Census research baselines and freeze their 4,743-cell gain funnel."""

import argparse
from collections import Counter, defaultdict
import csv
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from au_ree_v3.census_science import _GROUPS, _component_columns
from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_research import verify_source_manifest
from au_ree_v3.publication import load_release
from au_ree_v3.registry import load_profile, load_registry
from au_ree_v3.research_delta import ResearchBuild, compare_research_builds
from au_ree_v3.sources import ContentStore

MAIN = "074e00daf69360cd71cab2554f651b4b6e76a668"
ARCHIVED_CODE = "d114c388440ad7db9b1b3de25d3e7646d4a84953"
RELEASE = "de50379f50e49fdd74ac536deb51b8b03dade8f442565644564c5cef1f2a17ef"
SOURCE_SHA = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"
PRIOR = "docs/research/scientific_value_admission_receipt.json"
PRIOR_SHA = "c07d56fdceb5df173724813c6b9e19d9650f0d3d7b08d00a3622235e0ea39169"
SOURCES = "work/research-yield/source-inputs/source_manifest.json"
OLD = "work/scientific-yield-independent/revision-d114c388"
INVENTORY = "work/scientific-admission/inventory-verified-a"
BINDING = "work/scientific-admission/binding-b-6fdb97f-run1"
HOLDOUTS = (
    ("raw-binding-10", "work/scientific-admission/heldout-family-review/annotations_v1.json",
     "840cf4ce2cc3330af8772d2b8593c050fab50e59069b694610c9ee5e2a77705c"),
    ("raw-binding-31", "work/scientific-yield-independent/holdout_oracle.json",
     "ad27468ea501eb060e37cc0370e7861110477b074149864305a91176f1ca26e2"),
)
RELEASE_FIELDS = {"release_id", "selection_id", "derivation_id"}
FUNNEL_FIELDS = (
    "project_id", "column_id", "column", "value", "value_state", "value_origin", "evidence_state",
    "funnel_stage", "reason", "stage_reason", "observation_id", "source_sha256", "scope_id", "statement_id",
    "baseline_lineage_id", "shadow_or_conflict_key", "new_raw_observation", "new_formal_derived_cell",
)


def digest(payload):
    return sha256(payload).hexdigest()


def require(test, reason):
    if not test:
        raise ValueError(reason)


def checked_bytes(path, expected):
    data = path.read_bytes()
    require(digest(data) == expected, f"Pinned input differs: {path.name}")
    return data


def read_json(path):
    return json.loads(path.read_bytes())


def write_json(path, value):
    path.write_text(canonical_json(value) + "\n", encoding="utf8")


def release_independent(value):
    if isinstance(value, dict):
        return {key: release_independent(item) for key, item in value.items() if key not in RELEASE_FIELDS}
    if isinstance(value, list):
        return [release_independent(item) for item in value]
    return value


def freeze_holdouts(workspace, out, manifest, prior_sha):
    results = {}
    editions = defaultdict(set)
    for row in manifest["editions"]:
        editions[row["sha256"]].update(row["project_ids"])
    for name, relative, expected in HOLDOUTS:
        original = checked_bytes(workspace / relative, expected)
        value = json.loads(original)
        manifest = value.get("source_manifest_sha256", value.get("manifest_sha256"))
        require(manifest == SOURCE_SHA, "Holdout annotations have a different source inventory")
        families = []
        for row in value["cases"]:
            families.append({
                "issuer": row.get("issuer", row.get("issuer_family")),
                "document_family": row.get("report_family", row.get("case")),
                "project_ids": sorted([row.get("project_id", row.get("project")), *row.get("related_project_ids", ())]),
                "source_sha256": row.get("source_sha256", row.get("source")),
            })
        for family in families:
            require(all(family.values()), "Holdout family identity is incomplete")
            require(set(family["project_ids"]) <= editions[family["source_sha256"]],
                    "Holdout Project identities do not match the frozen exact edition")
        family_hash = digest(canonical_json(sorted(families, key=canonical_json)).encode())
        stored = out / "frozen_holdouts" / f"{name}.json"
        stored.parent.mkdir(exist_ok=True)
        stored.write_bytes(original)
        results[name] = {
            "annotations_sha256": expected, "issuer_project_family_sha256": family_hash,
            "documents": len(families), "prior_scoring": prior_sha[name],
            "relative_frozen_annotations": f"frozen_holdouts/{name}.json",
        }
    return results


def prior_binding(workspace, prior):
    expected = {row["path"]: row["sha256"] for row in prior["artifacts"]}
    inventory_path = f"{INVENTORY}/source_case_inventory_receipt.json"
    binding_path = f"{BINDING}/table_binding_receipt.json"
    inventory = json.loads(checked_bytes(workspace / inventory_path, expected[inventory_path]))
    binding = json.loads(checked_bytes(workspace / binding_path, expected[binding_path]))
    require(binding["inputs"]["source_case_inventory_receipt.json"] == expected[inventory_path]
            and binding["inputs"]["source_manifest.json"] == SOURCE_SHA,
            "Historical table binding does not bind the inventory receipt and fixed manifest")
    for name, fingerprint in inventory["output_sha256"].items():
        checked_bytes(workspace / INVENTORY / name, fingerprint)
    for name, fingerprint in binding["outputs"].items():
        checked_bytes(workspace / BINDING / name, fingerprint)
    records = {
        name: [json.loads(line) for line in (workspace / BINDING / name).read_bytes().splitlines()]
        for name in ("typed_candidate_observations.jsonl", "unbound_numeric_tokens.jsonl")
    }
    typed, unbound = records.values()
    reasons = Counter(row["reason"] for row in unbound)
    unrecognized = inventory["kind_counts"]["UNRECOGNIZED_SOURCE_PROJECT"]
    counts = {
        "distinct_original_number_positions": binding["distinct_native_source_positions"],
        "resolved_original_number_positions": binding["distinct_resolved_native_positions"],
        "typed_candidates_after_exact_cell_dedup": len(typed),
        "unbound_scoped_sightings": len(unbound),
        "unbound_reasons": dict(reasons),
        "unrecognized_source_project_work_items": unrecognized,
    }
    require(counts == {
        "distinct_original_number_positions": 298, "resolved_original_number_positions": 103,
        "typed_candidates_after_exact_cell_dedup": 110, "unbound_scoped_sightings": 225,
        "unbound_reasons": {"HEADER_OR_UNIT_UNBOUND": 210, "AMBIGUOUS_OR_CONFLICTING_COLUMN_ALIGNMENT": 13,
                            "NATIVE_TOKEN_NOT_A_UNIQUE_NUMERIC_CELL": 2},
        "unrecognized_source_project_work_items": 19,
    }, "Pinned inventory or binding funnel does not match observed rows")
    existing = binding["existing_reviewed_exact_cell_overlaps"]
    return {**counts, "existing_reviewed_exact_cell_overlaps": existing,
            "inventory_receipt_sha256": expected[inventory_path], "table_binding_receipt_sha256": expected[binding_path],
            "pdf_reread": "NOT_RUN_REUSED_EXACT_RECEIPTS"}


def scientific_comparison(before_dir, after_dir, before, after):
    require(before.observations == after.observations, "Selected original observations changed")
    for name, extra in (
        ("census/census_receipt.json", ("outputs",)),
        ("observations/extraction.json", ()),
        ("decision/mcda_results.json", ("mcda_inputs_sha256",)),
        ("decision/mcda_inputs.json", ()),
    ):
        left, right = [read_json(path / name) for path in (before_dir, after_dir)]
        require(release_independent({key: value for key, value in left.items() if key not in extra})
                == release_independent({key: value for key, value in right.items() if key not in extra}),
                f"Scientific content changed beyond release-scoped identities: {name}")
    with tempfile.TemporaryDirectory(prefix="census-baseline-delta-") as work:
        delta = compare_research_builds(before_dir, after_dir, Path(work))
    require(delta["census_change_counts"] == {"UNCHANGED": 4723, "PROVENANCE_RECEIPT_CHANGED": 20},
            "Census has changes beyond its 20 release-scoped provenance cells")
    for key, current in after.census.items():
        old = before.census[key]
        for field in ("value", "source_sha256", "observation_id", "scope_id", "statement_id", "effective_date",
                      "temporal_status", "reason", "value_origin", "evidence_state"):
            require(old[field] == current[field], f"Scientific cell identity or source changed: {key[1]}/{field}")
        require(release_independent(json.loads(old["metadata_evidence"] or "{}"))
                == release_independent(json.loads(current["metadata_evidence"] or "{}")),
                f"Census metadata changed beyond release-scoped identities: {key[1]}")
    return {
        "compared_census_cells": delta["compared_census_cells"],
        "census_change_counts": delta["census_change_counts"],
        "byte_identical_artifacts": sorted(name for name, value in before.receipt["scientific_artifacts"].items()
                                          if after.receipt["scientific_artifacts"][name] == value),
        "science_and_mcda_equivalent": True,
    }


def pending_science(science, columns):
    pending = {}
    heading = {key: value[1] for key, value in _GROUPS.items()}
    heading.update({f"{species.casefold()}_ppm": column for species, column in _component_columns(columns).items()})
    for row in science["derived_candidates"]:
        require(row["formal_admission"] is False, "Historical formal derived value is out of baseline scope")
        heading[row["key"]] = row["census_column"]
        pending[row["project_id"], row["census_column"]] = ("SHADOW", row["key"], row["formal_admission_reason"])
    contained, = (column for column in columns if column == "總TREO蘊藏量_kt")
    for project, view in science["scoped_reported_resources"].items():
        shadow = view["contained_shadow"]
        if shadow.get("layer") == "SHADOW" and shadow.get("value") is not None:
            pending[project, contained] = ("SHADOW", shadow["shadow_id"], shadow["reason"])
    for row in science["rejected_derivations"]:
        if row["status"] == "SCIENTIFIC_CONFLICT" and row["key"] in heading:
            pending[row["project_id"], heading[row["key"]]] = ("SCIENTIFIC_CONFLICT", row["key"], row["reason"])
    return pending


def funnel_stage(cell, pending=None):
    state, evidence = cell["value_state"], cell["evidence_state"]
    reason = cell["reason"]
    if state == "PRESENT":
        return {
            "FORMAL_DIRECT": "REPORTED_DIRECT", "FORMAL_DERIVED": "CENSUS_ADMISSIBLE_DERIVED",
            "DESCRIPTIVE_ONLY": "VALIDATED_DESCRIPTIVE",
        }[evidence], "", reason
    if state == "NOT_APPLICABLE":
        require(reason != "NO_SELECTED_MRE", "Missing selection cannot be true not-applicable")
        return "TRUE_NOT_APPLICABLE", "", reason
    require(state == "UNKNOWN", "Unrecognized Census state")
    if pending:
        return pending
    if evidence == "SCIENTIFIC_CONFLICT":
        return evidence, "", reason
    if evidence in {"NOT_LOCATED", "SOURCE_UNAVAILABLE"} or reason in {
        "SOURCE_ATTRIBUTION_NOT_RECORDED", "LISTING_CODE_NOT_RECORDED_IN_SNAPSHOT",
    }:
        return "NOT_LOCATED", "", reason
    return "EVIDENCE_INSUFFICIENT", "", reason


def write_funnel(out, build, science, all_observations):
    pending = pending_science(science, build.census_columns)
    stages, origins = Counter(), Counter()
    per_source = defaultdict(lambda: {"selected_original_observation_ids": set(), "projected_science_cells": 0,
                                      "all_original_observation_ids": set(), "unselected_original_observations": 0})
    missing_by_project = Counter()
    projected = set()
    for identity, observation in build.observations.items():
        per_source[observation["source_sha256"]]["selected_original_observation_ids"].add(identity)
    for observation in all_observations:
        source = per_source[observation["source_sha256"]]
        source["all_original_observation_ids"].add(observation["observation_id"])
        source["unselected_original_observations"] += observation["observation_id"] not in build.observations
    with out.open("x", newline="", encoding="utf8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FUNNEL_FIELDS, lineterminator="\n")
        writer.writeheader()
        for project in build.census_projects:
            for heading in build.census_columns:
                cell = build.census[project, heading]
                stage, issue, why = funnel_stage(cell, pending.get((project, heading)))
                identity = cell["observation_id"]
                if stage == "REPORTED_DIRECT":
                    projected.add(identity)
                    per_source[cell["source_sha256"]]["projected_science_cells"] += 1
                stages[stage] += 1
                if cell["value_state"] == "PRESENT":
                    origins[cell["value_origin"]] += 1
                else:
                    missing_by_project[project] += 1
                writer.writerow({
                    **{key: cell[key] for key in FUNNEL_FIELDS if key in cell},
                    "funnel_stage": stage, "stage_reason": why, "shadow_or_conflict_key": issue,
                    "baseline_lineage_id": cell["lineage_id"], "new_raw_observation": "NO", "new_formal_derived_cell": "NO",
                })
    return {
        "cells": sum(stages.values()), "stages": dict(stages), "present_origins": dict(origins),
        "unique_directly_projected_observations": len(projected),
        "by_source_sha256": {
            key: {**row, "all_original_observation_ids": sorted(row["all_original_observation_ids"]),
                  "selected_original_observation_ids": sorted(row["selected_original_observation_ids"]),
                  "selected_observations_without_direct_grid_cell": len(row["selected_original_observation_ids"] - projected),
                  "unique_selected_original_observations": len(row["selected_original_observation_ids"])}
            for key, row in sorted(per_source.items())
        },
        "missing_cells_by_project": dict(missing_by_project),
        "new_raw_observations_this_stage": 0, "new_formal_derived_cells_this_stage": 0,
    }


def _assemble(workspace, after_dir, cold, out):
    source_bytes = checked_bytes(workspace / SOURCES, SOURCE_SHA)
    manifest = json.loads(source_bytes)
    prior = json.loads(checked_bytes(workspace / PRIOR, PRIOR_SHA))
    old = workspace / OLD
    archive = load_release(old / "archive", RELEASE, ContentStore(old / "cas"))
    before = ResearchBuild.load(old / "build")
    after = ResearchBuild.load(after_dir)
    require((before.receipt["code_commit"], archive["code_commit"], after.receipt["code_commit"]) ==
            (ARCHIVED_CODE, ARCHIVED_CODE, MAIN), "Builds were not executed for the fixed Stage 2 revisions")
    require(before.receipt_sha == archive["build_receipt_sha256"] and
            ContentStore(old / "cas").read(RELEASE) == (old / "archive" / "releases" / f"{RELEASE}.json").read_bytes(),
            "Historical candidate does not match its exact archive")
    receipt = read_json(cold / "replay_receipt.json")
    rebuilt = {name: digest((cold / "rebuilt" / name).read_bytes()) for name in before.receipt["scientific_artifacts"]}
    require(receipt["release_id"] == RELEASE and receipt["offline"] is True and receipt["F4"] == "PASS" and
            archive["output_hashes"] == before.receipt["scientific_artifacts"] == receipt["expected"] ==
            receipt["actual"] == rebuilt and not receipt["build_receipt_difference_fields"] and not receipt["differences"],
            "Archived candidate was not independently cold replayed and read back")
    require(before.receipt["input_artifacts"] == after.receipt["input_artifacts"] and
            after.receipt["input_artifacts"]["sources"] == SOURCE_SHA and before.receipt["environment"] ==
            after.receipt["environment"], "Baseline inputs or runtime differ across verified revisions")
    profile = load_profile(workspace / "config/research_profiles/au_ree_2026-11-28.json")
    sources = verify_source_manifest(profile, manifest, load_registry(profile.registry_path),
                                     ContentStore(workspace / "work/fresh/source-store"))
    require(sources["status"] == "PASS" and len(sources["verified_sha256"]) == len(sources["eligible_sha256"]) == 144
            and len(sources["verified_capture_sha256"]) == 148 and sources["projects"] == 93,
            "Source, Project or exact public capture scope differs from the lock")
    comparison = scientific_comparison(old / "build", after_dir, before, after)
    routing = prior_binding(workspace, prior)
    science = read_json(after_dir / "census/census_receipt.json")["science_views"]
    out.mkdir(parents=True)
    funnel_path = out / "census_gain_funnel.csv"
    all_observations = [json.loads(line) for line in (after_dir / "observations/observations.jsonl").read_bytes().splitlines()]
    require(len({row["observation_id"] for row in all_observations}) == len(all_observations) ==
            after.receipt["observation_counts"]["candidate_count"], "Original candidate Observation IDs are not unique")
    funnel = write_funnel(funnel_path, after, science, all_observations)
    expected = prior["checks"]["independent_holdout_auto_binding"]
    holdouts = freeze_holdouts(workspace, out, manifest,
                               {name: row for (name, _, _), row in zip(HOLDOUTS, expected, strict=True)})
    observations = after.receipt["observation_counts"]
    criteria = Counter(code for cells in after.decision_inputs["criteria_by_project"].values()
                       for code, cell in cells.items() if cell["state"] == "COMPARABLE")
    panel = after.decision_results["panels"]["issuer_resource_composition"]
    counts = {"projects": len(after.census_projects), "columns": len(after.census_columns), "cells": len(after.census),
              "present_cells": sum(funnel["present_origins"].values()),
              "reported_science_cells": funnel["stages"].get("REPORTED_DIRECT", 0),
              "formal_derived_cells": funnel["stages"].get("CENSUS_ADMISSIBLE_DERIVED", 0),
              "candidate_observations": observations["candidate_count"], "validated_observations": observations["validated_count"],
              "selected_observations": observations["selected_count"], "comparable_keys": dict(criteria),
              "complete_r1_s1_projects": panel["eligible_count"]}
    require(counts == {"projects": 93, "columns": 51, "cells": 4743, "present_cells": 1108,
                       "reported_science_cells": 16, "formal_derived_cells": 0, "candidate_observations": 42,
                       "validated_observations": 27, "selected_observations": 21, "comparable_keys": {"R1": 4, "S1": 2},
                       "complete_r1_s1_projects": 2}, "Fresh baseline census, admission or MCDA counts differ")
    baseline = {
        "schema": "census-stage2-baseline-v1", "status": "PASS", "counts": counts,
        "source_manifest_sha256": SOURCE_SHA, "source_verification": sources,
        "research_cutoff": manifest["cutoff"], "timezone": manifest["timezone"],
        "main_code_commit": MAIN, "archived_code_commit": ARCHIVED_CODE, "archived_release_id": RELEASE,
        "main_observation_release_id": after.receipt["observation_release_id"],
        "main_build_receipt_sha256": after.receipt_sha, "archive_build_receipt_sha256": before.receipt_sha,
        "main_scientific_artifact_sha256": after.receipt["scientific_artifacts"],
        "archive_scientific_artifact_sha256": archive["output_hashes"],
        "research_inputs_sha256": after.receipt["input_artifacts"],
        "policy_sha256": {"projection": after.receipt["census_projection_policy_sha256"],
                          "selection": after.receipt["selection_rules_hash"], "formula_registry": science["formula_registry_sha256"],
                          "mcda": after.decision_inputs["policy_sha256"]},
        "environment": after.receipt["environment"], "current_vs_archived": comparison,
        "gates": {**after.receipt["gates"], "F4": "PASS", "F5": archive["replica_inventory"]["status"],
                  "historical_O2": prior["gates"]["historical_O2"]},
        "previous_source_to_binding": routing, "holdouts": holdouts,
        "funnel": {**funnel, "sha256": digest(funnel_path.read_bytes())},
    }
    write_json(out / "stage2_baseline.json", baseline)
    return baseline


def build_baseline(workspace, after_dir, cold, out):
    if out.exists() or out.is_symlink():
        raise FileExistsError("Baseline output directory already exists")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".census-stage2-", dir=out.parent) as temporary:
        staged = Path(temporary) / "baseline"
        report = _assemble(workspace, after_dir, cold, staged)
        staged.rename(out)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("workspace", "main-build", "archived-cold-replay", "out"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    report = build_baseline(args.workspace, args.main_build, args.archived_cold_replay, args.out)
    print(canonical_json({"status": report["status"], "counts": report["counts"],
                          "baseline_sha256": digest((args.out / "stage2_baseline.json").read_bytes()),
                          "funnel_sha256": report["funnel"]["sha256"]}))
