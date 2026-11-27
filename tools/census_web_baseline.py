"""Verify the clean V3 CW0 build against the exact immutable candidate archive and cold replay."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import runpy

BASE = "40309ea006d2eaace3fe4bbf33beccca492261de"
RELEASE = "24bb0056a0655757a14fd11244fcee207e8a9b88f3d576f7e86c6341b97b3974"


def read(path):
    data = path.read_bytes()
    return json.loads(data), sha256(data).hexdigest()


def verify(root, current, replay_dir, out):
    data, data_sha = read(current/"build_receipt.json")
    if data["code_commit"] != BASE or data["gates"] != dict.fromkeys(("F0","F1","F2","F3"),"PASS"):
        raise ValueError("CW0 baseline must use the exact clean Git commit and passed build gates")
    prior=root/"work/yield/candidate/build"
    manifest_path=root/"work/yield/candidate/archive/releases"/f"{RELEASE}.json"
    archive, archive_sha = read(manifest_path)
    prior_receipt, prior_sha=read(prior/"build_receipt.json")
    replay, replay_sha = read(replay_dir/"replay_receipt.json")
    if (archive_sha!=RELEASE or archive["build_receipt_sha256"]!=prior_sha
            or prior_receipt["scientific_artifacts"]!=archive["output_hashes"]
            or archive["input_artifacts"]!=data["input_artifacts"]):
        raise ValueError("CW0 prior/current input or immutable archive provenance differs")
    if (replay["status"]!="PASS" or replay["F4"]!="PASS" or replay["offline"] is not True
            or replay["release_id"]!=RELEASE or replay["actual"]!=archive["output_hashes"]):
        raise ValueError("CW0 archive lacks exact cold replay evidence")
    for relative, digest in archive["output_hashes"].items():
        for path in (prior/relative, replay_dir/"rebuilt"/relative):
            if sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError(f"Archived V3 output differs from manifest {path}")
    for relative,digest in data["scientific_artifacts"].items():
        if sha256((current/relative).read_bytes()).hexdigest()!=digest:
            raise ValueError(f"CW0 current output differs from baseline receipt {relative}")

    science=runpy.run_path(str(root/"tools/engineering_benchmark.py"))["science"]
    a,b = science(prior),science(current)
    differences=sorted(field for field in a if a[field]!=b[field])
    if differences:
        raise ValueError(f"CW0 scientific differences: {differences}")
    projection,_=read(current/"census/census_receipt.json")
    historical,_=read(root/"docs/research/ontology_gain_receipt.json")
    sources, sources_sha=read(root/"work/research-yield/source-inputs/source_manifest.json")
    if sources_sha!=data["input_artifacts"]["sources"]:
        raise ValueError("CW0 source manifest fingerprint mismatch")
    if (projection["projects"],projection["columns"],projection["cells"],projection["identity_cells"],
        projection["formal_scientific_cells"],projection["cell_states"]["PRESENT"],
        data["verified_source_count"],data["eligible_source_count"],data["comparable_project_criteria"],
        data["mcda_panels"]["issuer_resource_composition"]["eligible_count"]) != (93,51,4743,93,12,105,144,144,6,2):
        raise ValueError("CW0 baseline count mismatch")
    statuses = historical["gates"]
    result={
        "schema":"census-web-cw0-baseline-v1","CW0":"PASS", "status":"PASS",
        "main_code_commit":BASE,"pinned_research_candidate_id":RELEASE,
        "pinned_candidate_scientific_code_commit":archive["code_commit"],
        "prior_archive_build_sha256":prior_sha,"prior_cold_replay_receipt_sha256":replay_sha,
        "prior_cold_replay_compared_artifacts":replay["compared_artifacts"],
        "prior_archive_replica_status":archive["replica_inventory"]["status"],
        "main_build_receipt_sha256":data_sha,"main_build_input_sha256":data["input_artifacts"],
        "main_build_scientific_artifacts":data["scientific_artifacts"],
        "research_cutoff":data["research_cutoff"],"timezone":archive["timezone"],
        "projects":93,"census_columns":51,"census_cells":4743,"census_populated":105,
        "census_identity":93,"census_scientific":12,
        "source_edition_events":len(sources["editions"]),"verified_sha256_count":144,"temporal_eligible_sha256_count":144,
        "observation_counts":data["observation_counts"],"comparable_project_criteria":6,"panels":data["mcda_panels"],
        "science_comparison_checked_fields":sorted(a),"science_differences":differences,
        "historical_gates":{"O2":statuses["O2"],"F5":statuses["F5"]},
        "optional_legacy_html":{"status":"NOT_FOUND_IN_LOCAL_SEARCH",
            "filename":"rare-earth-projects_v8_2026-07-20.html",
            "searched":["workspace","uploaded attachments","shared downloads","library files","pinned Git worktrees"]},
    }
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ("repository","current","replay","out"):
        parser.add_argument("--"+name,type=Path,required=True)
    args=parser.parse_args()
    print(verify(args.repository,args.current,args.replay,args.out)["status"])
