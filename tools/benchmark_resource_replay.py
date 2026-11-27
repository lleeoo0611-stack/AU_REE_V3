"""Compare one fixed Mt Weld source-to-selection path in fresh Python processes."""

import argparse
import ast
from collections import defaultdict
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import platform
import pstats
import subprocess
import sys
import tempfile

from v2_resource_fixture import RESULT_FIELDS, SCIENCE_REF, V2_ENTRY, extract_reference, fixture_config, v2_replay

BINDINGS = Path("config/migration/mt_weld_resource_binding_v1.json")
V3_ENTRY = "application.replay"


def v3_replay(bindings, assets, database, commit):
    from au_ree_v3.application import replay

    out = database.parent
    receipt = replay(bindings, assets, database, out, code_commit=commit)
    if receipt["status"] != "PASS":
        raise ValueError(receipt)
    rows = [json.loads(row)["values"] for row in (out / "v3-observations.jsonl").read_text().splitlines()]
    return {
        "identity": {key: rows[0][key] for key in RESULT_FIELDS},
        "ids": sorted(row["observation_id"] for row in rows),
        "values": {row["variable"]: str(Decimal(row["value"]).normalize()) for row in rows},
    }


def child(version, v2_root, bindings, assets, database, commit, profile):
    import cProfile
    import resource
    from time import perf_counter

    profiler = cProfile.Profile()
    config, binding = fixture_config(bindings)
    start = perf_counter()
    if profile:
        profiler.enable()
    if version == "v2":
        asset = assets / "assets" / binding["source"]["sha256"]
        result = v2_replay(v2_root, config, binding, asset, database)
    else:
        result = v3_replay(bindings, assets, database, commit)
    if profile:
        profiler.disable()
        profiler.dump_stats(str(profile))
    print(json.dumps({"result": result, "seconds": perf_counter() - start,
                      "peak_python_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                      "peak_child_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}))


def profile_metrics(path, source_root):
    from au_ree_v3.migration.audit import (
        DUPLICATE_MIN_NODES, branch_score, function_body_fingerprint,
    )

    def module(function):
        candidate = Path(function[0])
        if candidate.is_absolute() and candidate.is_relative_to(source_root):
            return candidate.relative_to(source_root).as_posix()
        return None

    modules, edges, calls = set(), set(), defaultdict(set)
    for function, info in pstats.Stats(str(path)).stats.items():
        target = module(function)
        if target is None:
            continue
        modules.add(target)
        calls[target].add((function[1], function[2]))
        edges.update((source, target) for caller in info[-1]
                     if (source := module(caller)) and source != target)
    scores, bodies = [], defaultdict(set)
    for file, locations in calls.items():
        for node in ast.walk(ast.parse((source_root / file).read_bytes())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start = min([node.lineno, *(decorator.lineno for decorator in node.decorator_list)])
            if (start, node.name) not in locations:
                continue
            scores.append(branch_score(node))
            fingerprint, size = function_body_fingerprint(node)
            if size >= DUPLICATE_MIN_NODES:
                bodies[fingerprint].add(file)
    scores.sort()
    return {
        "modules": sorted(modules), "edges": sorted(edges),
        "called_functions_with_ast": len(scores),
        "branch_score_p90": scores[(len(scores) * 9 - 1) // 10], "branch_score_max": scores[-1],
        "cross_module_duplicate_groups": sum(len(files) > 1 for files in bodies.values()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v2-root", type=Path, required=True)
    parser.add_argument("--v2-python", type=Path, required=True, help="V2 environment with pinned runtime dependencies")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    commit = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    bindings = root / BINDINGS
    config, binding = fixture_config(bindings)
    with tempfile.TemporaryDirectory(prefix="resource-benchmark-") as temp:
        temp = Path(temp)
        reference = temp / "reference"
        reference.mkdir()
        extract_reference(args.v2_root, reference, SCIENCE_REF, "src", "config", "data/claims.json")
        versions = {}
        for version, executable, source_root in (
            ("v2", args.v2_python.absolute(), reference / "src"),
            ("v3", Path(sys.executable), root / "src"),
        ):
            runs = []
            trace = temp / f"{version}.prof"
            for index in range(4):
                folder = temp / f"{version}-{index}"
                folder.mkdir()
                profile = trace if index == 3 else "-"
                run = subprocess.check_output([
                    str(executable), "-B", __file__, "_child", version, str(reference), str(bindings),
                    str(args.assets.resolve()), str(folder / "observations.sqlite"), commit, str(profile),
                ], env={**os.environ, "PYTHONPATH": str(source_root)}, text=True)
                runs.append(json.loads(run))
            if any(sample["result"] != runs[0]["result"] for sample in runs):
                raise ValueError(f"Inconsistent {version} benchmark output")
            versions[version] = {"samples": runs[:3], "dynamic_scope": profile_metrics(trace, source_root)}
        matched = versions["v2"]["samples"][0]["result"] == versions["v3"]["samples"][0]["result"]
        receipt = {
            "status": "PASS" if matched else "FAIL", "capability_id": "mt-weld-selected-resource-triple",
            "case_id": binding["case_id"], "source_sha256": binding["source"]["sha256"],
            "bindings_sha256": hashlib.sha256(bindings.read_bytes()).hexdigest(),
            "cutoff": config["research_cutoff"], "v2_commit": SCIENCE_REF, "v3_commit": commit,
            "v2_entry": V2_ENTRY, "v3_entry": V3_ENTRY,
            "platform": platform.platform(), "python": platform.python_version(),
            "cache": "fresh process and SQLite on each run, OS page cache uncontrolled, no network",
            "scope": "selected triple, IDs, project, source, statement, scope, basis and date",
            "difference_in_work": "V2 validates a frozen claims payload. V3 also extracts source tokens and writes evidence and Census.",
            "ci_cost_comparison": "UNMEASURED", "versions": versions,
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(receipt["status"])
    return 0 if matched else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["_child"]:
        _, _, version, ref, binding, assets, db, commit, profile = sys.argv
        child(version, Path(ref), Path(binding), Path(assets), Path(db), commit,
              None if profile == "-" else Path(profile))
    else:
        sys.exit(main())
