"""Measure same-input formal V3 build behavior using fresh subprocesses."""

import argparse
import ast
from collections import defaultdict
import cProfile
import csv
from hashlib import sha256
from importlib.util import resolve_name
import json
from math import ceil
import os
from pathlib import Path
import platform
import pstats
import resource
import shutil
import statistics
import subprocess
import sys
import tarfile
from time import perf_counter

BASELINE = "ac86ab43ec4671e177ce1c71556f88e1425fb633"
PROFILE = "config/research_profiles/au_ree_2026-11-28.json"
BINDINGS = "config/research_profiles/resource_bindings_v3.json"
SOURCES = "work/research-yield/source-inputs/source_manifest.json"
ASSETS = "work/fresh/source-store"


def digest(data):
    return sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def git(repository, *argv):
    return subprocess.run(["git", "-C", str(repository), *argv], capture_output=True, check=True).stdout


def fingerprint_tree(root):
    files = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for top in ("src", "config") for path in sorted((root / top).rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    return digest(json.dumps(files, sort_keys=True).encode())


def child(tree, manifest, assets, output, commit, trace):
    profiler = cProfile.Profile() if trace != "-" else None
    if profiler:
        profiler.enable()
    started = perf_counter()
    from au_ree_v3.fresh_research import build_research
    receipt = build_research(profile=tree/PROFILE, bindings=tree/BINDINGS, sources=manifest, assets=assets,
                             out=output, code_commit=commit)
    elapsed = perf_counter() - started
    if profiler:
        profiler.disable()
        profiler.dump_stats(trace)
    print(json.dumps({
        "seconds": elapsed, "parent_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "subprocess_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "gates": receipt["gates"],
    }))


def science(directory):
    """Use exact scientific values and IDs independent of release bookkeeping."""
    receipt = read(directory / "build_receipt.json")
    source_rows = [json.loads(line) for line in (directory / "observations/observations.jsonl").read_text().splitlines()]
    payload = read(directory / "decision/mcda_inputs.json")
    stage = read(directory / "decision/mcda_results.json")
    evidence_keys = (
        "project_id", "column", "value", "value_state", "source_sha256", "observation_id", "scope_id",
        "effective_date", "category", "reason", "raw_value", "raw_unit", "unit", "material_basis", "chemical_basis",
        "denominator_basis", "temporal_status", "temporal_decision_id",
    )
    observation_keys = ("observation_id", "project_id", "source_sha256", "value", "raw_value", "raw_unit", "unit",
                        "variable", "category", "scope_id", "statement_id", "page", "table", "row", "column",
                        "effective_date", "cutoff_grade", "cutoff_unit", "cutoff_analyte", "selected_for_census", "outcome")
    with (directory / "census/cell_status.csv").open(newline="", encoding="utf-8") as stream:
        evidence = [tuple(row[key] for key in evidence_keys) for row in csv.DictReader(stream)]
    criteria = {
        project: {
            code: (
                row["state"], row["reason"], row["accepted_value"], row["unit"], row["utility"],
                sorted((item["observation_id"], item["source_sha256"]) for item in row["lineage"])
                if row["state"] == "COMPARABLE" else [],
            ) for code, row in codes.items()
        } for project, codes in payload["criteria_by_project"].items()
    }
    return {
        "gates_F0_F3": receipt["gates"],
        "verified_source_count": receipt["verified_source_count"],
        "eligible_source_count": receipt["eligible_source_count"],
        "observation_counts": receipt["observation_counts"],
        "census_scientific_cell_count": receipt["scientific_output_count"],
        "formal_census_values_and_order_sha256": digest((directory / "census/census.csv").read_bytes()),
        "census_states_and_evidence_keys": evidence,
        "extracted_observation_values_and_evidence_keys": sorted(
            (tuple(row.get(key) for key in observation_keys) for row in source_rows), key=lambda item: item[0],
        ),
        "comparable_project_criteria": receipt["comparable_project_criteria"],
        "formal_comparison_values_and_evidence": criteria,
        "mcda_policy_sha256": payload["policy_sha256"],
        "complete_case_matrices_and_weights": {
            name: {key: values[key] for key in ("eligible_projects", "benefit_matrix", "scenario_weights")}
            for name, values in payload["panels"].items()
        },
        "scores_rankings_and_sensitivity": stage["panels"],
    }


def module_name(path):
    chunks = Path(path).with_suffix("").parts
    return ".".join(chunks[:-1] if chunks[-1] == "__init__" else chunks)


def static_imports(module, path, tree, scope):
    package = module if Path(path).name == "__init__.py" else module.rpartition(".")[0]
    possible = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            possible.update(alias.name for alias in node.names)
        if isinstance(node, ast.ImportFrom):
            target = node.module or ""
            if node.level:
                target = resolve_name("." * node.level + target, package)
            possible.add(target)
            possible.update(target + "." + alias.name for alias in node.names)
    return (possible & scope) - {module}


def metrics(trace, source):
    # Use the repository's existing branch and duplicate-body definitions.
    sys.path.insert(0, str(source))
    from au_ree_v3.migration.audit import DUPLICATE_MIN_NODES, branch_score, function_body_fingerprint

    def relative(address):
        path = Path(address[0])
        return path.relative_to(source).as_posix() if path.is_absolute() and path.is_relative_to(source) else None

    reach = defaultdict(set)
    edges = set()
    for target, info in pstats.Stats(str(trace)).stats.items():
        target_path = relative(target)
        if target_path is None:
            continue
        reach[target_path].add((target[1], target[2]))
        for caller in info[-1]:
            caller_path = relative(caller)
            if caller_path is not None and caller_path != target_path:
                edges.add((module_name(caller_path), module_name(target_path)))
    duplicates = defaultdict(list)
    scores = []
    static = set()
    reached_modules = {module_name(path) for path in reach}
    for path, lines in reach.items():
        tree = ast.parse((source / path).read_bytes())
        module = module_name(path)
        static.update((module, target) for target in static_imports(module, path, tree, reached_modules))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            line = min([node.lineno, *(dec.lineno for dec in node.decorator_list)])
            if (line, node.name) not in lines:
                continue
            scores.append((branch_score(node), module, node.name, line))
            signature, tokens = function_body_fingerprint(node)
            if tokens >= DUPLICATE_MIN_NODES:
                duplicates[signature].append((module, node.name, line))
    scores.sort()
    return {
        "reached_module_count": len(reached_modules),
        "reached_modules": sorted(reached_modules),
        "runtime_internal_edge_count": len(edges),
        "runtime_internal_edges": sorted(edges),
        "static_internal_import_edge_count_among_reached_modules": len(static),
        "static_internal_import_edges_among_reached_modules": sorted(static),
        "reached_ast_functions": len(scores),
        "branch_score_max": scores[-1][0],
        "branch_score_p90": scores[ceil(len(scores) * .9) - 1][0],
        "top_branch_scores": scores[-8:][::-1],
        "cross_module_repeated_AST_body_groups": sorted(
            sorted(group) for group in duplicates.values() if len({item[0] for item in group}) > 1
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-repo", type=Path, required=True)
    parser.add_argument("--current-repo", type=Path, required=True)
    parser.add_argument("--current-ref", help="Read a fixed target Git ref without switching the working tree")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    repository, current, out = (path.resolve() for path in (args.base_repo, args.current_repo, args.out))
    out.mkdir(parents=True, exist_ok=True)
    older, newer = out/"baseline-code", out/"current-code"
    def extract_git_tree(repo, ref, target):
        target.mkdir()
        archive = out/"tree.tar"
        archive.write_bytes(git(repo, "archive", "--format=tar", ref, "src", "config"))
        with tarfile.open(archive) as payload:
            payload.extractall(target, filter="data")
        archive.unlink()

    extract_git_tree(repository, BASELINE, older)
    if args.current_ref:
        extract_git_tree(current, args.current_ref, newer)
    else:
        for directory in ("src", "config"):
            shutil.copytree(current/directory, newer/directory, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    commits = BASELINE, git(current, "rev-parse", args.current_ref or "HEAD").decode().strip()
    trees = older, newer
    labels = "baseline", "current"
    versions = {name: {"code_commit": commit, "src_config_sha256": fingerprint_tree(tree), "runs": []}
                for name, commit, tree in zip(labels, commits, trees)}
    manifest, assets = repository/SOURCES, repository/ASSETS
    assert all(digest((tree/PROFILE).read_bytes()) == digest((older/PROFILE).read_bytes()) and
               digest((tree/BINDINGS).read_bytes()) == digest((older/BINDINGS).read_bytes()) for tree in trees)
    script = Path(__file__).resolve()
    samples = {label: [] for label in labels}
    for index in range(args.runs+1):
        for label, tree, commit in zip(labels, trees, commits):
            run = out/f"{label}-{index+1}"
            profiled = index == args.runs
            trace = out/f"{label}.prof" if profiled else "-"
            summary = json.loads(subprocess.check_output([
                sys.executable,"-B",str(script),"child",str(tree),str(manifest),str(assets),str(run),commit,str(trace),
            ], cwd=tree, env={**os.environ, "PYTHONPATH":str(tree/"src")}))
            samples[label].append((run, science(run)))
            if profiled:
                versions[label]["reached_code"] = metrics(trace, tree/"src")
            else:
                versions[label]["runs"].append(summary)
    expected = samples["baseline"][0][1]
    differences = {
        run.name: [key for key in expected if expected[key] != values[key]]
        for values in samples.values() for run, values in values
    }
    for version in versions.values():
        for field in ("seconds","parent_peak_rss_kib","subprocess_peak_rss_kib"):
            version["median_" + field] = statistics.median(item[field] for item in version["runs"])
    result = {
        "schema":"same-input-governed-research-engineering-v1",
        "status":"PASS" if all(not fields for fields in differences.values()) else "FAIL",
        "scope":"Real 144-SHA V3 source manifest to formal observations, 93×51 Census and governed MCDA under unchanged policy",
        "working_snapshot": bool(not args.current_ref and git(current, "status", "--porcelain", "--", "src", "config").strip()),
        "profile_sha256":digest((older/PROFILE).read_bytes()), "resource_bindings_sha256":digest((older/BINDINGS).read_bytes()),
        "source_manifest_sha256":digest(manifest.read_bytes()), "environment":read(samples["baseline"][0][0]/"build_receipt.json")["environment"],
        "platform":platform.platform(), "python":platform.python_version(),
        "timing_scope":"In a fresh interpreter, from before importing build modules to build receipt completion. Source readback and parser included; Git archive and setup excluded. No network.",
        "cache":"Fresh process, DocumentCache and SQLite each run; alternating baseline/current; OS page cache uncontrolled",
        "profiling":"One additional cProfile run each; runtime call edges and static imports among reached modules; AST compares reached named functions only",
        "ci_cost_same_input":"UNMEASURED", "differences":differences,
        "scientific_counts":{k:expected[k] for k in ("verified_source_count", "eligible_source_count", "observation_counts", "census_scientific_cell_count", "comparable_project_criteria")},
        "versions": versions,
    }
    (out/"engineering_comparison.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":result["status"],"differences":differences,"out":str(out)}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["child"]:
        tree,manifest,assets,output,commit,trace=sys.argv[2:]
        child(*map(Path,(tree,manifest,assets,output)),commit,trace)
    else:
        main()
