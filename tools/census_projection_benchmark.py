"""Benchmark the established Census path on one immutable live Store snapshot."""

import argparse
import ast
from collections import defaultdict
import cProfile
import csv
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tarfile
from time import perf_counter

from engineering_benchmark import PROFILE, digest, fingerprint_tree, git, metrics, module_name, read, static_imports

BASE = "40309ea006d2eaace3fe4bbf33beccca492261de"
SCIENCE_ARTIFACTS = ("census.csv", "cell_status.csv", "census_receipt.json")


def fixed_sources(directory: Path) -> dict[str, str]:
    with (directory / "census/cell_status.csv").open(encoding="utf-8", newline="") as stream:
        return {
            row["project_id"]: row["source_search_status"]
            for row in csv.DictReader(stream) if row["value_origin"] == "IDENTITY"
        }


def child(tree: Path, build: Path, target: Path, trace: str) -> None:
    output = target / "census"
    target.mkdir()
    # The benchmark opens a disposable copy of the fixed SQLite image.
    database = target / "research.sqlite"
    shutil.copyfile(build / "observations/research.sqlite", database)
    release_id = read(build / "build_receipt.json")["observation_release_id"]
    proofs = read(build / "observations/extraction.json")["trusted_temporal_evidence_ids"]
    statuses = fixed_sources(build)
    profiler = cProfile.Profile() if trace != "-" else None
    if profiler is not None:
        profiler.enable()
    start = perf_counter()
    from au_ree_v3.fresh_census import build_census
    from au_ree_v3.store import Store

    with Store(database, trusted_temporal_evidence_ids=proofs) as store:
        receipt = build_census(tree / PROFILE, output, store=store,
                               release_id=release_id, project_statuses=statuses)
    elapsed = perf_counter() - start
    if profiler is not None:
        profiler.disable()
        profiler.dump_stats(trace)
    print(json.dumps({
        "wall_seconds": elapsed,
        "python_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "child_peak_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
        "status": receipt["status"],
        "source_search_statuses_supplied": len(statuses),
        "cells": receipt["cells"],
        "formal_scientific_cells": receipt["formal_scientific_cells"],
        "artifacts_sha256": {name: digest((output / name).read_bytes()) for name in SCIENCE_ARTIFACTS},
    }))


def _extract(repository: Path, ref: str, tree: Path) -> None:
    tree.mkdir()
    tar = tree / "source.tar"
    tar.write_bytes(git(repository, "archive", "--format=tar", ref, "src", "config"))
    with tarfile.open(tar) as archive:
        archive.extractall(tree, filter="data")
    tar.unlink()


def _summarize(items: list[dict]) -> dict:
    dimensions = ("wall_seconds", "python_peak_rss_kib", "child_peak_rss_kib")
    return {f"median_{field}": statistics.median(item[field] for item in items) for field in dimensions}


def _static_review(tree: Path) -> dict:
    from au_ree_v3.migration.audit import DUPLICATE_MIN_NODES, branch_score, function_body_fingerprint

    root = tree / "src"
    names = {"fresh_census", "comparability", "normalization", "publication", "web_publication", "cli"}
    paths = sorted(
        path for path in (root / "au_ree_v3").glob("*.py")
        if path.stem in names or path.stem.startswith("census_")
    )
    modules = {module_name(path.relative_to(root)) for path in (root / "au_ree_v3").rglob("*.py")}
    files, bodies = {}, defaultdict(list)
    for path in paths:
        content = path.read_bytes()
        source = ast.parse(content)
        module = module_name(path.relative_to(root))
        functions = [item for item in ast.walk(source) if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
        scores = sorted((branch_score(item), item.name, item.lineno) for item in functions)
        for item in functions:
            key, size = function_body_fingerprint(item)
            if size >= DUPLICATE_MIN_NODES:
                bodies[key].append((module, item.name, item.lineno))
        files[module] = {
            "sha256": digest(content),
            "direct_internal_imports": sorted(static_imports(module, path, source, modules)),
            "named_ast_functions": len(functions),
            "top_branch_scores": scores[-5:][::-1],
        }
    return {
        "scope": "Named module source only, including new paths and functions not executed in same-scope timing",
        "reviewed_modules": files,
        "cross_module_repeated_AST_body_groups": sorted(
            sorted(group) for group in bodies.values() if len({item[0] for item in group}) > 1
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--build", required=True, type=Path, help="Baseline exact 144-SHA research build")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--ref", action="append", default=[], help="Additional committed comparison ref")
    parser.add_argument("--worktree", type=Path, help="Optional precommit input snapshot, labeled separately")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    repository, build, output = (path.resolve() for path in (args.repo, args.build, args.out))
    output.mkdir(parents=True, exist_ok=True)
    refs = tuple(dict.fromkeys(git(repository, "rev-parse", f"{ref}^{{commit}}").decode().strip()
                               for ref in (BASE, *args.ref)))
    data = {}
    for index, commit in enumerate(refs):
        label = f"v{index}-{commit[:7]}"
        tree = output / f"{label}-code"
        _extract(repository, commit, tree)
        data[label] = {
            "commit": commit, "source_kind": "GIT_COMMIT", "tree": tree,
            "input_code_sha256": fingerprint_tree(tree), "runs": [],
        }
    if args.worktree:
        tree = output / "working-snapshot-code"
        tree.mkdir()
        for component in ("src", "config"):
            shutil.copytree(args.worktree / component, tree / component,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        data["working-snapshot"] = {
            "commit": None, "base_commit": git(args.worktree, "rev-parse", "HEAD").decode().strip(),
            "source_kind": "WORKTREE_SNAPSHOT", "tree": tree,
            "input_code_sha256": fingerprint_tree(tree), "runs": [],
        }

    expected = {name: digest((build / "census" / name).read_bytes()) for name in SCIENCE_ARTIFACTS}
    differences = {}
    for index in range(args.runs + 1):
        ordered = list(data) if index % 2 == 0 else list(reversed(data))
        for label in ordered:
            version = data[label]
            tree = version["tree"]
            profiled = index == args.runs
            profile = str(output / f"{label}.prof") if profiled else "-"
            stage = output / f"{label}-{index+1}"
            result = json.loads(subprocess.check_output([
                sys.executable, "-B", str(Path(__file__).resolve()), "child",
                str(tree), str(build), str(stage), profile,
            ], cwd=tree, env={**os.environ, "PYTHONPATH": str(tree / "src")}))
            mismatch = [name for name, value in expected.items() if value != result["artifacts_sha256"].get(name)]
            if result["status"] != "PASS":
                mismatch.append("F3")
            differences[stage.name] = mismatch
            if profiled:
                version["reached_code"] = metrics(profile, tree / "src")
                version["static_source_review"] = _static_review(tree)
            else:
                version["runs"].append(result)
    for version in data.values():
        version.pop("tree")
        version.update(_summarize(version["runs"]))
    report = {
        "schema": "same-input-established-census-engineering-v1",
        "status": "PASS" if not any(differences.values()) else "FAIL",
        "fixed_store_sha256": digest((build / "observations/research.sqlite").read_bytes()),
        "fixed_build_receipt_sha256": digest((build / "build_receipt.json").read_bytes()),
        "fixed_source_manifest_sha256": read(build / "build_receipt.json")["input_artifacts"]["sources"],
        "fixed_artifacts_sha256": expected,
        "scope": "Same 93x51 established stand-alone Census, fixed Store, release, source search status and policy; "
                 "new metadata/derived exporters are a separate workload",
        "cache": "Fresh Python + SQLite copy per run, alternating ref order; OS page cache uncontrolled",
        "timing": "Module imports, Store open, Census computation, validated CSV outputs and receipt. "
                  "Excluded Git archive, copying the Store, and reading fixed benchmark parameters.",
        "matching_artifacts": list(SCIENCE_ARTIFACTS), "artifact_differences": differences,
        "ci_same_workload_cost": "UNMEASURED", "python": platform.python_version(),
        "platform": platform.platform(), "versions": data,
    }
    (output / "census_projection_engineering.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    print(json.dumps({"status": report["status"], "out": str(output), "differences": differences}))


if __name__ == "__main__":
    if sys.argv[1:2] == ["child"]:
        tree, build, target, trace = sys.argv[2:]
        child(Path(tree), Path(build), Path(target), trace)
    else:
        main()
