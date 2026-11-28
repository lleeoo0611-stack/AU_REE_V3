"""Review bounded original resource-absence assertions against the cutoff Census."""

from argparse import ArgumentParser
from hashlib import sha256
import json
from pathlib import Path

from au_ree_v3.census import COLUMNS
from au_ree_v3.census_metadata import load_projection_policy
from au_ree_v3.contracts import canonical_json
from au_ree_v3.fresh_research import _verified_edition
from au_ree_v3.registry import load_profile
from au_ree_v3.research_delta import ResearchBuild
from au_ree_v3.sources import ContentStore
from verify_reported_mreo import _match


ORACLE = Path(__file__).resolve().parents[1] / "tests/fixtures/census_applicability_oracle_v1.json"
RESOURCE_HEADINGS = (*COLUMNS.values(), "有TREO資源估計")


def _document(spec, projects, profile, editions, events, content):
    receipt, digest = spec["capture_sha256"], spec["source_sha256"]
    edition = editions[receipt]
    if edition["sha256"] != digest or not set(projects).issubset(edition["project_ids"]):
        raise ValueError("Original review is outside the captured Project or edition")
    temporal = _verified_edition(profile, edition, events[receipt], content)
    if temporal.status != "ELIGIBLE":
        raise ValueError("Original public evidence is ineligible at the research cutoff")
    anchors = _match(content.path(digest), spec["witnesses"])
    return {
        "source_sha256": digest, "capture_sha256": receipt, "url": edition["url"],
        "temporal_decision_id": temporal.decision_id, "witnesses": anchors,
    }


def _cells(build, projects):
    return {project: {heading: {
        key: build.census[project, heading][key] for key in ("value_state", "value_origin", "reason", "value")
    } for heading in RESOURCE_HEADINGS} for project in projects}


def audit(profile, sources, assets, baseline, candidate):
    spec = json.loads(ORACLE.read_bytes())
    if spec["schema"] != "census-applicability-oracle-v1" or sha256(sources.read_bytes()).hexdigest() != spec["source_manifest_sha256"]:
        raise ValueError("Applicability review requires its pinned source manifest")
    settings = load_profile(profile)
    before, after = ResearchBuild.load(baseline), ResearchBuild.load(candidate)
    cutoff = spec["research_cutoff"]
    if {settings.research_cutoff.isoformat(), before.receipt["research_cutoff"], after.receipt["research_cutoff"]} != {cutoff}:
        raise ValueError("Applicability case, profile and Census cutoffs differ")
    policy = load_projection_policy(after.census_columns)
    rule = "REQUIRE_AFFIRMATIVE_PURPOSE_SPECIFIC_INAPPLICABILITY_EVIDENCE"
    if any(policy.by_name[heading]["applicability_rule"] != rule for heading in RESOURCE_HEADINGS):
        raise ValueError("Current Census resource columns differ from the reviewed applicability rule")
    manifest = json.loads(sources.read_bytes())
    content = ContentStore(assets)
    editions = {row["receipt_sha256"]: row for row in manifest["editions"]}
    events = {row["receipt_sha256"]: row for row in manifest["events"] if row["status"] == "PUBLIC_CAPTURED"}
    reviews = []
    for case in spec["cases"]:
        projects = case["project_ids"]
        original = [_document(row, projects, settings, editions, events, content) for row in case["documents"]]
        current = _cells(after, projects)
        if _cells(before, projects) != current or any(
            cell["value_state"] != ("PRESENT" if case.get("resource_present") and heading in COLUMNS.values() else "UNKNOWN")
            for cells in current.values() for heading, cell in cells.items()
        ):
            raise ValueError(f"Unexpected cutoff resource applicability in reviewed case {case['case_id']}")
        reviews.append({**case, "documents": original, "cutoff_resource_cells": current,
                        "cutoff_applicability": "RESOURCE_VALUES_PRESENT" if case.get("resource_present") else "UNRESOLVED"})
    return {
        "schema": "census-applicability-review-v1", "status": "PASS", "research_cutoff": cutoff,
        "oracle_sha256": sha256(ORACLE.read_bytes()).hexdigest(), "source_manifest_sha256": spec["source_manifest_sha256"],
        "census_projection_policy_sha256": policy.sha256, "baseline_build_sha256": before.receipt_sha,
        "candidate_build_sha256": after.receipt_sha, "named_cases": len(reviews),
        "original_pdf_count": sum(len(case["documents"]) for case in reviews),
        "reviewed_cells": sum(len(case["project_ids"]) * len(RESOURCE_HEADINGS) for case in reviews),
        "new_cutoff_not_applicable_cells": sum(
            row["value_state"] == "NOT_APPLICABLE" and before.census[key]["value_state"] != "NOT_APPLICABLE"
            for key, row in after.census.items()
        ), "reviews": reviews,
    }


def main():
    parser = ArgumentParser(description=__doc__)
    for flag in ("profile", "sources", "assets", "baseline", "candidate", "out"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    inputs = vars(parser.parse_args())
    output = inputs.pop("out")
    report = audit(**inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(canonical_json(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
