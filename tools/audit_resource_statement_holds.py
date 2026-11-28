"""Recheck manually annotated resource statement holds on exact published bytes."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from au_ree_v3.census_resource_display import _original_phrase
from au_ree_v3.documents import DocumentCache
from au_ree_v3.fresh_research import _verified_edition
from au_ree_v3.registry import load_profile
from au_ree_v3.sources import ContentStore

ROOT = Path(__file__).resolve().parents[1]
EXPECTATIONS = ROOT / "tests/fixtures/held_resource_statements_v1.json"


def audit(profile, manifest, assets, build):
    raw_manifest = manifest.read_bytes()
    sources = json.loads(raw_manifest)
    expected = json.loads(EXPECTATIONS.read_bytes())
    observed = (json.loads(line) for line in (build / "observations/observations.jsonl").read_bytes().splitlines())
    selected = {(row["project_id"], row["source_sha256"]) for row in observed if row["selected_for_census"]}
    events = {row["receipt_sha256"]: row for row in sources["events"] if row["status"] == "PUBLIC_CAPTURED"}
    settings = load_profile(profile)
    content = ContentStore(assets)
    cache = DocumentCache()
    checked = []
    for case in expected["cases"]:
        source, project = case["source_sha256"], case["project_id"]
        edition, = [row for row in sources["editions"] if row["sha256"] == source and project in row["project_ids"]]
        capture = edition["receipt_sha256"]
        temporal = _verified_edition(settings, edition, events[capture], content)
        if temporal.status != "ELIGIBLE" or (project, source) in selected:
            raise ValueError(f"Exact held statement was selected or lost its capture proof: {case['case']}")
        pdf = cache.pdf(content.read(source), source, max(page for page, _ in case["witnesses"]))
        evidence = []
        for page, quote in case["witnesses"]:
            anchor = _original_phrase(pdf, page, quote)
            if anchor is None:
                raise ValueError(f"Independent native evidence cannot be located: {case['case']}, page {page}")
            evidence.append({"physical_page": anchor.page, "locator": anchor.locator, "quote": anchor.text})
        checked.append({**{key: value for key, value in case.items() if key != "witnesses"},
                        "capture_sha256": capture, "temporal_decision_id": temporal.decision_id, "evidence": evidence})
    return {"schema": expected["schema"], "status": "PASS", "source_manifest_sha256": sha256(raw_manifest).hexdigest(),
            "known_scope_negative_count": len(checked), "formal_admission_false_positives": 0,
            "scope": "NAMED_ORIGINAL_NEGATIVES_ONLY", "generalized_admission_precision_recall": "UNMEASURED",
            "cases": checked}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("profile", "manifest", "assets", "build", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.profile, args.manifest, args.assets, args.build)
    with args.out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"status": result["status"], "cases": result["known_scope_negative_count"]}))


if __name__ == "__main__":
    main()
