"""Check the fixed Census attribute batch against independently reviewed original evidence."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import subprocess

from verify_census_attributes import digest, ledger, read_json, require, verify_artifacts
from verify_census_attribute_breadth import SOURCE_MANIFEST, fingerprint, selection_metadata

CLS, STATUS, HISTORY = "JORC_CLASSIFICATION", "JORC_STUDY_STATUS", "PROJECT_HISTORY"
POSITIVES = {
    (CLS, "AU-d9fb1b1e3e2ec9fa0851"): (26, "40b378004d36", "ALLUVIAL_REE", "沖積型稀土礦床", None,
                                      "Alluvial REE deposit which is hosted by alluvial fans", "2013-04-15"),
    (CLS, "AU-216d0983f6892f9b58f9"): (129, "452dabd702e8", "CLAY_REGOLITH_HOSTED_REO", "黏土風化殼寄主型稀土礦床", None,
                                      "mixed clay regolith-hosted REO mineralisation", "2026-08-18"),
    (CLS, "AU-9b667066e6eb66753005"): (30, "5c0fe372d968", "CARBONATITE_HOSTED_REO", "碳酸岩寄主型稀土礦床", None,
                                      "The Cummins Range REO deposit occurs within the Cummins Range carbonatite complex", "2024-01-25"),
    (CLS, "AU-524c37fc04cf47bf8dca"): (33, "94a57655d74e", "HEAVY_MINERAL_STRANDLINE", "海灘岸線型重礦物砂礦床", None,
                                      "West Balranald deposit is a beach deposited HM strandline", "2023-02-21"),
    (CLS, "AU-6d807eb7caec9db0f35e"): (23, "5f8f642b6c9b", "OFFSHORE_HEAVY_MINERAL_SANDS", "離岸沉積型重礦物砂礦床", None,
                                      "offshore depositional paleo-environment of the Loxton Parilla Sands", "2025-10-15"),
    (STATUS, "AU-9bd8e5ed8408cd84d876"): (171, "5aad631c770a", "DFS", "Browns Range Project — Wolverine scope", "2025-08",
                                         "Browns Range HRE Project completed in August 2025", "2025-09-15"),
    (HISTORY, "AU-2cfeb25aefee0c4f95fb"): (23, "734d9ea35fd9", "DFS", "Dubbo Project", "2013",
                                          "Definitive Feasibility Study based on a flowsheet to recover the above products, was completed in 2013",
                                          "2025-07-11"),
}
REFUSALS = {
    (CLS, "AU-4ec1fb08563b42352d29"): (44, "39699576fe9e", "CANDIDATE", "TENTATIVE_GEOLOGICAL_INTERPRETATION",
                                      "The deposit type being investigated"),
    (CLS, "AU-b2dadf9c8046c3c68a02"): (23, "246dea894d00", "CANDIDATE", "TENTATIVE_GEOLOGICAL_INTERPRETATION",
                                      "It is thought that the regolith"),
    (CLS, "AU-176852ef1348ac10d154"): (28, "77097e83c943", "EVIDENCE_INSUFFICIENT", "REFERENCE_TO_ADJACENT_SCOPE",
                                      "Goschen Project, south of the Nowie drill program"),
    (STATUS, "AU-d9fb1b1e3e2ec9fa0851"): (36, "40b378004d36", "CANDIDATE", "NO_UNIQUE_DATED_COMPLETION",
                                         "The status of study at Charley Creek is Scoping Study"),
}
EVIDENTIARY = {"FORMAL_DIRECT", "FORMAL_DERIVED", "VALIDATED_DESCRIPTIVE"}
DFS_LABEL = "已完成最終可行性研究"


def read_anchors(cases, assets):
    grouped = defaultdict(list)
    for row in cases.values():
        grouped[row["source_sha256"]].extend(row.get("evidence") or row["review_evidence"])
        receipt = assets / "objects" / row["capture_sha256"]
        capture = read_json(receipt)
        require(digest(receipt) == row["capture_sha256"] and capture["sha256"] == row["source_sha256"]
                and row["project_id"] in capture["project_ids"] and capture["status"] == "PUBLIC_CAPTURED"
                and capture["http_status"] == 200 and capture["acquired_at_research_timezone"][:10] <= "2026-11-28",
                "Public source capture or Project membership differs")
    for source, anchors in grouped.items():
        path = assets / "objects" / source
        require(digest(path) == source, "Original PDF differs")
        result = subprocess.run(
            ["pdftotext", "-layout", "-enc", "UTF-8", str(path), "-"],
            capture_output=True, timeout=30, check=True,
        )
        pages = result.stdout.decode("utf-8").split("\f")
        for anchor in anchors:
            span = anchor["text_span"]
            lines = pages[anchor["page"] - 1].splitlines()[span["start_line"] - 1:span["end_line"]]
            region = " ".join(word for line in lines for word in line[span["start_column"] - 1:].split())
            quote = anchor["quote"]
            require(anchor["source_sha"] == source and span["view"] == "NATIVE_PDF_LAYOUT_REGION"
                    and region[span["start"]:span["end"]] == quote and region.count(quote) == 1,
                    "Native layout anchor differs on independent PDF readback")
    return len(grouped), sum(map(len, grouped.values()))


def review_assertions(cases):
    expected_cells = {}
    for (adapter, project), (page, source, code, label, event, original, publication) in POSITIVES.items():
        row = cases[adapter, project]
        claim = row["assertion"]
        require(row["verdict"] == "VALIDATED" and row["selection"]["purpose"] == "CENSUS_DISPLAY_ONLY"
                and row["page"] == page and row["source_sha256"].startswith(source)
                and original in claim["source_term"] and claim["controlled_term"] == code
                and claim["event_date"] == event and claim["publication_date"] == publication
                and claim["role"] == ("CONFIRMED_DEPOSIT_TYPE" if adapter == CLS else "COMPLETED")
                and claim["scope_kind"] == ("NAMED_PROJECT_DEPOSIT" if adapter == CLS else "PROJECT")
                and row["temporal_decision"]["status"] == "ELIGIBLE",
                "Independent expected original attribute differs")
        fields = {"deposit_type_code": code, "deposit_type": label} if adapter == CLS else {
            "development_stage_code": code, "development_stage_category": DFS_LABEL,
            "development_stage": f"{DFS_LABEL}（{claim['event_date_token']} · {label}）",
        }
        expected_cells.update({(project, key): (value, row) for key, value in fields.items()})
    for key, (page, source, verdict, reason, original) in REFUSALS.items():
        row = cases[key]
        require(row["selection"] is None and row["verdict"] == verdict and row["reason"] == reason
                and row["page"] == page and row["source_sha256"].startswith(source)
                and original in row["review_evidence"][0]["quote"], "Native refusal did not follow original evidence")
    return expected_cells


def verify(before, after, assets):
    baseline, candidate, fixed, decision = verify_artifacts(before, after)
    require(baseline["input_artifacts"]["sources"] == SOURCE_MANIFEST
            and baseline["verified_source_count"] == candidate["verified_source_count"] == 144
            and baseline["input_artifacts"]["bindings"] == candidate["input_artifacts"]["bindings"]
            and baseline["observation_counts"] == candidate["observation_counts"]
            and baseline["scientific_output_count"] == candidate["scientific_output_count"] == 19
            and baseline["comparable_project_criteria"] == candidate["comparable_project_criteria"] == 6,
            "Fixed source denominator or numeric science changed")
    old, new = ledger(before), ledger(after)
    prior_receipt, receipt = (read_json(directory / "census/census_receipt.json") for directory in (before, after))
    prior_attrs, attrs = (item["attribute_assessments"] for item in (prior_receipt, receipt))
    cases = [row for row in attrs["cases"] if row.get("adapter")]
    indexed = {(row["adapter"], row["project_id"]): row for row in cases}
    require(len(cases) == len(indexed) and indexed.keys() == POSITIVES.keys() | REFUSALS.keys(),
            "Reviewed set differs from the independent cases")
    reviewed = review_assertions(indexed)
    prior_cells = {key for key, row in old.items() if row["evidence_state"] == "VALIDATED_DESCRIPTIVE"}
    differing = {key for key, row in new.items() if row != old[key]}
    require(old.keys() == new.keys() and differing == prior_cells | reviewed.keys()
            and len(prior_cells) == 11 and attrs["display_cells"] == 27 and attrs["mcda_inputs_added"] == 0,
            "Core grid changed outside the approved display fields")
    for key in prior_cells:
        require({field for field in new[key] if new[key][field] != old[key][field]}
                == {"metadata_evidence", "selection_id", "lineage_id"}
                and selection_metadata(old[key], baseline) == selection_metadata(new[key], candidate),
                "Prior attribute value or original source evidence changed")
    previous = {row["case_id"]: {k: v for k, v in row.items() if k != "selection"} for row in prior_attrs["cases"]}
    require(previous == {row["case_id"]: {k: v for k, v in row.items() if k != "selection"}
                         for row in attrs["cases"] if "adapter" not in row}, "Historical role or refusal changed")
    for key, (value, proof) in reviewed.items():
        row = new[key]
        display = json.loads(row["metadata_evidence"])
        require(old[key]["value_state"] == "UNKNOWN" and row["value_state"] == "PRESENT"
                and row["value"] == value and row["evidence_state"] == "VALIDATED_DESCRIPTIVE"
                and row["source_sha256"] == proof["source_sha256"] and not row["observation_id"]
                and row["comparable"] in {"", "NO"} and display["anchors"] == proof["evidence"]
                and display["selection"] == proof["selection"] and display["assessment"] == proof["assessment"],
                "Cell does not match the exact checked assertion and display use")
        selection_metadata(row, candidate)
    surfaces = {}
    for adapter in (CLS, STATUS, HISTORY):
        gold = Counter("positive" if key in POSITIVES else "negative" for key in indexed if key[0] == adapter)
        surfaces[adapter] = {"tp": gold["positive"], "fp": 0, "fn": 0, "tn": gold["negative"],
                             "reviewed_denominator": sum(gold.values()), "scope": "PURPOSIVE_FIXED_CASES"}
    source_pdfs, anchors = read_anchors(indexed, assets)
    prior_reach, new_reach = ({key[0] for key, row in grid.items() if row["evidence_state"] in EVIDENTIARY}
                              for grid in (old, new))
    return {
        "schema": "native-census-attribute-independent-review-v1", "status": "PASS",
        "baseline_build_sha256": digest(before / "build_receipt.json"), "candidate_build_sha256": digest(after / "build_receipt.json"),
        "source_manifest_sha256": SOURCE_MANIFEST, "cohort_cells": len(new),
        "core_present": {"before": prior_receipt["cell_states"]["PRESENT"], "after": receipt["cell_states"]["PRESENT"]},
        "meaningful_project_reach": {"before": len(prior_reach), "after": len(new_reach), "new_project_ids": sorted(new_reach - prior_reach)},
        "fixed_source_positive_assertions": len(POSITIVES), "fixed_source_new_display_cells": len(reviewed),
        "fixed_source_reviewed_pdfs": source_pdfs, "independent_readback_anchors": anchors,
        "new_source_editions": 0, "unchanged_numeric_artifacts": fixed, "unchanged_normalized_mcda_artifacts": decision,
        "authorized_policy_sha256": attrs["batch_policy_sha256"], "evaluated_surfaces": surfaces,
        "unseen_families_precision_recall": "UNMEASURED",
        "cases": {row["case_id"]: {"verdict": row["verdict"], "reason": row["reason"],
                                 "source_sha256": row["source_sha256"], "page": row["page"]} for row in cases},
        "reviewed_cells_sha256": fingerprint(sorted((project, column, value) for (project, column), (value, _) in reviewed.items())),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "assets", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.before, args.after, args.assets)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, ensure_ascii=False)
        stream.write("\n")
    print(digest(args.out))


if __name__ == "__main__":
    main()
