"""Replay the same exact resource fixture before and after temporal governance."""

import argparse
from io import BytesIO
import os
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tarfile
import tempfile

from benchmark_resource_replay import profile_metrics


RUN = """
from pathlib import Path
import cProfile
import json
import resource
import sys
from time import perf_counter
root, assets, out, commit, trace = sys.argv[1:]
out = Path(out)
profile = cProfile.Profile()
started = perf_counter()
if trace != "-":
    profile.enable()
from au_ree_v3.application import replay
receipt = replay(
    Path(root) / "config/migration/mt_weld_resource_binding_v1.json", Path(assets),
    out / "store.sqlite", out, code_commit=commit,
)
elapsed = perf_counter() - started
if trace != "-":
    profile.disable()
    profile.dump_stats(trace)
if receipt["status"] != "PASS":
    raise SystemExit(receipt)
rows = [json.loads(line) for line in (out / "v3-oracle-actual.jsonl").read_text().splitlines()]
scientific_values = {row["case_id"]: row["values"] for row in rows}
print(json.dumps({
    "elapsed_seconds": elapsed,
    "peak_python_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    "peak_parser_rss_kib": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    "scientific_values": scientific_values,
}))
"""


def measure(root, assets, folder, commit):
    samples = []
    trace = folder / "profile"
    folder.mkdir()
    for index in range(6):
        completed = subprocess.run(
            [sys.executable, "-B", "-c", RUN, str(root), str(assets), str(folder / str(index)), commit,
             str(trace) if index == 5 else "-"],
            cwd=folder, env={"PATH": os.defpath, "PYTHONPATH": str(root / "src")},
            text=True, capture_output=True,
        )
        if completed.returncode:
            raise ValueError(completed.stderr)
        samples.append(json.loads(completed.stdout))
    science = samples[0]["scientific_values"]
    if any(run.pop("scientific_values") != science for run in samples):
        raise ValueError("Independent runs disagree on selected scientific values")
    return {
        "commit": commit, "unprofiled_samples": samples[:5],
        "median_seconds": statistics.median(run["elapsed_seconds"] for run in samples[:5]),
        "profiled_internal_scope": profile_metrics(trace, root / "src"),
    }, science


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Fixed V3 Git commit before the change")
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    git = ["git", "-C", str(root)]
    base = subprocess.check_output([*git, "rev-parse", f"{args.base}^{{commit}}"], text=True).strip()
    current = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
    with tempfile.TemporaryDirectory(prefix="temporal-governance-") as sandbox:
        sandbox = Path(sandbox)
        before = sandbox / "before"
        before.mkdir()
        raw = subprocess.check_output([*git, "archive", base, "src", "config/migration/mt_weld_resource_binding_v1.json"])
        with tarfile.open(fileobj=BytesIO(raw)) as archive:
            archive.extractall(before, filter="data")
        old, old_values = measure(before, args.assets.resolve(), sandbox / "before-runs", base)
        new, new_values = measure(root, args.assets.resolve(), sandbox / "after-runs", current)
    receipt = {
        "status": "PASS" if old_values == new_values else "FAIL",
        "scope": "Mt Weld fixed-source resource replay to visible scientific values",
        "case_count": len(old_values), "python": platform.python_version(), "platform": platform.platform(),
        "cache": "Fresh SQLite and Python per run, OS page cache uncontrolled, no network",
        "before": old, "after": new,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(receipt, indent=2) + "\n")
    return int(receipt["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
