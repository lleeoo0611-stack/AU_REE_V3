"""Independently verify expanded text attributes against the frozen Census and original PDFs."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from verify_census_attributes import (
    DECISIONS, SELECTED, digest, ledger, pdf_pages, read_json, require, verify_artifacts, verify_proofs,
)

SOURCE_MANIFEST = "2cbb412fec60d43048321142b4baf242a8bae91739b6f7f2b1771eb1765629cd"
PROSPECTUS = "af0a8ff9a1b194a45f16dc56dd3b71c04c5f426f15dd23d0511de087f9ec8ac5"
PROSPECTUS_CAPTURE = "c75bd82388ad8a43e12a337a7f232298106b5220195901f4be66fe6904dd5179"
GOSCHEN = "5f8f642b6c9b561ca0f397fee0990e52786d85af3e67d53756def87b5cde2cd0"
DUBBO = "734d9ea35fd9617eb2bad21c48fec17cdae08a5c66fa3e3cf7857390d04218ab"
COMPLETED_DFS = "已完成最終可行性研究"
NEW_STUDIES = {
    "avonbank-dfs-completed": {
        "project": "AU-329908ab8d4c1d1d3e03", "scope": "Avonbank Mineral Sands Project", "page": 46,
        "event": "2021", "token": "2021",
        "quote": "The Avonbank Project Definitive Feasibility Study completed in 2021 demonstrated attractive economics for the Project.",
    },
    "copi-dfs-completed": {
        "project": "AU-fe65cb1bd28eacb7a67f", "scope": "Copi Mineral Sands Project", "page": 47,
        "event": "2024-01", "token": "January 2024",
        "quote": "Attractive returns are demonstrated by the Copi DFS completed in January 2024",
    },
}
GOSCHEN_ID = "goschen-dfs-explicit-completion-date-unreported"
DUBBO_ID = "dubbo-dfs-project-scope-not-local"


def fingerprint(payload):
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256(data).hexdigest()


def selection_metadata(row, build):
    provenance = json.loads(row["metadata_evidence"])
    selection = provenance.pop("selection")
    selection_id = selection.pop("selection_id")
    version = selection.pop("contract_version")
    require(version == "attribute-selection-v1"
            and selection_id == row["selection_id"] == fingerprint([version, selection])
            and selection.pop("profile_sha256") == build["input_artifacts"]["profile"]
            and selection["source_manifest_sha256"] == SOURCE_MANIFEST
            and selection["release_id"] == build["observation_release_id"]
            and selection["purpose"] == "CENSUS_DISPLAY_ONLY",
            "Attribute selection is not bound to its own profile, source manifest, and release")
    require(row["lineage_id"] == fingerprint({key: value for key, value in row.items() if key != "lineage_id"}),
            "Cell lineage fingerprint differs")
    return provenance, selection


def new_cells():
    return {(item["project"], column): value for item in NEW_STUDIES.values() for column, value in (
        ("development_stage_code", "DFS"), ("development_stage_category", COMPLETED_DFS),
        ("development_stage", f"{COMPLETED_DFS}（{item['token']} · {item['scope']}）"),
    )}


def review_sources(cases, assets):
    proof_path = assets / "objects" / PROSPECTUS_CAPTURE
    proof = read_json(proof_path)
    require(digest(proof_path) == PROSPECTUS_CAPTURE and proof["sha256"] == PROSPECTUS
            and proof["http_status"] == 200 and proof["method"] == "ANONYMOUS_HTTPS_GET"
            and proof["status"] == "PUBLIC_CAPTURED" and proof["final_url"].startswith("https://international.austrade.gov.au/")
            and proof["acquired_at_research_timezone"][:10] == "2026-11-28",
            "Austrade public capture differs")
    for case, expected in NEW_STUDIES.items():
        row = cases[case]
        assertion = row["assertion"]
        require(row["project_id"] == expected["project"] and expected["project"] in proof["project_ids"]
                and row["source_sha256"] == PROSPECTUS
                and row["capture_sha256"] == PROSPECTUS_CAPTURE and row["page"] == expected["page"]
                and row["verdict"] == "VALIDATED" and assertion["role"] == "COMPLETED" and row["selection"]
                and assertion["scope_kind"] == "PROJECT" and assertion["scope"] == expected["scope"]
                and assertion["source_term"] == expected["quote"]
                and assertion["publication_date"] == "2026-03"
                and assertion["event_date"] == expected["event"] and assertion["event_date_token"] == expected["token"]
                and row["temporal_decision"]["status"] == "ELIGIBLE" and row["temporal_decision"]["matching_evidence_ids"]
                and any(anchor["page"] == 2 and anchor["quote"] == "Publication date: March 2026"
                        for anchor in row["evidence"]), "Project milestone lacks its exact date, scope, or source")
    goschen = cases[GOSCHEN_ID]
    require(goschen["source_sha256"] == GOSCHEN and goschen["page"] == 8
            and goschen["assertion"]["source_term"] == "the 2023 Definitive Feasibility Study completed for Area 1 by VHM."
            and goschen["assertion"]["event_date"] is None and goschen["selection"] is None
            and goschen["verdict"] == "EVIDENCE_INSUFFICIENT" and goschen["reason"] == "HISTORICAL_OBSERVATION_DATE_MISSING",
            "Goschen study label was confused with an event date")
    dubbo = cases[DUBBO_ID]
    pages = pdf_pages(assets / "objects" / DUBBO, DUBBO, 23)
    page = pages[22]
    completion = "A Definitive Feasibility Study based on a flowsheet to recover the above products, was completed in 2013"
    scope = "The Dubbo Project was originally developed by Alkane Resources Limited (Alkane)"
    require(dubbo["source_sha256"] == DUBBO and dubbo["page"] == 23 and dubbo["selection"] is None
            and dubbo["verdict"] == "EVIDENCE_INSUFFICIENT" and dubbo["reason"] == "PROJECT_SCOPE_NOT_LOCAL_TO_MILESTONE"
            and page.count(completion) == page.count(scope) == 1
            and page.index(completion) - (page.index(scope) + len(scope)) > 160
            and "ASX Release 11 July 2025" in pages[0],
            "Dubbo local-scope refusal no longer follows the original PDF")
    return {**verify_proofs([row for row in cases.values() if "evidence" in row], assets),
            "separately_read_refusal_pdfs": 1, "new_validated_source_pdfs": 1}


def verify(before, after, assets):
    baseline, candidate, fixed, decision = verify_artifacts(before, after)
    require(baseline["input_artifacts"]["sources"] == SOURCE_MANIFEST
            and baseline["input_artifacts"]["bindings"] == candidate["input_artifacts"]["bindings"]
            and baseline["verified_source_count"] == candidate["verified_source_count"] == 144
            and baseline["scientific_output_count"] == candidate["scientific_output_count"] == 18
            and baseline["observation_counts"] == candidate["observation_counts"]
            and baseline["comparable_project_criteria"] == candidate["comparable_project_criteria"] == 6,
            "Frozen source cohort or existing science changed")
    old, new = ledger(before), ledger(after)
    expected = new_cells()
    require(old.keys() == new.keys(), "Census grid identities differ")
    changed = {key for key in new if new[key] != old[key]}
    require(changed == SELECTED.keys() | expected.keys(), "Census changed outside the approved attribute cells")
    for key, value in SELECTED.items():
        require(old[key]["value"] == new[key]["value"] == value
                and {field for field in new[key] if old[key][field] != new[key][field]}
                == {"selection_id", "metadata_evidence", "lineage_id"}
                and selection_metadata(old[key], baseline) == selection_metadata(new[key], candidate),
                "A prior descriptive value or its source evidence changed")
    for key, value in expected.items():
        row = new[key]
        require(old[key]["value_state"] == "UNKNOWN" and row["value_state"] == "PRESENT"
                and row["value"] == value and row["evidence_state"] == "VALIDATED_DESCRIPTIVE"
                and row["source_sha256"] == PROSPECTUS and not row["observation_id"]
                and row["comparable"] in ("", "NO"), "Expanded descriptive cell is absent or malformed")
        selection_metadata(row, candidate)
    old_census, new_census = (read_json(directory / "census/census_receipt.json") for directory in (before, after))
    old_review, review = (item["attribute_assessments"] for item in (old_census, new_census))
    row_list = review["cases"]
    cases = {row["case_id"]: row for row in row_list}
    require(len(cases) == len(row_list) and cases.keys() == DECISIONS.keys() | NEW_STUDIES.keys() | {GOSCHEN_ID, DUBBO_ID}
            and {key: (cases[key]["assertion"]["role"], cases[key]["verdict"], bool(cases[key]["selection"]))
                 for key in DECISIONS} == DECISIONS
            and old_review["policy_sha256"] == review["policy_sha256"]
            and old_review["display_cells"] == 5 and review["display_cells"] == 11
            and old_review["selected_attributes"] == 2 and review["selected_attributes"] == 4
            and review["mcda_inputs_added"] == 0 and new_census["coverage"]["scientific"] == old_census["coverage"]["scientific"],
            "Historical controls, approved policy, or attributed coverage changed")
    return {
        "schema": "census-attribute-breadth-review-v1", "status": "PASS",
        "baseline_build_sha256": digest(before / "build_receipt.json"),
        "candidate_build_sha256": digest(after / "build_receipt.json"),
        "source_manifest_sha256": SOURCE_MANIFEST, "cohort_cells": len(new),
        "present_cells": {"before": old_census["cell_states"]["PRESENT"], "after": new_census["cell_states"]["PRESENT"]},
        "fixed_144_net_new_cells": len(expected), "fixed_144_net_new_project_attributes": len(NEW_STUDIES),
        "unchanged_old_attribute_values_and_source_anchors": len(SELECTED),
        "new_cells": [{"project_id": project, "column_id": column, "value": value}
                      for (project, column), value in sorted(expected.items())],
        "new_assertions": {key: cases[key]["assertion"] for key in (*NEW_STUDIES, GOSCHEN_ID)},
        "numeric_observation_counts": candidate["observation_counts"],
        "unchanged_formal_scientific_cells": candidate["scientific_output_count"],
        "unchanged_comparable_project_criteria": candidate["comparable_project_criteria"],
        "unchanged_numeric_artifacts": fixed,
        "unchanged_mcda_content_excluding_profile_and_linked_artifact_hashes": decision,
        "source_evidence": review_sources(cases, assets), "verdicts": review["verdicts"],
        "cases": {key: row["reason"] for key, row in cases.items()}, "policy_sha256": review["policy_sha256"],
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
