"""Measure the single-version T1 shadow workload with fixed commit and CAS."""

import argparse
import cProfile
from hashlib import sha256
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import tarfile
from time import perf_counter

from engineering_benchmark import PROFILE, ASSETS, SOURCES, fingerprint_tree, git, metrics

BINDINGS = "config/research_profiles/technical_bindings_v1.json"
ORACLE = "tests/fixtures/technical_processing_oracle_v1.json"


def child(tree, manifest, assets, out, trace):
    profiler = cProfile.Profile() if trace != "-" else None
    if profiler:
        profiler.enable()
    started = perf_counter()
    from au_ree_v3.technical_processing import run_technical_pilot
    result = run_technical_pilot(tree/PROFILE, manifest, assets, tree/BINDINGS, out, oracle=tree/ORACLE)
    elapsed = perf_counter()-started
    if profiler:
        profiler.disable()
        profiler.dump_stats(trace)
    print(json.dumps({
        "status":result["status"], "seconds":elapsed,
        "parent_peak_rss_kib":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "subprocess_peak_rss_kib":resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        **{field:result[field] for field in (
            "source_objects_read_back", "candidate_observations", "source_anchored_observations",
            "pairwise_compatible", "ranked_panels", "observation_artifact_sha256", "pair_artifact_sha256",
        )},
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-repo", type=Path, required=True, help="V3 directory containing the locked CAS and manifest")
    parser.add_argument("--current-repo", type=Path, required=True)
    parser.add_argument("--current-ref", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    baseline, current, out = (path.resolve() for path in (args.base_repo, args.current_repo, args.out))
    out.mkdir(parents=True)
    tree = out/"technical-code"
    tree.mkdir()
    commit = git(current,"rev-parse",args.current_ref).decode().strip()
    tar = out/"technical.tar"
    tar.write_bytes(git(current,"archive","--format=tar",commit,"src","config",ORACLE))
    with tarfile.open(tar) as archived:
        archived.extractall(tree, filter="data")
    tar.unlink()
    trace = out/"technical.prof"
    committed_source = tree/"src"
    runs = []
    for index in range(4):
        result = subprocess.check_output([
            sys.executable,"-B",str(Path(__file__).resolve()),"child",str(tree),str(baseline/SOURCES),
            str(baseline/ASSETS),str(out/f"run-{index+1}"),str(trace) if index==3 else "-",
        ],env={**os.environ,"PYTHONPATH":str(committed_source)})
        runs.append(json.loads(result))
    profiled = metrics(trace,committed_source)
    actual = [{k:v for k,v in run.items() if k not in ("seconds","parent_peak_rss_kib","subprocess_peak_rss_kib")}
              for run in runs]
    stable = len({json.dumps(record,sort_keys=True) for record in actual})==1
    report = {
        "schema":"technical-shadow-engineering-v1",
        "status":"PASS" if stable and all(run["status"]=="PASS" for run in runs) else "FAIL",
        "same_workload_baseline":"UNMEASURED","same_workload_ci_cost":"UNMEASURED",
        "cache":"Fresh interpreter and SQLite each run. OS page cache uncontrolled.",
        "code_commit":commit,"src_config_sha256":fingerprint_tree(tree),
        "manifest_sha256":sha256((baseline/SOURCES).read_bytes()).hexdigest(),
        "bindings_sha256":sha256((tree/BINDINGS).read_bytes()).hexdigest(),
        "oracle_sha256":sha256((tree/ORACLE).read_bytes()).hexdigest(),
        "repeat_outputs_stable":stable,"runs":runs[:-1],"reached_code":profiled,
        **{"median_"+key:statistics.median(run[key] for run in runs[:-1]) for key in
           ("seconds","parent_peak_rss_kib","subprocess_peak_rss_kib")},
    }
    (out/"technical_engineering.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({key:report[key] for key in
                      ("status","median_seconds","median_parent_peak_rss_kib","median_subprocess_peak_rss_kib")}))


if __name__=="__main__":
    if sys.argv[1]=="child":
        tree,manifest,assets,out,trace=sys.argv[2:]
        child(*map(Path,(tree,manifest,assets,out)),trace)
    else:
        main()
