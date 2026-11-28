"""Independently verify the fixed 144-source Stage 3 build and two cold replays."""

import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

ARTIFACTS = {
    "census/cell_status.csv", "census/census.csv", "census/census_receipt.json",
    "decision/decision_receipt.json", "decision/mcda_inputs.json", "decision/mcda_results.json",
    "observations/extraction.json", "observations/observations.jsonl",
}
NUMERIC = {"FORMAL_SELECTION", "FORMAL_DERIVED"}
PINNED_SOURCE = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        hash_object = sha256()
        while content := stream.read(1 << 20):
            hash_object.update(content)
    return hash_object.hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def census_rows(root):
    with (root / "census/cell_status.csv").open(newline="", encoding="utf-8") as stream:
        cells = list(csv.DictReader(stream))
    rows = {(row["project_id"], row["column"]): row for row in cells}
    require(len(rows) == len(cells) == 4743, f"Expected 4,743 unique Census keys in {root}")
    return rows


def evidence(row):
    return json.loads(row["metadata_evidence"]) if row["metadata_evidence"] else {}


def is_attribute(row):
    return (row["evidence_state"] == "VALIDATED_DESCRIPTIVE"
            and evidence(row).get("evidence_type") == "SOURCE_BOUND_TEXT_ATTRIBUTE")


def comparable(root):
    inputs = read(root / "decision/mcda_inputs.json")
    results = read(root / "decision/mcda_results.json")
    matrices = {key: {name: panel[name] for name in ("eligible_projects", "benefit_matrix", "scenario_weights")}
                for key, panel in inputs["panels"].items()}
    criteria = {(project, criterion): {key: row[key] for key in ("accepted_value", "utility", "unit", "reason")}
                for project, rows in inputs["criteria_by_project"].items() for criterion, row in rows.items()
                if row["state"] == "COMPARABLE"}
    return inputs["policy_sha256"], criteria, matrices, results["panels"]


def compare_fields(previous, current, fields, identity):
    mismatch = sorted(key for key in fields if previous[key] != current[key])
    require(not mismatch, f"Protected lineage differs for {identity}: {mismatch}")


def classify_change(previous, current):
    if previous["value_state"] != "PRESENT" and current["value_state"] == "PRESENT":
        return "added"
    if previous["value_state"] == "PRESENT" and current["value_state"] != "PRESENT":
        return "removed"
    return "changed"


def check_previous(before, after):
    left, right = census_rows(before), census_rows(after)
    require(left.keys() == right.keys(), "Stage 3 Census changed the fixed cohort or column keys")
    numeric = {key for key, row in left.items() if row["value_origin"] in NUMERIC}
    attributes = {key for key, row in left.items() if is_attribute(row)}
    require((len(numeric), len(attributes)) == (18, 5), "Baseline lacks the fixed 18 numeric and 5 typed cells")
    common = {
        "value", "column_id", "source_sha256", "physical_page", "scope", "temporal_decision_id",
        "temporal_status", "value_state", "value_origin",
    }
    quantitative = {
        "raw_value", "raw_unit", "unit", "precision", "statement_id", "effective_date", "category", "formula_id",
        "formula_sha256", "input_observation_ids", "observation_id", "source_table", "source_row", "source_column",
        "locator", "comparable",
    }
    typed = {
        "attribute_kind", "attribute_role", "publication_date", "event_date_token", "native_value", "display_only",
        "source_capture_sha256", "anchors",
    }
    for key in numeric | attributes:
        compare_fields(left[key], right[key], common | quantitative if key in numeric else common, key)
        if key in attributes:
            compare_fields(evidence(left[key]), evidence(right[key]), typed, key)
    ratings = comparable(before)
    require(len(ratings[1]) == 6 and ratings == comparable(after), "Fixed MCDA criterion use or results changed")
    deltas, details, added_projects = Counter(), [], set()
    for key, prior in left.items():
        current = right[key]
        if (prior["value_state"], prior["value"]) == (current["value_state"], current["value"]):
            continue
        mode = classify_change(prior, current)
        deltas[mode, current["value_origin"] or prior["value_origin"]] += 1
        if mode == "added":
            added_projects.add(key[0])
        details.append({"project_id": key[0], "column": key[1], "change": mode, "origin": current["value_origin"],
                        "before": prior["value"], "after": current["value"], "source_sha256": current["source_sha256"]})
    return {
        "protected_scientific": len(numeric), "protected_attributes": len(attributes), "comparable_keys": len(ratings[1]),
        "changed_cells_by_kind": [{"change": kind, "origin": origin, "count": count}
                                  for (kind, origin), count in sorted(deltas.items())],
        "changed_cells": details, "new_project_count": len(added_projects),
    }


def check_store(build):
    database_url = (build / "observations/research.sqlite").resolve().as_uri() + "?mode=ro&immutable=1"
    tables = ("sources", "anchors", "raw_observations", "candidates", "validations", "accepted_values", "derived_values")
    with sqlite3.connect(database_url, uri=True) as connection:
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity check failed")
        require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign keys failed")
        return {"integrity_check": "PASS", "foreign_key_check": "PASS",
                "schema": connection.execute("PRAGMA user_version").fetchone()[0],
                "rows": {table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables}}


def verify_closure(cas, pins):
    for pin in pins:
        require(re.fullmatch(r"[a-f0-9]{64}", pin), f"Invalid archive object SHA-256 {pin!r}")
        require(digest(cas / "objects" / pin) == pin, f"Release object bytes differ from {pin}")
    return len(pins)


def verify_replay(replay, identifier, pins):
    receipt = read(replay / "replay_receipt.json")
    status = (receipt["status"], receipt["F4"], receipt["offline"], receipt["compared_artifacts"])
    require(status == ("PASS", "PASS", True, 8), f"Cold replay not verified for {replay}")
    require(receipt["release_id"] == identifier and receipt["actual"] == receipt["expected"] == pins,
            f"Cold replay pins differ for {replay}")
    require(not receipt["differences"] and not receipt["build_receipt_difference_fields"],
            f"Cold replay reports scientific differences for {replay}")


def audit(release, build, baseline, cas, replays, replica_cas=None):
    identifier = release.stem
    require(identifier == digest(release), "Release index does not match its manifest SHA-256")
    manifest = read(release)
    require(manifest["source_manifest_hash"] == PINNED_SOURCE, "Expected the fixed Stage 3 source universe")
    closure = manifest["required_object_sha256"]
    pins = {identifier, *closure}
    verify_closure(cas, pins)
    local_replica = None
    if replica_cas is not None:
        require(cas.resolve() != replica_cas.resolve(), "Replica must have a separate directory")
        local_replica = {"verified_objects_including_release": verify_closure(replica_cas, pins),
                         "same_filesystem": cas.stat().st_dev == replica_cas.stat().st_dev}
    expected = manifest["output_hashes"]
    require(set(expected) == ARTIFACTS, "Unexpected scientific artifact paths")
    receipt_path = build / "build_receipt.json"
    receipt = read(receipt_path)
    require(digest(receipt_path) == manifest["build_receipt_sha256"], "Live build receipt hash differs")
    require(receipt["scientific_artifacts"] == expected and receipt["code_commit"] == manifest["code_commit"],
            "Live build artifacts or commit differ from archive")
    require(receipt["gates"] == manifest["build_gates"], "Live build gates differ from archive")
    require(receipt["verified_source_count"] == receipt["eligible_source_count"] == 144,
            "The fixed 144 originals were not all verified and eligible")
    require(len(replays) == len({path.resolve() for path in replays}) == 2, "Two distinct cold replay folders required")
    for replay in replays:
        verify_replay(replay, identifier, expected)
    for folder in (build, *(replay / "rebuilt" for replay in replays)):
        require({file: digest(folder / file) for file in ARTIFACTS} == expected, f"Artifact bytes differ in {folder}")
    return {
        "status": "PASS", "release_id": identifier, "code_commit": manifest["code_commit"],
        "closure_objects": len(closure), "source_and_evidence_objects": len(manifest["source_and_evidence_sha256"]),
        "scientific_artifacts": expected, "runs_verified": len(replays), "sqlite": check_store(build),
        "comparison": check_previous(baseline, build), "input_source_manifest": PINNED_SOURCE,
        "local_replica_readback": local_replica, "F5": manifest["replica_inventory"]["status"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("release", "build", "baseline", "cas", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--replays", type=Path, nargs=2, required=True)
    parser.add_argument("--replica-cas", type=Path)
    args = parser.parse_args()
    result = audit(args.release, args.build, args.baseline, args.cas, args.replays, args.replica_cas)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "release_id": result["release_id"], "out": str(args.out)}))


if __name__ == "__main__":
    main()
