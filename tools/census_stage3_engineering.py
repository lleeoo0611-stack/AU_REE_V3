"""Benchmark frozen Census builds on equal inputs, with optional wider-scope results."""

import argparse
from collections import Counter
from contextlib import redirect_stdout
from hashlib import sha256
from io import BytesIO, StringIO
import json
import os
from pathlib import Path
import resource
import sqlite3
from statistics import median
import subprocess
import sys
import tarfile
from time import perf_counter
from unittest.mock import patch

import engineering_benchmark as engineering

BASE_PROFILE = Path("config/research_profiles/au_ree_census_attributes_v1.json")
BINDINGS = Path("config/research_profiles/resource_bindings_v4.json")
SAMPLE_FIELDS = ("seconds", "cpu_seconds", "parent_peak_rss_kib", "subprocess_peak_rss_kib")


def archive(repository, ref, output):
    output.mkdir(parents=True)
    content = engineering.git(repository, "archive", "--format=tar", ref, "src", "config")
    with tarfile.open(fileobj=BytesIO(content)) as archive_file:
        archive_file.extractall(output, filter="data")


def dependency_cycles(edges):
    graph = {node: {node} for pair in edges for node in pair}
    for caller, target in edges:
        graph[caller].add(target)
    for targets in graph.values():
        todo = set(targets)
        while todo:
            extra = graph[todo.pop()] - targets
            targets.update(extra)
            todo.update(extra)
    return sorted({tuple(sorted(other for other in targets if node in graph[other]))
                   for node, targets in graph.items()
                   if any(other != node and node in graph[other] for other in targets)})


def cpu_seconds():
    usage = (resource.getrusage(scope) for scope in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN))
    return sum(item.ru_utime + item.ru_stime for item in usage)


def run_build(code, profile, bindings, manifest, assets, out, revision, trace):
    engineering.PROFILE = profile
    engineering.BINDINGS = bindings
    start = cpu_seconds()
    capture = StringIO()
    with redirect_stdout(capture):
        engineering.child(code, manifest, assets, out, revision, trace)
    result = json.loads(capture.getvalue())
    return {**result, "cpu_seconds": cpu_seconds() - start}


def measure_cache(profile, bindings, manifest, assets, out, revision):
    from au_ree_v3 import documents, fresh_observations
    from au_ree_v3.fresh_research import build_research
    from au_ree_v3.sources import ContentStore

    raw_shas = {row["sha256"] for row in engineering.read(manifest)["editions"]}
    cache = documents.DocumentCache()
    pdf_sizes = []
    counts = Counter()
    original_pdf, original_connection, original_read = documents.pdf_pages, sqlite3.connect, ContentStore.read

    def parse_pdf(content, last_page):
        pages = original_pdf(content, last_page)
        pdf_sizes.append(len(pages))
        return pages

    def connect(*args, **kwargs):
        connection = original_connection(*args, **kwargs)
        connection.set_trace_callback(lambda sql: counts.update(("sql_statements",)))
        return connection

    def read_content(store, key):
        counts["raw_cas_reads" if key in raw_shas else "proof_cas_reads"] += 1
        return original_read(store, key)

    samples = []
    with (patch.object(documents, "pdf_pages", parse_pdf),
          patch.object(sqlite3, "connect", connect),
          patch.object(ContentStore, "read", read_content),
          patch.object(fresh_observations, "DocumentCache", lambda: cache)):
        for mode in ("initial_decode", "reused_resource_document_cache"):
            counts.clear()
            pdf_sizes.clear()
            start = perf_counter()
            receipt = build_research(profile=profile, bindings=bindings, sources=manifest, assets=assets,
                                     out=out / mode, code_commit=revision)
            samples.append({"mode": mode, "seconds": perf_counter() - start, **counts,
                            "pdf_decodes": len(pdf_sizes), "physical_pages": sum(pdf_sizes),
                            "artifacts": receipt["scientific_artifacts"]})
    if samples[0]["artifacts"] != samples[1]["artifacts"]:
        raise ValueError("Document cache reuse changed scientific bytes")
    return samples


def child(mode, code, profile, bindings, manifest, assets, output, revision, trace):
    if mode == "build":
        result = run_build(code, profile, bindings, manifest, assets, output, revision, trace)
    else:
        result = measure_cache(profile, bindings, manifest, assets, output, revision)
    print(json.dumps(result))


def launch(mode, code, profile, bindings, args, output, revision, trace="-"):
    invocation = [sys.executable, "-B", Path(__file__).resolve(), "_child", mode, code, profile, bindings,
                  args.manifest.resolve(), args.assets.resolve(), output, revision, trace]
    return json.loads(subprocess.check_output(list(map(str, invocation)), cwd=code,
                                             env={**os.environ, "PYTHONPATH": str(code / "src")}))


def safe_relative_path(relative):
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"Expected a repository-relative config path, got {relative}")
    return relative


def benchmark(args):
    output = args.out.resolve()
    output.mkdir()
    revisions = {label: engineering.git(args.repository, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
                 .decode().strip() for label, ref in (("base", args.base_ref), ("candidate", args.candidate_ref))}
    code = {label: output / f"code-{label}" for label in revisions}
    for label, revision in revisions.items():
        archive(args.repository, revision, code[label])
    profile, bindings = (code["base"] / safe_relative_path(path) for path in (args.base_profile, args.bindings))
    workloads = {label: (code[label], profile, bindings, revision) for label, revision in revisions.items()}
    if args.candidate_profile is not None:
        candidate_profile = code["candidate"] / safe_relative_path(args.candidate_profile)
        candidate_bindings = code["candidate"] / safe_relative_path(args.candidate_bindings or args.bindings)
        workloads["expanded_candidate"] = (code["candidate"], candidate_profile, candidate_bindings, revisions["candidate"])

    measures, science = {}, {}
    for label, (tree, profile, bindings, revision) in workloads.items():
        measures[label] = {"commit": revision, "code_tree_sha256": engineering.fingerprint_tree(tree),
                           "profile_sha256": engineering.digest(profile.read_bytes()),
                           "bindings_sha256": engineering.digest(bindings.read_bytes()), "runs": []}
    for position in range(args.runs + 1):
        for label, (tree, profile, bindings, revision) in workloads.items():
            destination = output / f"{label}-{position + 1}"
            profiled = position == args.runs
            trace = output / f"{label}.prof" if profiled else "-"
            sample = launch("build", tree, profile, bindings, args, destination, revision, trace)
            receipt = engineering.read(destination / "build_receipt.json")
            result = engineering.science(destination)
            if science.setdefault(label, result) != result:
                raise ValueError(f"{label} scientific values changed between repeats")
            item = measures[label]
            if item.setdefault("artifacts", receipt["scientific_artifacts"]) != receipt["scientific_artifacts"]:
                raise ValueError(f"{label} artifact bytes changed between repeats")
            if not profiled:
                item["runs"].append(sample)
                continue
            reached = engineering.metrics(trace, tree / "src")
            reached["static_cycles"] = dependency_cycles(reached["static_internal_import_edges_among_reached_modules"])
            reached["runtime_cycles"] = dependency_cycles(reached["runtime_internal_edges"])
            item.update(reached_code=reached, environment=receipt["environment"],
                        observations=receipt["observation_counts"], comparable_keys=receipt["comparable_project_criteria"],
                        census_states=engineering.read(destination / "census/census_receipt.json")["cell_states"])
    for label, (tree, profile, bindings, revision) in workloads.items():
        item = measures[label]
        item["median"] = {field: median(row[field] for row in item["runs"]) for field in SAMPLE_FIELDS}
        item["cache"] = launch("cache", tree, profile, bindings, args, output / f"cache-{label}", revision)

    baseline = science["base"]
    differences = {label: [field for field in baseline if baseline[field] != result[field]]
                   for label, result in science.items() if label != "base"}
    report = {
        "schema": "stage3-census-engineering-v1",
        "status": "FAIL" if differences["candidate"] else "PASS",
        "source_manifest_sha256": sha256(args.manifest.read_bytes()).hexdigest(),
        "scientific_difference_fields": differences,
        "measures": measures,
        "scope": "Base and candidate run exact same baseline profile and all its config pins. Expanded candidate measures a different profile.",
        "measurement_limits": "Fresh processes and alternating workloads; uncontrolled OS cache. Cache pair reuses only the resource observation cache."
                              " PDF telemetry covers native decoding, and SQLite and CAS checks repeat. CI billed cost is separate.",
    }
    (output / "engineering.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "scientific_difference_fields": differences, "out": str(output)}))
    if report["status"] == "FAIL":
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--candidate-ref", required=True)
    parser.add_argument("--base-profile", type=Path, default=BASE_PROFILE)
    parser.add_argument("--bindings", type=Path, default=BINDINGS)
    parser.add_argument("--candidate-profile", type=Path)
    parser.add_argument("--candidate-bindings", type=Path)
    for name in ("manifest", "assets", "out"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be at least one")
    benchmark(args)


if __name__ == "__main__":
    if sys.argv[1:2] == ["_child"]:
        mode, code, profile, bindings, manifest, assets, output, revision, trace = sys.argv[2:]
        child(mode, *map(Path, (code, profile, bindings, manifest, assets, output)), revision, trace)
    else:
        main()
