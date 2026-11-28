"""Compare attribute projection to a fixed Census and independently read original PDFs."""

import argparse
from collections import Counter, defaultdict
import csv
from hashlib import file_digest, sha256
import json
from pathlib import Path
import subprocess

SELECTED = {
    ("AU-efff91a06cf344122d88", "deposit_type_code"): "CARBONATITE_SUPERGENE_REGOLITH",
    ("AU-efff91a06cf344122d88", "deposit_type"): "碳酸岩風化殼超基因富集型",
    ("AU-524c37fc04cf47bf8dca", "development_stage_code"): "DFS",
    ("AU-524c37fc04cf47bf8dca", "development_stage_category"): "已完成最終可行性研究",
    ("AU-524c37fc04cf47bf8dca", "development_stage"): "已完成最終可行性研究（late 2022 · Balranald critical minerals development）",
}
DECISIONS = {
    "mt-weld-issuer-deposit-type": ("CONFIRMED_DEPOSIT_TYPE", "VALIDATED", True),
    "mt-weld-issuer-host-description-only": ("HOST_DESCRIPTION", "CANDIDATE", False),
    "koppamurra-pfs-asserted-date-unreported": ("COMPLETED", "EVIDENCE_INSUFFICIENT", False),
    "koppamurra-dfs-engineering-plan": ("PLANNED", "CANDIDATE", False),
    "donald-phase-one-dfs-completed": ("COMPLETED", "EVIDENCE_INSUFFICIENT", False),
    "donald-phase-two-pfs-completed": ("COMPLETED", "EVIDENCE_INSUFFICIENT", False),
    "balranald-dfs-completed": ("COMPLETED", "VALIDATED", True),
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def read_json(path):
    return json.loads(path.read_bytes())


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def ledger(build):
    with (build / "census/cell_status.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    values = {(row["project_id"], row["column_id"]): row for row in rows}
    require(len(rows) == len(values) == 4743, "Census ledger shape or identity changed")
    return values


def verify_proofs(cases, assets):
    grouped = defaultdict(list)
    for item in cases:
        grouped[item["source_sha256"]].extend(item["evidence"])
    anchors = 0
    for source, evidence in sorted(grouped.items()):
        path = assets / "objects" / source
        require(digest(path) == source, "Source PDF fingerprint differs")
        last_page = max(record["page"] for record in evidence)
        raw = subprocess.run(
            ["pdftotext", "-f", "1", "-l", str(last_page), "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=30, check=True,
        ).stdout
        pages = [" ".join(page.split()) for page in raw.decode("utf-8").split("\f")]
        for anchor in evidence:
            text = pages[anchor["page"] - 1]
            span = anchor["text_span"]
            require(anchor["source_sha"] == source and text[span["start"]:span["end"]] == anchor["quote"]
                    and text.count(anchor["quote"]) == 1,
                    "Independent PDF anchor differs")
            anchors += 1
    return {"source_pdfs": len(grouped), "anchors": anchors}


def verify(before, after, assets):
    baseline, candidate = (read_json(path / "build_receipt.json") for path in (before, after))
    require(baseline["input_artifacts"]["sources"] == candidate["input_artifacts"]["sources"],
            "Source corpora differ")
    for directory, receipt in ((before, baseline), (after, candidate)):
        for name, checksum in receipt["scientific_artifacts"].items():
            require(digest(directory / name) == checksum, f"Build artifact differs: {name}")
    fixed = ("observations/observations.jsonl", "observations/extraction.json",
             "decision/mcda_inputs.json", "decision/mcda_results.json", "decision/decision_receipt.json")
    require(all(baseline["scientific_artifacts"][name] == candidate["scientific_artifacts"][name] for name in fixed),
            "Numeric observations or MCDA changed")
    old, new = ledger(before), ledger(after)
    require(old.keys() == new.keys(), "Census grid keys differ")
    changed = {key for key, row in new.items() if row != old[key]}
    require(changed == SELECTED.keys(), "Census changed outside of the five selected attribute cells")
    for key, expected in SELECTED.items():
        row = new[key]
        require(old[key]["value_state"] == "UNKNOWN" and row["value_state"] == "PRESENT"
                and row["evidence_state"] == "VALIDATED_DESCRIPTIVE" and row["value"] == expected
                and not row["observation_id"] and row["comparable"] in ("", "NO"), "Attribute is absent or malformed")
    assessment = read_json(after / "census/census_receipt.json")["attribute_assessments"]
    cases = assessment["cases"]
    require({row["case_id"]: (row["assertion"]["role"], row["verdict"], bool(row["selection"]))
             for row in cases} == DECISIONS, "Attribute role or review outcome differs")
    require(assessment["display_cells"] == 5 and assessment["selected_attributes"] == 2
            and assessment["mcda_inputs_added"] == 0, "Receipt selected an unexpected attribute")
    return {
        "schema": "census-attribute-independent-review-v1", "status": "PASS",
        "baseline_build_sha256": digest(before / "build_receipt.json"),
        "candidate_build_sha256": digest(after / "build_receipt.json"),
        "cohort_cells": len(new), "changed_attribute_cells": len(changed),
        "selected_attributes": assessment["selected_attributes"],
        "existing_formal_scientific_cells": candidate["scientific_output_count"],
        "comparable_project_criteria": candidate["comparable_project_criteria"],
        "unchanged_artifacts": {name: candidate["scientific_artifacts"][name] for name in fixed},
        "source_evidence": verify_proofs(cases, assets),
        "verdicts": dict(Counter(row["verdict"] for row in cases)),
        "cases": {row["case_id"]: row["reason"] for row in cases},
        "policy_sha256": assessment["policy_sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for label in ("before", "after", "assets", "out"):
        parser.add_argument("--" + label, type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.before, args.after, args.assets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(result, sort_keys=True, ensure_ascii=False, indent=2) + "\n"
    with args.out.open("x", encoding="utf-8") as stream:
        stream.write(data)
    print(sha256(data.encode()).hexdigest())


if __name__ == "__main__":
    main()
