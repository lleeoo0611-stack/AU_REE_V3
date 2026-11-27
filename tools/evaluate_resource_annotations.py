"""Compare real resource observations to independent transcriptions of frozen source bytes."""

import argparse
from collections import Counter
from decimal import Decimal
from hashlib import file_digest, sha256
import json
from pathlib import Path


PARTITIONS = ("DESIGN_REGRESSION", "INDEPENDENT_HOLDOUT")


def identity(row):
    return row["project_id"], row["source_sha256"]


def _in_window(record, expected):
    return (record["variable"] == expected["variable"] and record["page"] in expected["pages"]
            and all(record[field] == expected[field] for field in ("table", "row") if field in expected))


def _same_measurement(record, expected):
    return (record["raw_value"] == expected["raw_value"] and record["unit"] == expected["unit"]
            and Decimal(record["value"]) == Decimal(expected["value"]))


def _check_split(cases):
    ids = set()
    keys = {partition: (set(), set()) for partition in PARTITIONS}
    for case in cases:
        case_id = case["case_id"]
        if case_id in ids:
            raise ValueError(f"Duplicate annotation case {case_id}")
        ids.add(case_id)
        projects, sources = keys[case["split"]]
        project, source = identity(case)
        projects.add(project)
        sources.add(source)
    design, holdout = (keys[part] for part in PARTITIONS)
    if design[0] & holdout[0] or design[1] & holdout[1]:
        raise ValueError("Holdout overlaps the design cases by Project ID or source SHA")


def _check_source(root, source):
    path = root / source
    if not path.is_file():
        return "BLOCKED"
    with path.open("rb") as stream:
        return "PASS" if file_digest(stream, "sha256").hexdigest() == source else "FAIL"


def _case_result(case, records, attempted, integrity):
    expected = case["expected_observations"]
    result = {
        "case_id": case["case_id"], "split": case["split"],
        "project_id": case["project_id"], "source_sha256": case["source_sha256"],
        "source_integrity": integrity, "expected_observations": len(expected),
    }
    if integrity != "PASS" or not attempted:
        return {**result, "status": integrity if integrity != "PASS" else "NOT_RUN",
                "reason": "Source missing or mismatched" if integrity != "PASS" else "No V3 extraction case attempted this Project and source"}

    indexed = list(enumerate(records))
    predictions = {
        index for index, record in indexed
        if any(_in_window(record, window) for window in expected)
    }
    used = set()
    missing = []
    selected_expected = set()
    for label in expected:
        match = next((i for i, row in indexed if i in predictions - used and _in_window(row, label)
                      and _same_measurement(row, label)
                      and ("effective_date" not in case or row.get("effective_date") == case["effective_date"])), None)
        if match is None:
            missing.append(label)
        else:
            used.add(match)
            if label["selected"]:
                selected_expected.add(match)

    selected = {i for i, row in indexed if row["selected_for_census"]}
    missing_selections = sum(label["selected"] for label in missing) + len(selected_expected - selected)
    if case["formal_status"] == "WITHHOLD":
        false_selections = selected
    else:
        explicitly_forbidden = set(case["forbidden_selected_pages"])
        false_selections = {i for i, row in indexed if i in selected and row["page"] in explicitly_forbidden}
        false_selections |= (predictions - selected_expected) & selected
    mismatches = predictions - used
    return {
        **result, "status": "FAIL" if missing or mismatches or missing_selections or false_selections else "PASS",
        "candidate_tp": len(used), "candidate_fp": len(mismatches), "candidate_fn": len(missing),
        "formal_missing": missing_selections, "formal_false_admissions": len(false_selections),
        "missing": missing, "unexpected_in_window": [records[i] for i in sorted(mismatches)],
        "false_admissions": [records[i] for i in sorted(false_selections)],
    }


def evaluate(annotations, extraction, observations, objects):
    if annotations["schema"] != "independent-resource-annotations-v1" or annotations["cutoff"] != extraction["cutoff"]:
        raise ValueError("Annotation contract differs from the extraction cutoff or schema")
    _check_split(annotations["cases"])
    attempted = {identity(case) for case in extraction["cases"]}
    rows = {}
    for observation in observations:
        rows.setdefault(identity(observation), []).append(observation)
    results = [
        _case_result(case, rows.get(identity(case), ()), identity(case) in attempted,
                     _check_source(objects, case["source_sha256"]))
        for case in annotations["cases"]
    ]
    partitions = {}
    for partition in PARTITIONS:
        matching = [row for row in results if row["split"] == partition]
        measured = [row for row in matching if "candidate_tp" in row]
        totals = Counter()
        for row in measured:
            for key in ("candidate_tp", "candidate_fp", "candidate_fn", "formal_missing", "formal_false_admissions"):
                totals[key] += row[key]
        tp, fp, fn = (totals[f"candidate_{key}"] for key in ("tp", "fp", "fn"))
        partitions[partition] = {
            "cases": len(matching), "measured_cases": len(measured),
            "case_statuses": dict(Counter(row["status"] for row in matching)),
            "status": "MEASURED_SCOPED" if measured else "NOT_RUN",
            "candidate_tp": tp if measured else None,
            "candidate_fp": fp if measured else None,
            "candidate_fn": fn if measured else None,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "formal_false_admissions": totals["formal_false_admissions"] if measured else None,
            "formal_missing": totals["formal_missing"] if measured else None,
        }
    return {
        "schema": "resource-extraction-evaluation-v1",
        "cutoff": annotations["cutoff"],
        "metric_scope": "Exact candidate cells in annotated and attempted page/table/row windows only.",
        "corpus_accuracy": "UNMEASURED",
        "partitions": partitions,
        "cases": results,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--extraction", required=True, type=Path, help="Directory with extraction.json and observations.jsonl")
    parser.add_argument("--objects", required=True, type=Path, help="Directory of raw CAS objects named by SHA")
    parser.add_argument("--out", required=True, type=Path)
    options = parser.parse_args()
    annotations = options.annotations.read_bytes()
    extraction = (options.extraction / "extraction.json").read_bytes()
    observations = (options.extraction / "observations.jsonl").read_bytes()
    result = evaluate(json.loads(annotations), json.loads(extraction),
                      [json.loads(line) for line in observations.splitlines()], options.objects)
    result["input_sha256"] = {
        name: sha256(data).hexdigest()
        for name, data in (("annotations", annotations), ("extraction", extraction), ("observations", observations))
    }
    options.out.parent.mkdir(parents=True, exist_ok=True)
    options.out.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result["partitions"], sort_keys=True))
    return 1 if any(case["status"] == "FAIL" for case in result["cases"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
