"""Audit the frozen Stage 3 candidate and a Stage 4 fixed-corpus research build."""

import argparse
from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

STAGE3_ID = "47bcd58e568dc912f78c887ad83377adc891f81a472b3e71a266cf2e5abd60dd"
STAGE3_CODE = "1c44cab28602a5bcf15745f11f293dfb2b3f3aee"
START_CODE = "293cdfedea31874e0d757a7513695aaa77da30f0"
FIXED_SOURCE = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"
ARTIFACTS = {
    "census/census.csv", "census/cell_status.csv", "census/census_receipt.json",
    "decision/decision_receipt.json", "decision/mcda_inputs.json", "decision/mcda_results.json",
    "observations/extraction.json", "observations/observations.jsonl",
}
NUMERIC = {"FORMAL_SELECTION", "FORMAL_DERIVED"}
RELEASE_CONTEXT_KEYS = {"acceptance_id", "selection_id", "release_id", "profile_sha256"}
DERIVATION_CONTEXT_KEYS = {"assessment_id", "assessed_id", "derived_id", "derived_value_id"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha_file(path):
    digest = sha256()
    with path.open("rb") as stream:
        while block := stream.read(1 << 20):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def stable_hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def object_hashes(root, relative_files):
    return {name: sha_file(root / name) for name in sorted(relative_files)}


def read_census(root):
    with (root / "census/census.csv").open(encoding="utf-8", newline="") as stream:
        header, *wide = tuple(csv.reader(stream))
    with (root / "census/cell_status.csv").open(encoding="utf-8", newline="") as stream:
        ledger = list(csv.DictReader(stream))
    rows = {(row["project_id"], row["column"]): row for row in ledger}
    projects = [row[0] for row in wide]
    require(len(rows) == len(ledger) == 4743 and len(header) == 51 and len(projects) == len(set(projects)) == 93,
            f"Expected one ordered 93×51 Census ledger in {root}")
    for wide_row in wide:
        require(len(wide_row) == 51, f"Wrong Census row width in {root}")
        for column, value in zip(header, wide_row, strict=True):
            require(rows[wide_row[0], column]["value"] == value, f"Wide Census contradicts evidence ledger in {root}")
    return header, projects, rows


def field_evidence(row):
    return json.loads(row["metadata_evidence"]) if row["metadata_evidence"] else {}


def typed(row):
    return (row["evidence_state"] == "VALIDATED_DESCRIPTIVE"
            and field_evidence(row).get("evidence_type") == "SOURCE_BOUND_TEXT_ATTRIBUTE")


def remove_context(value, ignored):
    if isinstance(value, dict):
        return {key: remove_context(item, ignored) for key, item in value.items() if key not in ignored}
    if isinstance(value, list):
        return [remove_context(item, ignored) for item in value]
    return value


def normalized_mcda(root):
    content = {file: read(root / "decision" / file) for file in (
        "mcda_inputs.json", "mcda_results.json", "decision_receipt.json",
    )}
    files = root / "decision"
    first, last, receipt = content.values()
    input_hash, results_hash = (sha_file(files / name) for name in ("mcda_inputs.json", "mcda_results.json"))
    require(last["mcda_inputs_sha256"] == receipt["mcda_inputs_sha256"] == input_hash
            and receipt["mcda_results_sha256"] == results_hash, "MCDA receipt does not bind exact bytes")
    for row in (last, receipt):
        row.pop("mcda_inputs_sha256")
    receipt.pop("mcda_results_sha256")
    comparable = {(project, key) for project, criteria in first["criteria_by_project"].items()
                  for key, row in criteria.items() if row["state"] == "COMPARABLE"}
    require(Counter(key for _, key in comparable) == {"R1": 4, "S1": 2}, "Unexpected MCDA comparable criterion count")
    return remove_context(content, RELEASE_CONTEXT_KEYS), sorted(comparable)


def store_snapshot(root):
    path = (root / "observations/research.sqlite").resolve()
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        require(connection.execute("PRAGMA user_version").fetchone()[0] == 3, "Expected SQLite schema version 3")
        require(connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)], "SQLite integrity_check failed")
        require(not connection.execute("PRAGMA foreign_key_check").fetchall(), "SQLite foreign_key_check failed")
        schema = connection.execute("SELECT name, sql FROM sqlite_schema ORDER BY name").fetchall()
        fixed_tables = ("sources", "verified_sources", "entities", "scopes", "anchors", "relationships",
                        "raw_observations", "candidates", "validations", "temporal_decisions", "validation_anchors",
                        "semantic_assessments", "semantic_assessment_inputs", "semantic_assessment_anchors")
        snapshot = {table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr) for table in fixed_tables}
        for table in ("accepted_values", "derived_values"):
            ignored = RELEASE_CONTEXT_KEYS | DERIVATION_CONTEXT_KEYS
            records = [remove_context(json.loads(payload), ignored)
                       for (payload,) in connection.execute(f"SELECT payload FROM {table}")]
            snapshot[table] = sorted(records, key=stable_hash)
        snapshot["releases"] = [remove_context(json.loads(payload), {"release_id", "code_commit"})
                                for (payload,) in connection.execute("SELECT payload FROM releases ORDER BY release_id")]
        snapshot["release_projects"] = connection.execute("SELECT project_id FROM release_projects ORDER BY project_id").fetchall()
        snapshot["derived_inputs"] = connection.execute(
            """SELECT d.project_id, d.scope_id, json_extract(d.payload, '$.variable'), i.position, i.raw_record_id
               FROM derived_inputs i JOIN derived_values d USING (derived_id)
               ORDER BY d.project_id, d.scope_id, json_extract(d.payload, '$.variable'), i.position"""
        ).fetchall()
        return schema, snapshot
    finally:
        connection.close()


def archive_closure(release_root, release_id, cas, replay_roots, build):
    index = release_root / "releases" / f"{release_id}.json"
    require(re.fullmatch(r"[0-9a-f]{64}", release_id), "Expected a hexadecimal release SHA")
    require(sha_file(index) == release_id, "Release index bytes differ from the immutable identity")
    manifest = read(index)
    objects = {release_id, *manifest["required_object_sha256"]}
    require(len(cas) == 2 and len({path.resolve() for path in cas}) == 2, "Two distinct local CAS roots required")
    for pin in objects:
        require(re.fullmatch(r"[0-9a-f]{64}", pin), "Invalid archive object identifier")
        left, right = (root / "objects" / pin for root in cas)
        require(sha_file(left) == sha_file(right) == pin, f"Archive object bytes differ for {pin}")
        require(not left.samefile(right), f"Replica shares the same original file inode for {pin}")
    require(manifest["source_manifest_hash"] == FIXED_SOURCE, "Archive is outside the fixed 144-SHA cohort")
    artifacts = manifest["output_hashes"]
    require(set(artifacts) == ARTIFACTS, "Unexpected archive science artifact set")
    require(object_hashes(build, ARTIFACTS) == artifacts, "Build artifact bytes differ from the archive")
    require(sha_file(build / "build_receipt.json") == manifest["build_receipt_sha256"], "Build receipt differs")
    require(len(replay_roots) == 2 and len({path.resolve() for path in replay_roots}) == 2, "Two distinct offline runs required")
    for root in replay_roots:
        replay = read(root / "replay_receipt.json")
        result = (replay["status"], replay["offline"], replay["release_id"], replay["compared_artifacts"])
        require(result == ("PASS", True, release_id, 8), f"Invalid offline receipt {root}")
        require(replay["actual"] == replay["expected"] == artifacts and not replay["differences"],
                f"Cold manifest mismatch in {root}")
        require(object_hashes(root / "rebuilt", ARTIFACTS) == artifacts, f"Cold rebuilt bytes differ in {root}")
    return {"release_id": release_id, "code_commit": manifest["code_commit"], "archive_objects": len(objects),
            "source_and_proof_objects": len(manifest["source_and_evidence_sha256"]), "scientific_artifacts": artifacts,
            "replays": 2, "F5": manifest["replica_inventory"]["status"],
            "local_replica_same_filesystem": cas[0].stat().st_dev == cas[1].stat().st_dev}


def read_new_core_quotes(rows, captures, cas):
    if not rows:
        return 0
    require(cas is not None, "Independent Stage 4 core gains require the original source CAS")
    from au_ree_v3.documents import DocumentCache

    by_pdf = {}
    checked_captures = set()
    for row in rows:
        metadata = field_evidence(row)
        capture_sha = metadata["source_capture_sha256"]
        source = captures[capture_sha]
        if capture_sha not in checked_captures:
            pin_path = cas / "objects" / capture_sha
            require(sha_file(pin_path) == capture_sha and read(pin_path)["sha256"] == row["source_sha256"],
                    "A new core display source is not bound to its immutable capture")
            checked_captures.add(capture_sha)
        require(source["kind"] == "pdf", "Only fixed PDF core display gains are covered by this verifier")
        require(metadata["temporal_decision"]["status"] == "ELIGIBLE"
                and metadata["temporal_decision"]["decision_id"] == row["temporal_decision_id"],
                "New descriptor temporal selection conflicts with its evidence")
        by_pdf.setdefault(source["sha256"], {})
        for anchor in metadata["anchors"]:
            require(anchor["source_sha"] == source["sha256"] and anchor["page"] > 0 and anchor["quote"],
                    "New descriptor lacks an exact source quote")
            by_pdf[source["sha256"]][anchor["anchor_id"]] = anchor
    cache = DocumentCache()
    for pin, anchors in by_pdf.items():
        path = cas / "objects" / pin
        require(sha_file(path) == pin, "New core descriptor original no longer matches its source SHA")
        pdf = cache.pdf(path.read_bytes(), pin, last_page=max(anchor["page"] for anchor in anchors.values()))
        for anchor in anchors.values():
            quote = " ".join(anchor["quote"].split())
            page_text = pdf.pages[anchor["page"] - 1]
            span = anchor.get("text_span") or {}
            if span.get("view") == "NATIVE_PDF_LAYOUT_REGION":
                first, last, column = (span[key] for key in ("start_line", "end_line", "start_column"))
                lines = page_text.splitlines()
                require(1 <= first <= last <= len(lines) and column > 0, "Invalid native region boundaries")
                region = " ".join(" ".join(line[column - 1:] for line in lines[first - 1:last]).split())
                located = region[span["start"]:span["end"]]
            else:
                located = " ".join(page_text.split())
            require(quote in located, f"New core claim absent from the fixed page {pin}#{anchor['page']}")
    return sum(map(len, by_pdf.values()))


def visible_comparison(before, after, manifest, cas):
    columns, projects, baseline = read_census(before)
    after_columns, after_projects, candidate = read_census(after)
    require(columns == after_columns and projects == after_projects and baseline.keys() == candidate.keys(),
            "Original Census grid identity or order changed")
    previous = {key: row for key, row in baseline.items() if row["value_state"] == "PRESENT"}
    old_numeric = Counter(row["value_origin"] for row in previous.values() if row["value_origin"] in NUMERIC)
    require(len(previous) == 1122 and old_numeric == {"FORMAL_SELECTION": 16, "FORMAL_DERIVED": 3}
            and sum(typed(row) for row in previous.values()) == 11, "Prior Census metrics differ")
    old_context = {"release_id", "selection_id", "lineage_id", "metadata_evidence"}
    for key, row in previous.items():
        current = candidate[key]
        changes = [name for name in row if row[name] != current[name] and name not in old_context]
        require(not changes, f"Stage 4 changed an existing Census value or stable evidence in {key}: {changes}")
        if row["metadata_evidence"] != current["metadata_evidence"]:
            ignored = RELEASE_CONTEXT_KEYS | DERIVATION_CONTEXT_KEYS
            left, right = (remove_context(field_evidence(item), ignored) for item in (row, current))
            require(left == right, f"Stage 4 changed protected formula or display provenance in {key}")
    coverage = Counter(row["value_state"] for row in candidate.values())
    known_captures = {e["receipt_sha256"]: e for e in manifest["editions"]}
    additions, added_rows, assertion_ids = [], [], set()
    for key, row in candidate.items():
        prior = baseline[key]
        if prior["value_state"] == row["value_state"]:
            continue
        require(prior["value_state"] == "UNKNOWN" and row["value_state"] == "PRESENT" and typed(row),
                f"Unapproved core Census state transition in {key}")
        metadata = field_evidence(row)
        capture = known_captures.get(metadata["source_capture_sha256"])
        require(capture and capture["sha256"] == row["source_sha256"] and key[0] in capture["project_ids"],
                f"Core descriptor must link a fixed source capture owned by its Project in {key}")
        require(row["value_origin"] == "SOURCE_EVIDENCE" and row["temporal_status"] == "ELIGIBLE"
                and metadata["display_only"] is True and metadata["anchors"] and not row["observation_id"]
                and row["comparable"] in ("NO", ""), f"Invalid display evidence role in {key}")
        assertion = metadata["assertion"]["assertion_id"]
        assertion_ids.add(assertion)
        added_rows.append(row)
        additions.append({"project_id": key[0], "column": key[1], "value": row["value"],
                          "assertion_id": assertion, "source_sha256": row["source_sha256"]})
    meaningful = lambda rows: {project for (project, _), row in rows.items()
                               if row["value_origin"] in NUMERIC or typed(row)}
    previous_reach, current_reach = meaningful(baseline), meaningful(candidate)
    verified_anchors = read_new_core_quotes(added_rows, known_captures, cas)
    return {"core_present_before": len(previous), "core_present_after": coverage["PRESENT"], "denominator": 4743,
            "prior_scientific": dict(old_numeric), "prior_descriptive": 11, "new_core_cells": additions,
            "proof_replayed_new_core_pdf_anchors": verified_anchors, "new_assertions": len(assertion_ids), "new_census_projects": len({row["project_id"] for row in additions}),
            "meaningful_project_reach_before": len(previous_reach), "meaningful_project_reach_after": len(current_reach),
            "additional_reach_project_ids": sorted(current_reach - previous_reach)}


def compare(before, after, manifest_path, cas=None):
    require(sha_file(manifest_path) == FIXED_SOURCE, "Expected fixed original manifest")
    manifest = read(manifest_path)
    require(len({row["sha256"] for row in manifest["editions"]}) == 144
            and sum(row["temporal_decision"]["status"] == "ELIGIBLE" for row in manifest["editions"]) == 148,
            "Fixed cohort acquisition counts differ")
    decision, keys = normalized_mcda(before)
    require(normalized_mcda(after) == (decision, keys), "MCDA science, policy, provenance, ranks, weights or sensitivity changed")
    old_schema, old_data = store_snapshot(before)
    new_schema, new_data = store_snapshot(after)
    require(old_schema == new_schema, "Stage 4 modified SQLite schema")
    changed = [table for table, baseline in old_data.items() if baseline != new_data[table]]
    require(not changed, f"Stage 4 changed stored research: {changed}")
    read_names = lambda root: [remove_context(json.loads(line), RELEASE_CONTEXT_KEYS)
                               for line in (root / "observations/observations.jsonl").read_text().splitlines()]
    require(read_names(before) == read_names(after), "Original observations/readback artifacts changed")
    return {"status": "PASS", "fixed_source_manifest": FIXED_SOURCE, "source_sha256_count": 144,
            "eligible_capture_count": 148, "mcda_stable_science_sha256": stable_hash(decision), "mcda_keys": keys,
            "sqlite_integrity": "PASS", "sqlite_schema": 3, "sqlite_foreign_keys": "PASS",
            "immutable_table_row_counts": {table: len(rows) for table, rows in old_data.items()},
            "core": visible_comparison(before, after, manifest, cas)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "candidate", "manifest", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--cas", type=Path, help="Fixed original CAS. Required when core display cells were added.")
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument("--release-id")
    parser.add_argument("--candidate-stores", type=Path, nargs=2, metavar=("PRIMARY", "LOCAL_COPY"))
    parser.add_argument("--replays", type=Path, nargs=2, metavar=("COLD_A", "COLD_B"))
    args = parser.parse_args()
    archive_parameters = (args.archive_root, args.release_id, args.candidate_stores, args.replays)
    if any(archive_parameters) and not all(archive_parameters):
        parser.error("--archive-root, --release-id, --candidate-stores and --replays are required together")
    report = compare(args.baseline, args.candidate, args.manifest, args.cas)
    if args.archive_root is not None:
        report["archive"] = archive_closure(args.archive_root, args.release_id, args.candidate_stores, args.replays, args.candidate)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "out": str(args.out), "new_core_cells": len(report["core"]["new_core_cells"])}))


if __name__ == "__main__":
    main()
